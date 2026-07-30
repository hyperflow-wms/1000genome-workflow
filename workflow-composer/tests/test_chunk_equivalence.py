"""
Output-equivalence tests for the ind_jobs clamp in generate_workflow
(RFC-003 section 7 item 4).

Clamping can change chunk boundaries whenever it binds, so a per-chunk
byte-for-byte comparison against a fixed baseline workflow is not available
in general. Instead:

- ``TestBoundaryMoveEquivalence`` proves the *union* invariant: for every
  ind_jobs in 1..40 against the HLA threshold (166052), the emitted
  [counter, stop) ranges -- after the clamp has had a chance to bind -- are
  contiguous, start at 1, cover every row index in [1, 166052) exactly once
  with no gap and no overlap, number exactly the effective (post-clamp)
  ind_jobs, and the individuals_merge args are exactly the individuals
  output filenames in order. A moved boundary therefore cannot orphan or
  duplicate a chunk.

- ``test_individuals_output_is_a_pure_function_of_its_args`` proves a
  chunk's *content* is a pure function of its (counter, stop) args, so
  re-chunking (moving where boundaries fall) is safe: it runs
  ``individuals.py`` with the exact args recorded in the preserved HLA
  baseline workflow and diffs the extracted output tree against the
  preserved baseline tarball's extracted tree. Per the harness-level rule
  (never diff .tar.gz bytes -- gzip/tar embed timestamps), the comparison
  extracts both archives first.

No claim is made here about mutation_overlap or frequency output, which are
unseeded (random.sample() with no seed) and not byte-comparable.
"""
from __future__ import annotations

import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from workflow_composer.core.generator import (
    BUNDLED_POPULATIONS_DIR,
    ChromosomeData,
    HyperFlowGenerator,
)
from workflow_composer.core.environment import ComputeEnvironment

REPO_ROOT = Path(__file__).parent.parent.parent
BASELINE_DIR = REPO_ROOT / "tests" / "integration" / "workflow-eur-afr-hla-baseline"
INDIVIDUALS_SCRIPT = REPO_ROOT / "worker-base-image" / "scripts" / "individuals.py"

HLA_ROW_COUNT = 166052
HLA_INDIVIDUALS = 1153
HLA_C_NUM = "6"

pytestmark = pytest.mark.skipif(
    not BASELINE_DIR.exists(), reason="tests/integration/workflow-eur-afr-hla-baseline not available"
)


# ---------------------------------------------------------------------------
# Criterion 3: boundary-move equivalence by union comparison
# ---------------------------------------------------------------------------

def _generate_hla_only(ind_jobs_hint: int) -> dict:
    """Generate a single-chromosome (HLA, chr6) workflow with the clamp
    active, so the emitted ranges reflect whatever ind_jobs the clamp
    actually settled on for this hint -- exactly as generate_workflow would
    produce for a real HLA plan.
    """
    chromosomes = [
        ChromosomeData(
            vcf_file="ALL.chr6.hla.vcf",
            row_count=HLA_ROW_COUNT,
            annotation_file="ALL.chr6.hla.annotation.vcf",
            chromosome=HLA_C_NUM,
        )
    ]
    env = ComputeEnvironment.resolve("local")
    generator = HyperFlowGenerator()
    return generator.generate(
        chromosomes=chromosomes,
        populations=["EUR"],
        ind_jobs=ind_jobs_hint,
        individuals=HLA_INDIVIDUALS,
        compute_environment=env,
    )


class TestBoundaryMoveEquivalence:
    """Section 7 item 4 acceptance criterion 3: the union of emitted chunks
    covers [1, threshold) exactly once, however the clamp moved the
    boundaries."""

    @pytest.mark.parametrize("ind_jobs_hint", list(range(1, 41)))
    def test_ranges_are_contiguous_and_cover_every_row_exactly_once(self, ind_jobs_hint):
        wf = _generate_hla_only(ind_jobs_hint)

        ind_tasks = [p for p in wf["processes"] if p["name"] == "individuals"]
        effective_ind_jobs = wf["metadata"]["parallelism"][0]["ind_jobs"]

        assert len(ind_tasks) == effective_ind_jobs, (
            f"hint={ind_jobs_hint}: emitted {len(ind_tasks)} individuals tasks, "
            f"metadata reports effective ind_jobs={effective_ind_jobs}"
        )

        ranges = [
            (int(p["config"]["executor"]["args"][2]), int(p["config"]["executor"]["args"][3]))
            for p in ind_tasks
        ]

        # Ranges are emitted in ascending start order already; clip stop to
        # threshold the same way the worker script does (min(stop, total)),
        # since the last task's raw `stop` can run past the end of the file.
        clipped = [(start, min(stop, HLA_ROW_COUNT)) for start, stop in ranges]

        assert clipped[0][0] == 1, f"hint={ind_jobs_hint}: first range must start at 1"
        for (_, prev_stop), (start, _) in zip(clipped, clipped[1:]):
            assert start == prev_stop, (
                f"hint={ind_jobs_hint}: gap or overlap between ranges at {prev_stop} -> {start}"
            )
        assert clipped[-1][1] == HLA_ROW_COUNT, (
            f"hint={ind_jobs_hint}: last range must reach threshold {HLA_ROW_COUNT}, "
            f"got {clipped[-1][1]}"
        )
        for start, stop in clipped:
            assert stop > start, f"hint={ind_jobs_hint}: empty range [{start}, {stop})"

        # No index in [1, threshold) is covered by more than one range: the
        # contiguity check above already rules out overlap between adjacent
        # ranges, but confirm no chunk has zero rows (already checked) and
        # that consecutive starts strictly increase (rules out a
        # zero-or-negative step producing a duplicate range).
        starts = [s for s, _ in clipped]
        assert starts == sorted(set(starts)), (
            f"hint={ind_jobs_hint}: duplicate or out-of-order range starts {starts}"
        )

    @pytest.mark.parametrize("ind_jobs_hint", [1, 5, 15, 16, 20, 40])
    def test_merge_args_are_exactly_the_individuals_output_filenames_in_order(self, ind_jobs_hint):
        wf = _generate_hla_only(ind_jobs_hint)

        ind_tasks = [p for p in wf["processes"] if p["name"] == "individuals"]
        merge_tasks = [p for p in wf["processes"] if p["name"] == "individuals_merge"]
        assert len(merge_tasks) == 1

        individuals_output_names = []
        signal_by_id = {i: s["name"] for i, s in enumerate(wf["signals"])}
        for p in ind_tasks:
            out_signal_id = p["outs"][0]
            individuals_output_names.append(signal_by_id[out_signal_id])

        merge_args = merge_tasks[0]["config"]["executor"]["args"]
        assert merge_args[0] == HLA_C_NUM
        assert merge_args[1:] == individuals_output_names, (
            f"hint={ind_jobs_hint}: individuals_merge args do not match the "
            f"individuals task output filenames in order -- a moved boundary "
            f"orphaned or duplicated a chunk"
        )


# ---------------------------------------------------------------------------
# Criterion 4: per-chunk output equivalence (content is a pure function of args)
# ---------------------------------------------------------------------------

def _extract(tar_path: Path, dest: Path) -> dict[str, bytes]:
    """Extract a tarball and return {filename: contents} for every regular
    file inside, keyed by basename (the archive has no meaningful directory
    structure -- see individuals.py's compress())."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:gz") as tf:
        tf.extractall(dest, filter="data")
    return {
        p.name: p.read_bytes()
        for p in dest.rglob("*")
        if p.is_file()
    }


@pytest.mark.skipif(
    not (BASELINE_DIR / "ALL.chr6.hla.vcf").exists() or not (BASELINE_DIR / "columns.txt").exists(),
    reason="baseline ALL.chr6.hla.vcf / columns.txt not available",
)
@pytest.mark.skipif(not INDIVIDUALS_SCRIPT.exists(), reason="individuals.py not available")
def test_individuals_output_is_a_pure_function_of_its_args(tmp_path):
    """Re-run the exact chunk recorded in the preserved baseline workflow
    (chr6, [1, 16606) of 166052) against a fresh copy of the same input, and
    prove the *extracted* output tree is identical to the preserved
    baseline's extracted tree. This is what makes re-chunking (a clamp
    moving where the [counter, stop) boundaries fall) safe: each chunk's
    content depends only on its own args, not on which other chunks exist.

    Never compares .tar.gz bytes directly -- gzip/tar embed timestamps, so
    archives with identical contents differ (verified: 3596362 vs 3604677
    bytes here for identical extracted content).
    """
    baseline_vcf = BASELINE_DIR / "ALL.chr6.hla.vcf"
    baseline_columns = BASELINE_DIR / "columns.txt"
    baseline_tarball = BASELINE_DIR / "chr6n-1-16606.tar.gz"
    assert baseline_tarball.exists(), "preserved baseline chr6n-1-16606.tar.gz missing"

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    import shutil
    shutil.copy2(baseline_vcf, work_dir / "ALL.chr6.hla.vcf")
    shutil.copy2(baseline_columns, work_dir / "columns.txt")

    # Exact args recorded in tests/integration/workflow-eur-afr-hla-baseline/workflow.json
    # for the first individuals task on chr6.
    args = ["ALL.chr6.hla.vcf", "6", "1", "16606", "166052"]
    result = subprocess.run(
        [sys.executable, str(INDIVIDUALS_SCRIPT), *args],
        cwd=work_dir,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"individuals.py failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )

    fresh_tarball = work_dir / "chr6n-1-16606.tar.gz"
    assert fresh_tarball.exists(), f"expected output not produced: {fresh_tarball}"

    fresh_files = _extract(fresh_tarball, tmp_path / "fresh_extract")
    baseline_files = _extract(baseline_tarball, tmp_path / "baseline_extract")

    assert len(baseline_files) == 1153, f"expected 1153 individuals, got {len(baseline_files)}"
    assert set(fresh_files) == set(baseline_files), (
        "extracted filenames differ: "
        f"only in fresh={set(fresh_files) - set(baseline_files)}, "
        f"only in baseline={set(baseline_files) - set(fresh_files)}"
    )
    for name in baseline_files:
        assert fresh_files[name] == baseline_files[name], f"content differs for {name}"

    # The .tar.gz bytes themselves are NOT expected to match (gzip/tar embed
    # timestamps), and are deliberately not compared here -- only the
    # extracted trees above are.


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
