"""
Tests for RFC-004 section 4.6: resolution parity and correct individual
counts.

Two acceptance criteria:

1. For one fixed (variants, individuals, ComputeEnvironment), the HyperFlow
   and Nextflow backends receive an identical `Parallelism` -- same
   `ind_jobs`, `max_parallelism` and `est_peak_mb`. Neither backend may
   re-derive or adjust what `recommend_parallelism` decided.
2. `columns.txt` for a single-population intent carries that population's
   sample count, not the full bundled cohort.

No Docker, network, or FTP download: both fixtures below are synthetic,
built directly in `tmp_path` from the bundled population files under
`src/workflow_composer/data/populations/`.
"""
from __future__ import annotations

from pathlib import Path

from workflow_composer.backends.hyperflow.generator import (
    BUNDLED_POPULATIONS_DIR,
    clamp_ind_jobs,
    generate_workflow,
)
from workflow_composer.backends.nextflow.params import (
    NextflowBackend,
    build_command,
    intent_to_params,
)
from workflow_composer.core.environment import ComputeEnvironment
from workflow_composer.core.models import ResearchIntent
from workflow_composer.core.parallelism import Parallelism, recommend_parallelism

# A fixed, self-contained ComputeEnvironment -- deliberately not
# ComputeEnvironment.resolve("local"), so the test cannot be perturbed by
# G1KWF_* environment variables set in the ambient shell.
ENV = ComputeEnvironment(
    name="test",
    vcpus=8,
    host_mem_mb=16384,
    engine_reserve=1,
    host_reserve_mb=2048,
    mem_budget_mb=512,
)

VARIANTS = 200_000
INDIVIDUALS = 1_675

_VCF_FIXED_FIELDS = ["#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT"]


def _write_data_csv(tmp_path: Path, chromosome: str = "6", row_count: int = VARIANTS) -> Path:
    """A minimal data.csv row. generate_workflow/load_data_csv only parse
    this file -- they never open the VCF/annotation files it names -- so no
    VCF needs to exist on disk for the HyperFlow side of the parity test.
    """
    vcf_name = f"ALL.chr{chromosome}.fixture.vcf"
    annotation_name = f"ALL.chr{chromosome}.fixture.annotation.vcf"
    data_csv = tmp_path / "data.csv"
    data_csv.write_text(f"{vcf_name},{row_count},{annotation_name}\n")
    return data_csv


def _write_columns_fixture(tmp_path: Path, populations: list[str], chromosome: str = "6", row_count: int = 1000) -> Path:
    """A synthetic data.csv plus a VCF carrying a #CHROM header, so
    generate_columns_txt (via NextflowBackend.materialize) has something to
    read individual IDs from without touching the real 1000 Genomes data.

    The header's individuals are the union of the named bundled
    populations' real sample IDs -- read straight from
    src/workflow_composer/data/populations/ -- mirroring how a real VCF's
    sample columns line up with the population files.
    """
    individuals: list[str] = []
    for pop in populations:
        individuals.extend((BUNDLED_POPULATIONS_DIR / pop).read_text().split())

    vcf_name = f"ALL.chr{chromosome}.fixture.vcf"
    annotation_name = f"ALL.chr{chromosome}.fixture.annotation.vcf"
    header = "\t".join(_VCF_FIXED_FIELDS + individuals)
    (tmp_path / vcf_name).write_text(f"##fileformat=VCFv4.1\n{header}\n")

    data_csv = tmp_path / "data.csv"
    data_csv.write_text(f"{vcf_name},{row_count},{annotation_name}\n")
    return data_csv


# ---------------------------------------------------------------------------
# 1. Both backends receive an identical Parallelism
# ---------------------------------------------------------------------------

def test_hyperflow_and_nextflow_backends_receive_identical_parallelism():
    expected = recommend_parallelism(
        variants=VARIANTS,
        individuals=INDIVIDUALS,
        vcpus=ENV.vcpus,
        host_mem_mb=ENV.host_mem_mb,
        chromosomes=1,
        mem_budget_mb=ENV.mem_budget_mb,
        engine_reserve=ENV.engine_reserve,
        host_reserve_mb=ENV.host_reserve_mb,
    )

    # HyperFlow: clamp_ind_jobs is what generate_workflow calls per
    # chromosome. A hint at or above expected.ind_jobs clamps to exactly
    # expected.ind_jobs, and the Parallelism it returns must be the
    # identical value recommend_parallelism produced -- not a re-derived one.
    _, hyperflow_resolution = clamp_ind_jobs(
        ind_jobs_hint=expected.ind_jobs,
        row_count=VARIANTS,
        individuals=INDIVIDUALS,
        env=ENV,
        chromosomes=1,
    )
    assert hyperflow_resolution == expected

    # Nextflow: build_command must bind resolution's fields verbatim.
    intent = ResearchIntent(
        analysis_type="multi_population", populations=["EUR", "AFR"], chromosomes=["6"],
    )
    params = intent_to_params(intent)
    command = build_command(params, expected)
    assert command[command.index("--ind_jobs") + 1] == str(expected.ind_jobs)
    assert command[command.index("--ind_max_forks") + 1] == str(expected.max_parallelism)
    assert command[command.index("--task_mem") + 1] == f"{expected.est_peak_mb}MB"

    # Both backends' Parallelism, side by side.
    assert hyperflow_resolution.ind_jobs == expected.ind_jobs
    assert hyperflow_resolution.max_parallelism == expected.max_parallelism
    assert hyperflow_resolution.est_peak_mb == expected.est_peak_mb


def test_hyperflow_generator_metadata_matches_the_same_recommend_parallelism_call(tmp_path):
    """End-to-end: generate_workflow's per-chromosome metadata carries
    exactly the dials recommend_parallelism computes for the same
    (variants, individuals, ComputeEnvironment) -- proof the generator
    reports what it actually used rather than an independently adjusted
    value.
    """
    data_csv = _write_data_csv(tmp_path, chromosome="6", row_count=VARIANTS)

    expected = recommend_parallelism(
        variants=VARIANTS,
        individuals=INDIVIDUALS,
        vcpus=ENV.vcpus,
        host_mem_mb=ENV.host_mem_mb,
        chromosomes=1,
        mem_budget_mb=ENV.mem_budget_mb,
        engine_reserve=ENV.engine_reserve,
        host_reserve_mb=ENV.host_reserve_mb,
    )

    workflow = generate_workflow(
        data_csv=data_csv,
        populations_dir=BUNDLED_POPULATIONS_DIR,
        ind_jobs=expected.ind_jobs,
        chromosome_filter=["6"],
        population_filter=["EUR", "AFR"],
        individuals=INDIVIDUALS,
        compute_environment=ENV,
    )

    entry = workflow["metadata"]["parallelism"][0]
    assert entry["ind_jobs"] == expected.ind_jobs
    assert entry["max_parallelism"] == expected.max_parallelism
    assert entry["est_peak_mb"] == expected.est_peak_mb


# ---------------------------------------------------------------------------
# 2. columns.txt reflects the intent's populations, not the full cohort
# ---------------------------------------------------------------------------

def test_columns_txt_has_population_sample_count_not_full_cohort(tmp_path):
    eur_count = len((BUNDLED_POPULATIONS_DIR / "EUR").read_text().split())
    afr_count = len((BUNDLED_POPULATIONS_DIR / "AFR").read_text().split())

    data_csv = _write_columns_fixture(tmp_path, populations=["EUR", "AFR"], chromosome="6")

    intent = ResearchIntent(
        analysis_type="single_population", populations=["EUR"], chromosomes=["6"],
    )
    resolution = Parallelism(
        ind_jobs=1, max_parallelism=1, est_peak_mb=100, binding="cores", reason="x",
    )

    spec = NextflowBackend().materialize(
        intent=intent, measurements=None, resolution=resolution, data_csv=data_csv,
    )

    columns_txt = spec.files["columns.txt"]
    individual_count = len(columns_txt.rstrip("\n").split("\t")) - 9

    # EUR alone, not the full VCF's sample space (EUR + AFR) and not the
    # full bundled cohort (the ALL population file).
    assert individual_count == eur_count
    assert individual_count != eur_count + afr_count
    all_count = len((BUNDLED_POPULATIONS_DIR / "ALL").read_text().split())
    assert individual_count != all_count


def test_columns_txt_without_data_csv_still_falls_back_to_full_cohort_placeholder():
    """materialize's pre-4.6 behaviour is preserved when no data_csv is
    available (e.g. before data extraction has run): a caller that cannot
    supply a real VCF header still gets a usable, if unfiltered,
    columns.txt rather than an error.
    """
    intent = ResearchIntent(
        analysis_type="single_population", populations=["EUR"], chromosomes=["6"],
    )
    resolution = Parallelism(
        ind_jobs=1, max_parallelism=1, est_peak_mb=100, binding="cores", reason="x",
    )

    spec = NextflowBackend().materialize(intent=intent, measurements=None, resolution=resolution)

    columns_txt = spec.files["columns.txt"]
    individual_count = len(columns_txt.rstrip("\n").split("\t")) - 9
    all_count = len((BUNDLED_POPULATIONS_DIR / "ALL").read_text().split())
    assert individual_count == all_count
