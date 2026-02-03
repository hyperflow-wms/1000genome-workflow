"""
Test native generator against daxgen.py output.

This is the most critical test — ensures functional equivalence.
See "Known Differences" section in implementation plan for acceptable variations.
"""
import json
import subprocess
import tempfile
from pathlib import Path
import pytest
import sys

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from workflow_composer.core.generator import (
    generate_workflow,
    HyperFlowGenerator,
    parse_chromosome_number,
    load_data_csv,
    load_populations,
    validate_ind_jobs
)

# Path to original workflow-generator for comparison
WORKFLOW_GENERATOR_PATH = Path(__file__).parent.parent.parent / "workflow-generator"


class TestChromosomeParsing:
    """Test chromosome number extraction."""

    def test_parse_single_digit(self):
        assert parse_chromosome_number("ALL.chr1.250000.vcf") == "1"

    def test_parse_double_digit(self):
        assert parse_chromosome_number("ALL.chr10.250000.vcf") == "10"

    def test_parse_x_chromosome(self):
        assert parse_chromosome_number("ALL.chrX.something.vcf") == "X"


class TestDataLoading:
    """Test data loading functions."""

    def test_load_data_csv(self):
        if not (WORKFLOW_GENERATOR_PATH / "data.csv").exists():
            pytest.skip("data.csv not available")

        chromosomes = load_data_csv(WORKFLOW_GENERATOR_PATH / "data.csv")
        assert len(chromosomes) == 10
        assert all(c.row_count == 250000 for c in chromosomes)

    def test_load_populations(self):
        if not (WORKFLOW_GENERATOR_PATH / "data" / "populations").exists():
            pytest.skip("populations directory not available")

        populations = load_populations(WORKFLOW_GENERATOR_PATH / "data" / "populations")
        assert len(populations) == 7
        # Should be sorted alphabetically
        assert populations == sorted(populations)
        assert "AFR" in populations
        assert "EUR" in populations


class TestIndJobsValidation:
    """Test ind_jobs parameter validation."""

    def test_valid_ind_jobs(self):
        # 250000 % 250 == 0
        result = validate_ind_jobs(250, 250000, "test.vcf")
        assert result == 250

    def test_invalid_ind_jobs(self):
        # 250000 % 7 != 0
        with pytest.raises(ValueError, match="does not divide"):
            validate_ind_jobs(7, 250000, "test.vcf")

    def test_ind_jobs_capped_to_threshold(self):
        # If ind_jobs > threshold, should be capped
        result = validate_ind_jobs(1000, 100, "test.vcf")
        assert result == 100


class TestSignalDeduplication:
    """Test signal ID handling."""

    def test_no_duplicate_signals(self):
        """Verify native generator doesn't create duplicate signals."""
        if not (WORKFLOW_GENERATOR_PATH / "data.csv").exists():
            pytest.skip("workflow-generator data not available")

        wf = generate_workflow(
            data_csv=WORKFLOW_GENERATOR_PATH / "data.csv",
            populations_dir=WORKFLOW_GENERATOR_PATH / "data" / "populations",
            ind_jobs=5
        )

        signal_names = [s["name"] for s in wf["signals"]]
        unique_names = set(signal_names)

        assert len(signal_names) == len(unique_names), \
            f"Found {len(signal_names) - len(unique_names)} duplicate signals"

    def test_signal_id_zero_not_duplicated(self):
        """Specifically test that signal ID 0 is handled correctly.

        This is the exact bug that exists in hflow-convert-dax:
        - First signal gets ID 0
        - Check `if (!dataNames[dataName])` fails because !0 === true
        - Creates duplicate signal

        Our generator must NOT have this bug.
        """
        generator = HyperFlowGenerator()

        # Create first signal (gets ID 0)
        id1 = generator._get_or_create_signal("first_file.txt")
        assert id1 == 0, "First signal should have ID 0"

        # Request same signal again
        id2 = generator._get_or_create_signal("first_file.txt")
        assert id2 == 0, "Same file should return same ID"

        # Should still only have one signal
        assert len(generator.signals) == 1, \
            f"Expected 1 signal, got {len(generator.signals)}"


class TestTaskCounts:
    """Test task count calculations."""

    def test_task_count_formula(self):
        """Verify task count matches expected formula."""
        if not (WORKFLOW_GENERATOR_PATH / "data.csv").exists():
            pytest.skip("workflow-generator data not available")

        wf = generate_workflow(
            data_csv=WORKFLOW_GENERATOR_PATH / "data.csv",
            populations_dir=WORKFLOW_GENERATOR_PATH / "data" / "populations",
            ind_jobs=250
        )

        # C=10 chromosomes, P=7 populations, J=250 ind_jobs
        # Expected: C × (J + 2 + 2P) = 10 × (250 + 2 + 14) = 2660
        expected_tasks = 10 * (250 + 2 + 2*7)
        assert len(wf["processes"]) == expected_tasks, \
            f"Expected {expected_tasks} tasks, got {len(wf['processes'])}"

    def test_task_count_small(self):
        """Test task count with smaller ind_jobs."""
        if not (WORKFLOW_GENERATOR_PATH / "data.csv").exists():
            pytest.skip("workflow-generator data not available")

        wf = generate_workflow(
            data_csv=WORKFLOW_GENERATOR_PATH / "data.csv",
            populations_dir=WORKFLOW_GENERATOR_PATH / "data" / "populations",
            ind_jobs=5
        )

        # C=10, P=7, J=5
        # Expected: 10 × (5 + 2 + 14) = 210
        expected_tasks = 10 * (5 + 2 + 2*7)
        assert len(wf["processes"]) == expected_tasks

    def test_signal_count(self):
        """Verify signal count for default configuration."""
        if not (WORKFLOW_GENERATOR_PATH / "data.csv").exists():
            pytest.skip("workflow-generator data not available")

        wf = generate_workflow(
            data_csv=WORKFLOW_GENERATOR_PATH / "data.csv",
            populations_dir=WORKFLOW_GENERATOR_PATH / "data" / "populations",
            ind_jobs=250
        )

        # Expected: 2688 unique signals
        # (Note: daxgen.py + hflow-convert-dax produces 2689 due to duplication bug)
        assert len(wf["signals"]) == 2688, \
            f"Expected 2688 signals, got {len(wf['signals'])}"


class TestWorkflowStructure:
    """Test workflow structure and dependencies."""

    def test_frequency_parallel_with_mutation_overlap(self):
        """Verify frequency and mutation_overlap have same inputs (run in parallel)."""
        if not (WORKFLOW_GENERATOR_PATH / "data.csv").exists():
            pytest.skip("workflow-generator data not available")

        wf = generate_workflow(
            data_csv=WORKFLOW_GENERATOR_PATH / "data.csv",
            populations_dir=WORKFLOW_GENERATOR_PATH / "data" / "populations",
            ind_jobs=5
        )

        # Find pairs of mutation_overlap and frequency for same chromosome/population
        mut_processes = [p for p in wf["processes"] if p["name"] == "mutation_overlap"]
        freq_processes = [p for p in wf["processes"] if p["name"] == "frequency"]

        # Both should have same count
        assert len(mut_processes) == len(freq_processes)

        # For each pair with matching args, inputs should be identical
        for mut_p in mut_processes:
            mut_args = mut_p["config"]["executor"]["args"]
            # Find corresponding frequency process
            for freq_p in freq_processes:
                freq_args = freq_p["config"]["executor"]["args"]
                if mut_args == freq_args:  # Same chromosome and population
                    assert mut_p["ins"] == freq_p["ins"], \
                        f"mutation_overlap and frequency should have same inputs for {mut_args}"
                    break

    def test_workflow_has_required_fields(self):
        """Test that workflow has all required HyperFlow fields."""
        if not (WORKFLOW_GENERATOR_PATH / "data.csv").exists():
            pytest.skip("workflow-generator data not available")

        wf = generate_workflow(
            data_csv=WORKFLOW_GENERATOR_PATH / "data.csv",
            populations_dir=WORKFLOW_GENERATOR_PATH / "data" / "populations",
            ind_jobs=5
        )

        assert "name" in wf
        assert "version" in wf
        assert "processes" in wf
        assert "signals" in wf
        assert "ins" in wf
        assert "outs" in wf

    def test_process_structure(self):
        """Test that processes have required fields."""
        if not (WORKFLOW_GENERATOR_PATH / "data.csv").exists():
            pytest.skip("workflow-generator data not available")

        wf = generate_workflow(
            data_csv=WORKFLOW_GENERATOR_PATH / "data.csv",
            populations_dir=WORKFLOW_GENERATOR_PATH / "data" / "populations",
            ind_jobs=5
        )

        for proc in wf["processes"]:
            assert "name" in proc
            assert "function" in proc
            assert "type" in proc
            assert "firingLimit" in proc
            assert "ins" in proc
            assert "outs" in proc
            assert "config" in proc
            assert "executor" in proc["config"]
            assert "executable" in proc["config"]["executor"]
            assert "args" in proc["config"]["executor"]


class TestDaxgenComparison:
    """Compare native generator output with daxgen.py output."""

    @pytest.fixture
    def daxgen_workflow(self):
        """Generate workflow using original daxgen.py pipeline."""
        if not (WORKFLOW_GENERATOR_PATH / "daxgen.py").exists():
            pytest.skip("daxgen.py not available")

        # Check if hflow-convert-dax is available
        try:
            subprocess.run(["which", "hflow-convert-dax"], check=True, capture_output=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            pytest.skip("hflow-convert-dax not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            # Run daxgen.py
            subprocess.run(
                ["python3", "daxgen.py", "--ind-jobs", "5",
                 "--dax", f"{tmpdir}/test.dax"],
                cwd=WORKFLOW_GENERATOR_PATH,
                check=True
            )

            # Convert with hflow-convert-dax
            result = subprocess.run(
                ["hflow-convert-dax", f"{tmpdir}/test.dax"],
                capture_output=True,
                text=True,
                check=True
            )

            return json.loads(result.stdout)

    def test_process_count_matches(self, daxgen_workflow):
        """Native generator should produce same number of processes."""
        native_wf = generate_workflow(
            data_csv=WORKFLOW_GENERATOR_PATH / "data.csv",
            populations_dir=WORKFLOW_GENERATOR_PATH / "data" / "populations",
            ind_jobs=5
        )

        assert len(native_wf["processes"]) == len(daxgen_workflow["processes"]), \
            f"Process count mismatch: native={len(native_wf['processes'])}, daxgen={len(daxgen_workflow['processes'])}"

    def test_unique_signal_names_match(self, daxgen_workflow):
        """Native generator should produce same unique signal names."""
        native_wf = generate_workflow(
            data_csv=WORKFLOW_GENERATOR_PATH / "data.csv",
            populations_dir=WORKFLOW_GENERATOR_PATH / "data" / "populations",
            ind_jobs=5
        )

        native_names = sorted(set(s["name"] for s in native_wf["signals"]))
        daxgen_names = sorted(set(s["name"] for s in daxgen_workflow["signals"]))

        assert native_names == daxgen_names, \
            "Signal names differ between native and daxgen"

    def test_process_definitions_match(self, daxgen_workflow):
        """Native generator should produce equivalent process definitions."""
        native_wf = generate_workflow(
            data_csv=WORKFLOW_GENERATOR_PATH / "data.csv",
            populations_dir=WORKFLOW_GENERATOR_PATH / "data" / "populations",
            ind_jobs=5
        )

        def normalize_processes(processes):
            """Normalize for comparison (sort by args)."""
            return sorted(
                [
                    {
                        "name": p["name"],
                        "executable": p["config"]["executor"]["executable"],
                        "args": p["config"]["executor"]["args"]
                    }
                    for p in processes
                ],
                key=lambda p: (p["name"], json.dumps(p["args"]))
            )

        native_normalized = normalize_processes(native_wf["processes"])
        daxgen_normalized = normalize_processes(daxgen_workflow["processes"])

        assert native_normalized == daxgen_normalized, \
            "Process definitions differ"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
