"""
Tests for planner.resolve_parallelism.

Covers the acceptance criteria for "Route planner, CLI, and MCP server
through recommend_parallelism":

1. resolve_parallelism for the HLA intent matches a direct recommend_parallelism
   call over the same (V, I) the planner derived -- no independent arithmetic
   remains in planner.py.
2. The old region-span-preset behaviour is gone: bp span no longer determines
   ind_jobs (only the estimated variant count does), and the same intent on a
   bigger machine gets a bigger ind_jobs (machine-blindness fixed).
3. plan_workflow and create_advisory_plan both populate
   ExecutionHints.max_parallelism/est_peak_mb/parallelism_reason, and
   parameters_used["ind_jobs"] matches the ind_jobs embedded in the reason
   string.
4. An explicit ind_jobs argument is honoured as a hint, and an unknown
   parallelism preset raises ValueError.

Also covers a regression found in review: V must be a per-chromosome figure,
not a sum across the chromosomes/regions an intent touches, because
``generate_workflow`` applies the single resolved ``ind_jobs`` identically to
every chromosome. A summed V inflates ``ind_jobs`` past what any individual
chromosome's real variant count supports and silently drives its real
per-task work under the ``min_work`` floor.
"""
from __future__ import annotations

import re

import pytest

from workflow_composer.core.data_resolver import KNOWN_REGIONS, estimate_variant_count
from workflow_composer.core.environment import ComputeEnvironment
from workflow_composer.core.models import GenomicRegion, OutputFormat, ResearchIntent
from workflow_composer.core.parallelism import recommend_parallelism
from workflow_composer.core.planner import (
    _estimate_individuals,
    _estimate_max_variants_per_chromosome,
    calculate_ind_jobs,
    create_advisory_plan,
    plan_workflow,
    resolve_parallelism,
)

HLA_REGION = KNOWN_REGIONS["HLA"]

WORKFLOW_GENERATOR_PATH_AVAILABLE = True
try:
    from pathlib import Path
    _dc = Path(__file__).parent.parent.parent / "workflow-generator" / "data.csv"
    WORKFLOW_GENERATOR_PATH_AVAILABLE = _dc.exists()
except Exception:
    WORKFLOW_GENERATOR_PATH_AVAILABLE = False


def _hla_eur_afr_intent() -> ResearchIntent:
    return ResearchIntent(
        analysis_type="population_comparison",
        populations=["EUR", "AFR"],
        regions=[HLA_REGION],
    )


# ---------------------------------------------------------------------------
# Acceptance criterion 1: resolve_parallelism matches a direct recommend_parallelism
# call for the same (V, I) -- no independent arithmetic in planner.py.
# ---------------------------------------------------------------------------

def test_hla_matches_direct_recommend_parallelism_call():
    intent = _hla_eur_afr_intent()

    # Derive V and I the same way the planner does, independently of
    # resolve_parallelism itself.
    variants = _estimate_max_variants_per_chromosome(intent)
    individuals = _estimate_individuals(intent.populations)
    assert individuals == 1675  # EUR (664) + AFR (1011) bundled population files

    env = ComputeEnvironment.resolve("local", vcpus=16)
    expected = recommend_parallelism(
        variants=variants,
        individuals=individuals,
        vcpus=env.vcpus,
        host_mem_mb=env.host_mem_mb,
        chromosomes=1,
        mem_budget_mb=env.mem_budget_mb,
        engine_reserve=env.engine_reserve,
        host_reserve_mb=env.host_reserve_mb,
    )

    actual = resolve_parallelism(intent, compute_environment="local", vcpus=16)

    assert actual.ind_jobs == expected.ind_jobs
    assert actual.max_parallelism == expected.max_parallelism
    assert actual.est_peak_mb == expected.est_peak_mb


def test_estimate_max_variants_per_chromosome_uses_estimate_variant_count_not_bp_span():
    """V comes from estimate_variant_count over the region, not its bp span."""
    intent = _hla_eur_afr_intent()
    assert _estimate_max_variants_per_chromosome(intent) == estimate_variant_count(region=HLA_REGION)


# ---------------------------------------------------------------------------
# Acceptance criterion 2: the old behaviour (bp-span buckets, machine-blind)
# is gone.
# ---------------------------------------------------------------------------

def test_bp_span_no_longer_determines_ind_jobs():
    """Two regions with very different bp spans but equal estimated variant
    counts get the same ind_jobs -- the old preset (<1Mb -> small=10,
    1-10Mb -> medium=50) would have given them different values.
    """
    # region_a: chr1, 999,000 bp span (< 1 Mb -> old "small" bucket)
    region_a = GenomicRegion(name="A", chromosome="1", start=1_000_000, end=1_999_000)
    # region_b: chr22, 1,205,250 bp span (>= 1 Mb -> old "medium" bucket)
    region_b = GenomicRegion(name="B", chromosome="22", start=1_000_000, end=2_205_250)

    # Same estimated variant count despite the very different bp spans.
    assert estimate_variant_count(region=region_a) == estimate_variant_count(region=region_b)

    intent_a = ResearchIntent(analysis_type="region_analysis", populations=["EUR"], regions=[region_a])
    intent_b = ResearchIntent(analysis_type="region_analysis", populations=["EUR"], regions=[region_b])

    result_a = resolve_parallelism(intent_a, compute_environment="local")
    result_b = resolve_parallelism(intent_b, compute_environment="local")

    assert result_a.ind_jobs == result_b.ind_jobs


def test_bigger_machine_gets_bigger_ind_jobs():
    """The old rule was machine-blind; the new one isn't."""
    intent = _hla_eur_afr_intent()

    small_machine = resolve_parallelism(intent, compute_environment="local", vcpus=2)
    big_machine = resolve_parallelism(intent, compute_environment="local", vcpus=64)

    assert big_machine.ind_jobs > small_machine.ind_jobs


# ---------------------------------------------------------------------------
# Regression: V must be a per-chromosome figure, never a sum across the
# chromosomes an intent touches. generate_workflow applies the single
# resolved ind_jobs identically to every chromosome, so an ind_jobs sized
# against a summed V belongs to no single chromosome and can silently drive
# a smaller chromosome's real per-task work under the
# min_work=1e7 floor even though the reported ind_jobs "looks" fine in
# aggregate.
# ---------------------------------------------------------------------------

MIN_WORK = 1e7


def _real_work_per_task(variants: int, individuals: int, ind_jobs: int) -> float:
    """Work (rows * individuals) a single chromosome's tasks actually do
    once the *global* ind_jobs from resolve_parallelism is applied to its
    *own* (real, not summed) variant count."""
    return (variants / ind_jobs) * individuals


def test_multi_region_ind_jobs_keeps_every_touched_region_over_the_floor():
    """Two 1 Mb regions on chr6 and chr9 (EUR): summing V=35,529+30,303=65,832
    used to recommend ind_jobs=5, whose real per-task work on chr9's own
    30,303 variants is ~4.0M row*individuals -- well under the 1e7 floor.
    Driving off the larger single region instead keeps both regions' real
    work close to (never far under) the floor.
    """
    region_chr6 = GenomicRegion(name="R6", chromosome="6", start=1_000_000, end=2_000_000)
    region_chr9 = GenomicRegion(name="R9", chromosome="9", start=1_000_000, end=2_000_000)

    v_chr6 = estimate_variant_count(region=region_chr6)
    v_chr9 = estimate_variant_count(region=region_chr9)
    assert v_chr6 != v_chr9  # exercise genuinely different per-chromosome V

    intent = ResearchIntent(
        analysis_type="population_comparison",
        populations=["EUR"],
        regions=[region_chr6, region_chr9],
    )

    result = resolve_parallelism(intent, compute_environment="local", vcpus=16)
    individuals = _estimate_individuals(["EUR"])

    # V driving the recommendation is the larger of the two regions, not
    # their sum.
    assert _estimate_max_variants_per_chromosome(intent) == max(v_chr6, v_chr9)

    for v_real in (v_chr6, v_chr9):
        real_work = _real_work_per_task(v_real, individuals, result.ind_jobs)
        # Allow the same ceil() rounding slop a direct, single-region
        # recommend_parallelism call would have (real work ~0.67-0.79x the
        # floor here), but not the summed-V bug's ~0.40-0.47x undershoot.
        assert real_work >= MIN_WORK * 0.6, (
            f"region with V={v_real} does ~{real_work:,.0f} row*individuals "
            f"of real work per task at ind_jobs={result.ind_jobs}, far under "
            f"the {MIN_WORK:,.0f} min_work floor"
        )


def test_default_whole_genome_ind_jobs_keeps_smallest_chromosome_over_the_floor():
    """No regions/chromosomes (all 22 autosomes touched): summing every
    chromosome's V used to recommend an ind_jobs sized for the aggregate,
    which drove chr21/chr22's real per-task work far under the floor even
    though larger chromosomes looked fine. The driving V must be a single
    chromosome's, not the sum of all 22.
    """
    from workflow_composer.core.data_resolver import CHROMOSOME_VARIANT_COUNT

    intent = ResearchIntent(analysis_type="single_population", populations=["EUR"])

    result = resolve_parallelism(intent, compute_environment="local", vcpus=16)
    individuals = _estimate_individuals(["EUR"])

    autosome_variants = [CHROMOSOME_VARIANT_COUNT[str(i)] for i in range(1, 23)]
    assert _estimate_max_variants_per_chromosome(intent) == max(autosome_variants)

    smallest = min(autosome_variants)
    real_work = _real_work_per_task(smallest, individuals, result.ind_jobs)
    # The pre-fix sum-driven ind_jobs=130 left chr21/chr22 at ~0.56x the
    # floor; driving off the largest single chromosome instead leaves the
    # smallest comfortably over it (~4.9x here, since the whole range ends
    # up cores-bound rather than memory-bound).
    assert real_work >= MIN_WORK * 0.6, (
        f"smallest autosome (V={smallest}) does ~{real_work:,.0f} "
        f"row*individuals of real work per task at ind_jobs={result.ind_jobs}, "
        f"far under the {MIN_WORK:,.0f} min_work floor"
    )


# ---------------------------------------------------------------------------
# Acceptance criterion 3: plan_workflow / create_advisory_plan populate the
# new ExecutionHints fields, and parameters_used["ind_jobs"] agrees with the
# ind_jobs embedded in the reason string.
# ---------------------------------------------------------------------------

def _assert_ind_jobs_matches_reason(plan) -> None:
    match = re.match(r"^ind_jobs=(\d+)", plan.execution_hints.parallelism_reason)
    assert match is not None, plan.execution_hints.parallelism_reason
    assert int(match.group(1)) == plan.parameters_used["ind_jobs"]


def test_create_advisory_plan_populates_execution_hints():
    intent = _hla_eur_afr_intent()
    plan = create_advisory_plan(intent, compute_environment="local")

    assert plan.execution_hints.max_parallelism > 0
    assert plan.execution_hints.est_peak_mb > 0
    assert plan.execution_hints.parallelism_reason
    _assert_ind_jobs_matches_reason(plan)


@pytest.mark.skipif(
    not WORKFLOW_GENERATOR_PATH_AVAILABLE,
    reason="workflow-generator/data.csv not available",
)
def test_plan_workflow_populates_execution_hints():
    intent = _hla_eur_afr_intent()
    plan = plan_workflow(intent, compute_environment="local")

    assert plan.execution_hints.max_parallelism > 0
    assert plan.execution_hints.est_peak_mb > 0
    assert plan.execution_hints.parallelism_reason
    _assert_ind_jobs_matches_reason(plan)


def test_create_advisory_plan_and_plan_workflow_agree_on_ind_jobs():
    """Both planning paths call the same mechanism for the same
    intent/environment, so neither can silently drift far from the other --
    the recorded-vs-executed divergence (plan.json said 50, the harness ran 10) is
    unrepresentable.

    They need not be bit-identical, though: create_advisory_plan sizes V
    from the region-based *estimate* (``_estimate_max_variants_per_
    chromosome``), since it runs without real data files, while
    plan_workflow now reports what
    generate_workflow's per-chromosome clamp *actually did* against the
    *exact* row_count in data.csv -- the authoritative source (see
    generator.py's module docstring). When the estimate and the real
    row_count differ, as they do for this fixture's placeholder
    data.csv (which is 250,000, not this region's true row count), the two
    ind_jobs can land a task or two apart. Reporting the advisory estimate
    unchanged would reproduce that bug in the other direction:
    plan_workflow claiming a value the generator did not actually use.
    """
    intent = _hla_eur_afr_intent()
    advisory = create_advisory_plan(intent, compute_environment="local")

    if WORKFLOW_GENERATOR_PATH_AVAILABLE:
        full = plan_workflow(intent, compute_environment="local")
        assert abs(advisory.parameters_used["ind_jobs"] - full.parameters_used["ind_jobs"]) <= 2, (
            advisory.parameters_used["ind_jobs"], full.parameters_used["ind_jobs"]
        )


# ---------------------------------------------------------------------------
# Acceptance criterion 4: explicit ind_jobs is honoured as a hint; an
# unknown parallelism preset raises ValueError.
# ---------------------------------------------------------------------------

def test_explicit_ind_jobs_is_honoured():
    intent = _hla_eur_afr_intent()
    result = resolve_parallelism(intent, compute_environment="local", ind_jobs=999)
    assert result.ind_jobs == 999
    # max_parallelism/est_peak_mb still reflect the computed recommendation,
    # not the override -- clamping the override against them is a later step.
    assert result.max_parallelism > 0
    assert result.est_peak_mb > 0


def test_unknown_parallelism_preset_raises():
    intent = _hla_eur_afr_intent()
    with pytest.raises(ValueError, match="Unknown parallelism preset"):
        resolve_parallelism(intent, compute_environment="local", parallelism="nonsense")


def test_calculate_ind_jobs_wrapper_matches_resolve_parallelism():
    """The thin backward-compat wrapper returns exactly resolve_parallelism().ind_jobs."""
    intent = _hla_eur_afr_intent()
    assert calculate_ind_jobs(intent) == resolve_parallelism(intent).ind_jobs
    assert calculate_ind_jobs(intent, parallelism="large") == resolve_parallelism(
        intent, parallelism="large"
    ).ind_jobs


def test_calculate_ind_jobs_unknown_preset_raises():
    intent = _hla_eur_afr_intent()
    with pytest.raises(ValueError, match="Unknown parallelism preset"):
        calculate_ind_jobs(intent, parallelism="nonsense")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
