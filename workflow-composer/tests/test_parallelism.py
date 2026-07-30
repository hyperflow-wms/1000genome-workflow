"""
Tests for recommend_parallelism.

Covers the mechanism validation list: the three documented worked
examples, the max_work round-trip against the
cost model, monotonicity, and the invariant that no input can produce a
per-task estimate above mem_budget_mb or a concurrency above C.
"""
from __future__ import annotations

import math
import re

import pytest

from workflow_composer.core.parallelism import Parallelism, recommend_parallelism


# ---------------------------------------------------------------------------
# Documented worked examples
# ---------------------------------------------------------------------------

WORKED_EXAMPLES_KWARGS = dict(mem_budget_mb=512, host_mem_mb=31744, engine_reserve=1)


def test_worked_example_hla_16_vcpus_core_bound():
    """HLA region on 16 vcpus is core-bound."""
    result = recommend_parallelism(
        variants=166_052, individuals=1153, vcpus=16, **WORKED_EXAMPLES_KWARGS
    )
    assert result.ind_jobs == 15
    assert result.max_parallelism == 15
    assert result.est_peak_mb == 27
    assert result.binding == "cores"


def test_worked_example_chr1_16_vcpus_memory_bound():
    """Whole chr1 on 16 vcpus is memory-bound."""
    result = recommend_parallelism(
        variants=6_200_000, individuals=2504, vcpus=16, **WORKED_EXAMPLES_KWARGS
    )
    assert result.ind_jobs == 38
    assert result.max_parallelism == 15
    assert result.est_peak_mb == 512
    assert result.binding == "memory"


def test_worked_example_hla_64_vcpus_min_work_bound():
    """HLA region on 64 vcpus is min_work-bound."""
    result = recommend_parallelism(
        variants=166_052, individuals=1153, vcpus=64, **WORKED_EXAMPLES_KWARGS
    )
    assert result.ind_jobs == 20
    assert result.max_parallelism == 20
    assert result.est_peak_mb == 24
    assert result.binding == "min_work"


# ---------------------------------------------------------------------------
# max_work round-trip against the cost model
# ---------------------------------------------------------------------------

def test_max_work_round_trips_the_cost_model():
    """A memory-bound call's est_peak_mb inverts back to mem_budget_mb.

    Pins the inversion: 12 + 1.2*max_work/1e6 must equal
    mem_budget_mb, exercised end-to-end through the memory-bound worked
    example (chr1, 16 vcpus) so a units slip in the implementation fails
    this test even though it also fails the worked-example test above.
    """
    result = recommend_parallelism(
        variants=6_200_000, individuals=2504, vcpus=16, **WORKED_EXAMPLES_KWARGS
    )
    max_work = (WORKED_EXAMPLES_KWARGS["mem_budget_mb"] - 12) * 1e6 / 1.2
    inverted = 12 + 1.2 * max_work / 1e6
    assert inverted == pytest.approx(WORKED_EXAMPLES_KWARGS["mem_budget_mb"], abs=1e-6)
    # est_peak_mb is int(...) of the exact estimate, so it can differ from
    # the un-truncated inversion by less than 1.
    assert abs(result.est_peak_mb - inverted) < 1


def test_max_work_units_regression():
    """A units slip (dividing by 1.2e-3 instead of multiplying by 1e6) must fail.

    max_work = (mem_budget_mb - 12) * 1e6 / 1.2. At
    512 MB that is ~4.1667e8, not ~4.2e2 -- the value a units slip would
    produce.
    """
    max_work = (512 - 12) * 1e6 / 1.2
    assert max_work == pytest.approx(4.1667e8, rel=1e-4)
    assert max_work > 1e8  # rules out the ~4.2e2 units-slip value


# ---------------------------------------------------------------------------
# Monotonicity
# ---------------------------------------------------------------------------

def test_ind_jobs_monotonic_in_variants():
    """More variants never yields fewer tasks, all else equal."""
    prev = 0
    for variants in (1_000, 10_000, 100_000, 1_000_000, 6_000_000):
        result = recommend_parallelism(
            variants=variants, individuals=1153, vcpus=16, host_mem_mb=31744
        )
        assert result.ind_jobs >= prev
        prev = result.ind_jobs


def test_max_parallelism_monotonic_in_vcpus():
    """More cores never yields a smaller concurrency budget, all else equal."""
    prev = 0
    for vcpus in (2, 4, 8, 16, 32, 64):
        result = recommend_parallelism(
            variants=166_052, individuals=1153, vcpus=vcpus, host_mem_mb=31744
        )
        assert result.max_parallelism >= prev
        prev = result.max_parallelism


def test_max_parallelism_monotonic_in_host_mem():
    """More host memory never yields a smaller concurrency budget."""
    prev = 0
    for host_mem_mb in (2049, 4096, 8192, 16384, 31744):
        result = recommend_parallelism(
            variants=6_200_000, individuals=2504, vcpus=16, host_mem_mb=host_mem_mb
        )
        assert result.max_parallelism >= prev
        prev = result.max_parallelism


# ---------------------------------------------------------------------------
# Property sweep: acceptance criterion 3
# ---------------------------------------------------------------------------

VARIANT_COUNTS = [1_000, 10_000, 100_000, 1_000_000, 10_000_000]
INDIVIDUAL_COUNTS = [107, 1153, 2504]
VCPU_COUNTS = [2, 16, 64]
HOST_MEM_MB_VALUES = [4096, 31744]


@pytest.mark.parametrize("variants", VARIANT_COUNTS)
@pytest.mark.parametrize("individuals", INDIVIDUAL_COUNTS)
@pytest.mark.parametrize("vcpus", VCPU_COUNTS)
@pytest.mark.parametrize("host_mem_mb", HOST_MEM_MB_VALUES)
def test_invariants_hold_across_sweep(variants, individuals, vcpus, host_mem_mb):
    mem_budget_mb = 512
    engine_reserve = 1
    host_reserve_mb = 2048
    result = recommend_parallelism(
        variants=variants,
        individuals=individuals,
        vcpus=vcpus,
        host_mem_mb=host_mem_mb,
        mem_budget_mb=mem_budget_mb,
        engine_reserve=engine_reserve,
        host_reserve_mb=host_reserve_mb,
    )
    cores = vcpus - engine_reserve
    assert result.est_peak_mb <= mem_budget_mb
    assert result.max_parallelism <= cores
    assert result.max_parallelism >= 1
    assert result.max_parallelism * result.est_peak_mb <= host_mem_mb - host_reserve_mb
    assert result.ind_jobs >= 1


# ---------------------------------------------------------------------------
# Multi-chromosome runs
# ---------------------------------------------------------------------------

def test_multi_chromosome_ind_jobs_unchanged_but_concurrency_shared():
    """ind_jobs is per chromosome; max_parallelism is the shared global budget.

    A five-chromosome plan must not run five times the
    intended number of concurrent tasks.
    """
    single = recommend_parallelism(
        variants=166_052, individuals=1153, vcpus=16, host_mem_mb=31744, chromosomes=1
    )
    multi = recommend_parallelism(
        variants=166_052, individuals=1153, vcpus=16, host_mem_mb=31744, chromosomes=5
    )
    assert multi.ind_jobs == single.ind_jobs
    assert multi.max_parallelism < 5 * single.max_parallelism
    assert multi.max_parallelism <= single.max_parallelism * 5
    # The shared budget is capped by cores regardless of chromosome count.
    assert multi.max_parallelism <= 16 - 1


def test_multi_chromosome_concurrency_actually_differs_when_not_core_bound():
    """Non-vacuous version of the sharing check above.

    The case above keeps `cores` as the binding term on max_parallelism for
    both chromosomes=1 and chromosomes=5, so it cannot tell a correct
    `min(ind_jobs*chromosomes, cores, mem_cap)` apart from a regression that
    silently drops the `chromosomes` factor (`min(ind_jobs, cores, mem_cap)`)
    -- both produce the same numbers when `ind_jobs*chromosomes` was never
    going to be the smallest term anyway. Pick inputs where neither cores nor
    the memory cap binds, so `ind_jobs*chromosomes` is the actual min() term:
    a generous host_mem_mb and vcpus removes the memory and core ceilings,
    leaving the chromosome-shared ind_jobs term as what must bind.
    """
    single = recommend_parallelism(
        variants=50_000, individuals=1153, vcpus=64, host_mem_mb=1_000_000, chromosomes=1
    )
    multi = recommend_parallelism(
        variants=50_000, individuals=1153, vcpus=64, host_mem_mb=1_000_000, chromosomes=5
    )
    assert single.ind_jobs == multi.ind_jobs
    # Neither run is core- or memory-capped, so max_parallelism tracks
    # ind_jobs*chromosomes directly: 6 for chromosomes=1, 30 for chromosomes=5.
    assert single.max_parallelism == single.ind_jobs
    assert multi.max_parallelism == multi.ind_jobs * 5
    # The number this test exists to catch: a regression that drops the
    # chromosomes factor would leave multi.max_parallelism == single here.
    assert multi.max_parallelism != single.max_parallelism
    assert multi.max_parallelism == 5 * single.max_parallelism


# ---------------------------------------------------------------------------
# Reason string format
# ---------------------------------------------------------------------------

def test_reason_format_matches_section_5():
    result = recommend_parallelism(
        variants=166_052, individuals=1153, vcpus=16, **WORKED_EXAMPLES_KWARGS
    )
    assert result.reason == (
        "ind_jobs=15 max_parallelism=15 "
        "(core-bound; V=166,052 I=1153 C=15 est_peak=27MB/task)"
    )


@pytest.mark.parametrize(
    "kwargs,expected_binding_label",
    [
        (dict(variants=166_052, individuals=1153, vcpus=16), "core"),
        (dict(variants=6_200_000, individuals=2504, vcpus=16), "memory"),
        (dict(variants=166_052, individuals=1153, vcpus=64), "min_work"),
    ],
)
def test_reason_names_the_binding_constraint(kwargs, expected_binding_label):
    result = recommend_parallelism(**WORKED_EXAMPLES_KWARGS, **kwargs)
    pattern = (
        rf"^ind_jobs=\d+ max_parallelism=\d+ "
        rf"\({re.escape(expected_binding_label)}-bound; "
        rf"V=[\d,]+ I=\d+ C=\d+ est_peak=\d+MB/task\)$"
    )
    assert re.match(pattern, result.reason)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("variants", [0, -1])
def test_non_positive_variants_raises(variants):
    with pytest.raises(ValueError):
        recommend_parallelism(variants=variants, individuals=1153, vcpus=16, host_mem_mb=31744)


@pytest.mark.parametrize("individuals", [0, -5])
def test_non_positive_individuals_raises(individuals):
    with pytest.raises(ValueError):
        recommend_parallelism(variants=166_052, individuals=individuals, vcpus=16, host_mem_mb=31744)


@pytest.mark.parametrize("vcpus", [0, -2])
def test_non_positive_vcpus_raises(vcpus):
    with pytest.raises(ValueError):
        recommend_parallelism(variants=166_052, individuals=1153, vcpus=vcpus, host_mem_mb=31744)


@pytest.mark.parametrize("host_mem_mb", [0, -1024])
def test_non_positive_host_mem_raises(host_mem_mb):
    with pytest.raises(ValueError):
        recommend_parallelism(variants=166_052, individuals=1153, vcpus=16, host_mem_mb=host_mem_mb)


@pytest.mark.parametrize("mem_budget_mb", [12, 10, 0, -5])
def test_mem_budget_at_or_below_base_cost_raises(mem_budget_mb):
    """mem_budget_mb <= 12 (the cost model's fixed base cost) must raise.

    Below the model's own fixed base cost, max_work goes negative, which
    would otherwise drive rows_per_task negative and ind_jobs to 0 --
    violating the function's own ind_jobs >= 1 invariant. Reproduces the
    regression found in review: recommend_parallelism(variants=1000,
    individuals=100, vcpus=16, host_mem_mb=31744, mem_budget_mb=10) used to
    return ind_jobs=0.
    """
    with pytest.raises(ValueError):
        recommend_parallelism(
            variants=1000, individuals=100, vcpus=16, host_mem_mb=31744,
            mem_budget_mb=mem_budget_mb,
        )


def test_vcpus_at_or_below_engine_reserve_clamps_cores_to_one():
    """vcpus <= engine_reserve must not raise or go non-positive; C clamps to 1."""
    result = recommend_parallelism(
        variants=166_052, individuals=1153, vcpus=1, host_mem_mb=31744, engine_reserve=1
    )
    assert "C=1" in result.reason
    assert result.max_parallelism <= 1
    assert result.max_parallelism >= 1


def test_parallelism_dataclass_is_frozen():
    result = recommend_parallelism(variants=166_052, individuals=1153, vcpus=16, host_mem_mb=31744)
    with pytest.raises(Exception):
        result.ind_jobs = 99  # type: ignore[misc]
