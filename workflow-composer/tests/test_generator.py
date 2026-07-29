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

    def test_non_divisible_ind_jobs_allowed(self):
        """Non-divisible row counts are now allowed.

        The worker scripts handle partial ranges correctly via min(stop, total).
        This test verifies that the generator accepts non-divisible counts.
        """
        # 250000 % 7 != 0, but this should now be allowed
        result = validate_ind_jobs(7, 250000, "test.vcf")
        assert result == 7  # Should not raise, just return the value

    def test_invalid_row_count_rejected(self):
        """Row count must be positive."""
        with pytest.raises(ValueError, match="Row count must be positive"):
            validate_ind_jobs(10, 0, "test.vcf")

        with pytest.raises(ValueError, match="Row count must be positive"):
            validate_ind_jobs(10, -5, "test.vcf")

    def test_invalid_ind_jobs_rejected(self):
        """ind_jobs must be positive."""
        with pytest.raises(ValueError, match="ind_jobs must be positive"):
            validate_ind_jobs(0, 100, "test.vcf")

        with pytest.raises(ValueError, match="ind_jobs must be positive"):
            validate_ind_jobs(-5, 100, "test.vcf")

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

    def test_file_count(self):
        """Verify file count for default configuration."""
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


class TestRemainderHandling:
    """Test that non-divisible row counts are handled correctly."""

    def test_remainder_creates_extra_task(self):
        """Non-divisible row counts are absorbed into ind_jobs tasks, not an extra one."""
        from workflow_composer.core.generator import HyperFlowGenerator, ChromosomeData

        # 2487 rows with ind_jobs=10 → step=ceil(2487/10)=249, so 10 tasks cover
        # the file and the last one simply stops early at `total`. Rounding the
        # step down instead would emit an 11th task for the few trailing rows,
        # and that task still scans the input up to its offset.
        chromosomes = [
            ChromosomeData(
                vcf_file="ALL.chr6.test.vcf",
                row_count=2487,
                annotation_file="ALL.chr6.test.annotation.vcf",
                chromosome="6"
            )
        ]

        generator = HyperFlowGenerator()
        workflow = generator.generate(
            chromosomes=chromosomes,
            populations=["EUR"],
            ind_jobs=10,
            name="test"
        )

        # Count individuals tasks
        ind_tasks = [p for p in workflow["processes"] if p["name"] == "individuals"]
        assert len(ind_tasks) == 10, "Should have exactly ind_jobs tasks, no remainder task"

        # Args are: [vcf_file, chromosome, start, stop, total]
        ranges = [
            (int(p["config"]["executor"]["args"][2]), int(p["config"]["executor"]["args"][3]))
            for p in ind_tasks
        ]
        total = int(ind_tasks[-1]["config"]["executor"]["args"][4])
        assert total == 2487, "Total should be 2487"

        # Ranges are contiguous and every task has rows to process
        assert ranges[0][0] == 1, "First task should start at 1"
        for (_, prev_stop), (start, _) in zip(ranges, ranges[1:]):
            assert start == prev_stop, f"Gap or overlap at {prev_stop} -> {start}"
        for start, stop in ranges:
            assert min(stop, total) > start, f"Task [{start}, {stop}) processes no rows"

        # The last task runs to the end of the file
        assert ranges[-1][1] >= total, "Last task should cover through total"

    def test_exact_division_no_extra_task(self):
        """Exactly divisible row counts should not create extra tasks."""
        from workflow_composer.core.generator import HyperFlowGenerator, ChromosomeData

        # 2480 rows with ind_jobs=10 → step=248, no remainder
        chromosomes = [
            ChromosomeData(
                vcf_file="ALL.chr6.test.vcf",
                row_count=2480,
                annotation_file="ALL.chr6.test.annotation.vcf",
                chromosome="6"
            )
        ]

        generator = HyperFlowGenerator()
        workflow = generator.generate(
            chromosomes=chromosomes,
            populations=["EUR"],
            ind_jobs=10,
            name="test"
        )

        ind_tasks = [p for p in workflow["processes"] if p["name"] == "individuals"]
        assert len(ind_tasks) == 10, "Should have exactly 10 individuals tasks"


class TestVariantEstimation:
    """Test variant count estimation functions."""

    def test_full_chromosome_estimate(self):
        """Full chromosome should return known count."""
        from workflow_composer.core.data_resolver import estimate_variant_count, CHROMOSOME_VARIANT_COUNT

        est = estimate_variant_count(chromosome="6")
        assert est == CHROMOSOME_VARIANT_COUNT["6"]

    def test_region_estimate_includes_safety_margin(self):
        """Region estimates should include safety margin."""
        from workflow_composer.core.data_resolver import (
            estimate_variant_count,
            CHROMOSOME_VARIANT_COUNT,
            CHROMOSOME_LENGTH_BP
        )
        from workflow_composer.core.models import GenomicRegion

        # Create a region that is 1% of chromosome
        chrom = "22"
        chrom_length = CHROMOSOME_LENGTH_BP[chrom]
        region_size = chrom_length // 100  # 1%

        region = GenomicRegion(
            name="test",
            chromosome=chrom,
            start=1000000,
            end=1000000 + region_size,
            context="test"
        )

        est = estimate_variant_count(region=region)

        # Base estimate would be ~1% of chromosome variants
        base_estimate = CHROMOSOME_VARIANT_COUNT[chrom] // 100

        # Should be higher due to safety margin (default 1.2)
        assert est > base_estimate, "Estimate should include safety margin"
        assert est <= base_estimate * 1.5, "Safety margin should be reasonable"

    def test_compute_optimal_ind_jobs(self):
        """Optimal ind_jobs should find clean divisors when possible."""
        from workflow_composer.core.data_resolver import compute_optimal_ind_jobs

        # Exact divisor available
        optimal = compute_optimal_ind_jobs(250000, target=100)
        assert 250000 % optimal == 0, "Should find exact divisor when available"

        # No exact divisor, should return close to target
        optimal = compute_optimal_ind_jobs(2487, target=50)
        assert 1 <= optimal <= 2487


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
