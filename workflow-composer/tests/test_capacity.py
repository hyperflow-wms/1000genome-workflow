"""
Arithmetic tests for core/capacity.py.

Covers the mechanism validation for this milestone: hand-computed W/S/C*
for one region, the chr6 worked example for optimal_ind_jobs, the
max_ind_jobs clamp at both ends, and the ValueError contract. Also covers
the four A5 properties from CAPACITY-IMPLEMENTATION-PLAN.md section 3 --
C* never exceeding the DAG's maximum width, J* minimising predicted span,
region-count monotonicity of W and C*, and the two-regime cost formula --
plus the Q1/Q3 gate reproductions against RFC-006-REVIEW.md section 8.

The property tests are hand-parametrised over a spread of region sets
(one large plus several small, all-small, all-large, K from 1 to 5, P
from 1 to 7) rather than pulling in a property-testing dependency -- see
CAPACITY-IMPLEMENTATION-PLAN.md section 3, workstream A5.
"""
from __future__ import annotations

import math

import pytest

from workflow_composer.core.capacity import (
    Capacity,
    RegionEstimate,
    optimal_ind_jobs,
    recommend_capacity,
)
from workflow_composer.core.performance_model import (
    DEFAULT_PERFORMANCE_MODEL,
    PerformanceModel,
)


# ---------------------------------------------------------------------------
# RegionEstimate.data_volume
# ---------------------------------------------------------------------------

def test_data_volume_is_variants_times_individuals():
    region = RegionEstimate(name="HLA", chromosome="6", variants=166_052, individuals=1153)
    assert region.data_volume == 166_052 * 1153


# ---------------------------------------------------------------------------
# optimal_ind_jobs: the chr6 worked example, section 2.1.1
# ---------------------------------------------------------------------------

def test_optimal_ind_jobs_chr6_worked_example():
    # D = 2.745e8, shipped coefficients -> J* ~= 26 (measured optimum 20,
    # current policy's 38 -- section 2.1.1).
    assert optimal_ind_jobs(2.745e8, DEFAULT_PERFORMANCE_MODEL) == 26


def test_optimal_ind_jobs_matches_hand_derivation():
    model = DEFAULT_PERFORMANCE_MODEL
    d = 2.745e8
    expected = round(math.sqrt(model.b_ind * d / model.c_merge))
    assert optimal_ind_jobs(d, model) == expected


def test_optimal_ind_jobs_never_below_one():
    # A tiny data volume should still floor at 1, not 0.
    assert optimal_ind_jobs(1.0, DEFAULT_PERFORMANCE_MODEL) >= 1
    assert optimal_ind_jobs(0.0, DEFAULT_PERFORMANCE_MODEL) >= 1


# ---------------------------------------------------------------------------
# optimal_ind_jobs: max_ind_jobs clamp holds at both ends
# ---------------------------------------------------------------------------

def test_optimal_ind_jobs_clamps_large_region_to_ceiling():
    # Unclamped J* for a large D would exceed 4 by a wide margin.
    unclamped = optimal_ind_jobs(2.745e8, DEFAULT_PERFORMANCE_MODEL)
    assert unclamped > 4
    clamped = optimal_ind_jobs(2.745e8, DEFAULT_PERFORMANCE_MODEL, max_ind_jobs=4)
    assert clamped == 4


def test_optimal_ind_jobs_tiny_region_still_gets_one():
    # A tiny D's unclamped J* is already 1, and a ceiling above 1 must not
    # push it up.
    tiny = optimal_ind_jobs(1.0, DEFAULT_PERFORMANCE_MODEL, max_ind_jobs=4)
    assert tiny == 1


def test_optimal_ind_jobs_clamp_never_goes_below_one():
    assert optimal_ind_jobs(2.745e8, DEFAULT_PERFORMANCE_MODEL, max_ind_jobs=1) == 1


# ---------------------------------------------------------------------------
# recommend_capacity: one region, hand-computed W/S/C*
# ---------------------------------------------------------------------------

def _hand_compute_one_region(d: float, p: int, model: PerformanceModel, j: int) -> tuple[float, float]:
    """Independent re-derivation of work_r/span_r from the section 2.1
    formulas, used only to check the module's arithmetic -- not copied
    from the module under test."""
    work_r = (
        model.a_ind * j + model.b_ind * d
        + model.b_merge * d + model.c_merge * j
        + p * (model.a_mo + model.b_mo * d)
        + p * (model.a_fr + model.b_fr * d)
    )
    span_r = (
        (model.a_ind + model.b_ind * d / j)
        + (model.b_merge * d + model.c_merge * j)
        + max(model.a_mo + model.b_mo * d, model.a_fr + model.b_fr * d)
    )
    return work_r, span_r


def test_recommend_capacity_single_region_matches_hand_computation():
    model = DEFAULT_PERFORMANCE_MODEL
    variants, individuals, populations = 166_052, 1153, 3
    region = RegionEstimate(name="HLA", chromosome="6", variants=variants, individuals=individuals)
    d = region.data_volume

    j_expected = round(math.sqrt(model.b_ind * d / model.c_merge))
    j_expected = max(1, j_expected)
    expected_work, expected_span = _hand_compute_one_region(d, populations, model, j_expected)
    expected_slots_exact = expected_work / expected_span
    expected_slots = max(1, math.ceil(expected_slots_exact))

    result = recommend_capacity([region], populations, model)

    assert result.ind_jobs["HLA"] == j_expected
    assert result.work_seconds == pytest.approx(expected_work)
    assert result.span_seconds == pytest.approx(expected_span)
    assert result.slots_exact == pytest.approx(expected_slots_exact)
    assert result.slots == expected_slots
    assert result.model_version == model.version


def test_recommend_capacity_slots_is_ceil_of_work_over_span():
    region = RegionEstimate(name="HLA", chromosome="6", variants=166_052, individuals=1153)
    result = recommend_capacity([region], 3, DEFAULT_PERFORMANCE_MODEL)
    assert result.slots == max(1, math.ceil(result.work_seconds / result.span_seconds))


def test_recommend_capacity_slots_never_below_one():
    # A minimal single-variant, single-individual region should still
    # recommend at least one slot.
    region = RegionEstimate(name="tiny", chromosome="1", variants=1, individuals=1)
    result = recommend_capacity([region], 1, DEFAULT_PERFORMANCE_MODEL)
    assert result.slots >= 1


# ---------------------------------------------------------------------------
# Capacity.predicted_makespan / predicted_cost
# ---------------------------------------------------------------------------

def test_predicted_makespan_is_max_of_work_over_c_and_span():
    capacity = Capacity(
        slots=4,
        slots_exact=3.5,
        work_seconds=2500.0,
        span_seconds=650.0,
        ind_jobs={"HLA": 26},
        model_version="1.0.0",
        reason="test",
    )
    # Work-bound below C*.
    assert capacity.predicted_makespan(2) == pytest.approx(max(2500.0 / 2, 650.0))
    # Span-bound at or above C*.
    assert capacity.predicted_makespan(10) == pytest.approx(650.0)


def test_predicted_cost_is_c_times_predicted_makespan():
    capacity = Capacity(
        slots=4,
        slots_exact=3.5,
        work_seconds=2500.0,
        span_seconds=650.0,
        ind_jobs={"HLA": 26},
        model_version="1.0.0",
        reason="test",
    )
    for c in (1, 2, 4, 7, 20):
        assert capacity.predicted_cost(c) == pytest.approx(c * capacity.predicted_makespan(c))


# ---------------------------------------------------------------------------
# ValueError contract
# ---------------------------------------------------------------------------

def test_recommend_capacity_raises_on_empty_regions():
    with pytest.raises(ValueError):
        recommend_capacity([], 3, DEFAULT_PERFORMANCE_MODEL)


def test_recommend_capacity_raises_on_non_positive_populations():
    region = RegionEstimate(name="HLA", chromosome="6", variants=166_052, individuals=1153)
    with pytest.raises(ValueError):
        recommend_capacity([region], 0, DEFAULT_PERFORMANCE_MODEL)
    with pytest.raises(ValueError):
        recommend_capacity([region], -1, DEFAULT_PERFORMANCE_MODEL)


def test_recommend_capacity_raises_on_non_positive_variants():
    region = RegionEstimate(name="bad", chromosome="6", variants=0, individuals=1153)
    with pytest.raises(ValueError):
        recommend_capacity([region], 3, DEFAULT_PERFORMANCE_MODEL)


def test_recommend_capacity_raises_on_non_positive_individuals():
    region = RegionEstimate(name="bad", chromosome="6", variants=166_052, individuals=0)
    with pytest.raises(ValueError):
        recommend_capacity([region], 3, DEFAULT_PERFORMANCE_MODEL)


# ---------------------------------------------------------------------------
# A5 property tests (CAPACITY-IMPLEMENTATION-PLAN.md section 3)
#
# Region sets are expressed as (name, chromosome, D) triples with
# individuals=1, so D itself is the region's data_volume -- this keeps the
# fixtures readable without needing separately plausible variants/individuals
# splits. Six sets, spanning K=1..5 and P=1..7, one-large-plus-small,
# all-small and all-large shapes, cover every property below.
# ---------------------------------------------------------------------------

def _regions(triples: list[tuple[str, str, float]]) -> list[RegionEstimate]:
    return [
        RegionEstimate(name=name, chromosome=chrom, variants=int(round(d)), individuals=1)
        for name, chrom, d in triples
    ]


REGION_SETS: dict[str, tuple[list[tuple[str, str, float]], int]] = {
    "one_large_several_small": (
        [("R1", "1", 2.745e8), ("R2", "2", 1e5), ("R3", "3", 2e5), ("R4", "4", 5e4)],
        3,
    ),
    "all_small": (
        [("R1", "1", 5e4), ("R2", "2", 8e4), ("R3", "3", 3e4)],
        2,
    ),
    "all_large": (
        [("R1", "1", 1.5e8), ("R2", "2", 2e8)],
        5,
    ),
    "single_region_k1_p1": (
        [("R1", "1", 1e6)],
        1,
    ),
    "many_regions_k5_p7": (
        [
            ("R1", "1", 1e5),
            ("R2", "2", 2e5),
            ("R3", "3", 1.5e5),
            ("R4", "4", 3e5),
            ("R5", "5", 2.5e5),
        ],
        7,
    ),
    "mixed_k3_p4": (
        [("R1", "1", 1e7), ("R2", "2", 5e5), ("R3", "3", 2e6)],
        4,
    ),
}


def _span_r(d: float, j: int, model: PerformanceModel) -> float:
    """Independent re-derivation of span_r from section 2.1, used only to
    check properties of the module under test -- not copied from it."""
    return (
        (model.a_ind + model.b_ind * d / j)
        + (model.b_merge * d + model.c_merge * j)
        + max(model.a_mo + model.b_mo * d, model.a_fr + model.b_fr * d)
    )


# --- Property 1: C* never exceeds the DAG's maximum width -----------------
#
# Section 2.1 proves W <= max_width * S, so C* = W/S <= max_width always.
# This is a mathematical consequence of the model, not something the
# implementation enforces -- capacity.py has no width clamp (grep it for
# "2 *" or "max_width": neither appears). The test asserts the property
# holds; it must not be turned into an assertion inside recommend_capacity.

@pytest.mark.parametrize("set_name", sorted(REGION_SETS))
def test_slots_never_exceeds_dag_max_width(set_name):
    triples, populations = REGION_SETS[set_name]
    regions = _regions(triples)
    k = len(regions)
    result = recommend_capacity(regions, populations, DEFAULT_PERFORMANCE_MODEL)

    max_width = max(sum(result.ind_jobs.values()), 2 * k * populations)
    assert result.slots <= max_width


# --- Property 2: J* minimises the predicted span ---------------------------

@pytest.mark.parametrize("set_name", sorted(REGION_SETS))
def test_optimal_ind_jobs_minimises_span_over_a_wide_integer_range(set_name):
    triples, _populations = REGION_SETS[set_name]
    model = DEFAULT_PERFORMANCE_MODEL

    for _name, _chrom, d in triples:
        j_star = optimal_ind_jobs(d, model)
        span_at_star = _span_r(d, j_star, model)
        for j in range(1, 4 * j_star + 11):
            assert span_at_star <= _span_r(d, j, model) + 1e-9, (
                f"J={j} beats J*={j_star} for D={d}: "
                f"{_span_r(d, j, model)} < {span_at_star}"
            )


# --- Property 3: adding a region raises W and C*, leaves S unchanged ------
#
# Holds when the added region's own span is below the current maximum.
# A minimal region (D=1, J=1) has span ~= a_ind + c_merge + max(a_mo, a_fr)
# -- about 114s with the shipped coefficients, the floor described in
# RFC-006-REVIEW.md section 8 -- which is below every base set's max span
# here, since each set contains at least one region of realistic genomic
# size.

@pytest.mark.parametrize("set_name", sorted(REGION_SETS))
def test_adding_a_small_region_raises_work_and_capacity_leaves_span_unchanged(set_name):
    triples, populations = REGION_SETS[set_name]
    model = DEFAULT_PERFORMANCE_MODEL
    base_regions = _regions(triples)
    base = recommend_capacity(base_regions, populations, model)

    added_d = 1.0
    added_j = optimal_ind_jobs(added_d, model)
    added_span = _span_r(added_d, added_j, model)
    assert added_span < base.span_seconds, (
        "fixture invariant violated: added region's span must be below "
        "the base set's max span for this property to be meaningful"
    )

    grown_regions = base_regions + [
        RegionEstimate(name="added-tiny", chromosome="99", variants=1, individuals=1)
    ]
    grown = recommend_capacity(grown_regions, populations, model)

    assert grown.work_seconds > base.work_seconds
    assert grown.span_seconds == pytest.approx(base.span_seconds)
    assert grown.slots_exact > base.slots_exact
    assert grown.slots >= base.slots


# --- Property 4: the two-regime cost formula --------------------------------
#
# predicted_cost(c) = c * max(W/c, S) is flat at W below the knee C*=W/S
# and linear in c above it -- a direct consequence of the max() in
# predicted_makespan, not a separate code path to test for divergence.

@pytest.mark.parametrize("set_name", sorted(REGION_SETS))
def test_predicted_cost_is_flat_below_slots_exact_and_linear_above(set_name):
    triples, populations = REGION_SETS[set_name]
    result = recommend_capacity(_regions(triples), populations, DEFAULT_PERFORMANCE_MODEL)

    below = max(1, math.floor(result.slots_exact))
    for c in range(1, below + 1):
        assert result.predicted_cost(c) == pytest.approx(result.work_seconds, rel=1e-9)

    for c in range(below + 1, below + 21):
        if c <= result.slots_exact:
            continue
        assert result.predicted_cost(c) == pytest.approx(
            c * result.span_seconds, rel=1e-9
        )


# ---------------------------------------------------------------------------
# M1 gate: Q1 and Q3 reproduce RFC-006-REVIEW.md section 8's C* of about
# 4.0 and 14.3.
#
# D values are taken from the documents, not from the planner:
#
#   Q1 (HLA + BRCA1, P=3): chr6 D=2.745e8 (CAPACITY-IMPLEMENTATION-PLAN.md
#     section 2.1.1); chr17 D=2400*1668 -- 2400 is the BRCA1 variant count
#     in engines/hyperflow/harness/cases.yaml's q1-hla-brca1 description
#     ("2.4K on chr17"), 1668 the cohort size behind section 8's D.
#
#   Q3 (HBB + CFTR + APOE, P=5): chr7 D=1.1e7 (RFC-006-REVIEW.md section 8);
#     chr11 D=136*2504, chr19 D=113*2504 -- 136 and 113 are the HBB and APOE
#     variant counts in cases.yaml's q3-multi-region description, 2504 the
#     1000 Genomes cohort size.
#
# The reference implementation of section 2.1's exact formulas gives
# slots_exact ~= 3.58 for Q1 and ~= 13.61 for Q3, both about 10% and 5%
# below section 8's tabulated 4.0 and 14.3. Section 8's table is computed
# with a simplified span that drops the b_ind*D/J and c_merge*J terms
# (individuals and merge collapse to one fixed-cost line each), which is
# why the exact model sits below it. That gap is expected and is not a
# defect to close by tuning coefficients -- the bands below already account
# for it.

def test_q1_hla_brca1_reproduces_section_8_capacity():
    regions = [
        RegionEstimate(name="HLA", chromosome="6", variants=int(2.745e8), individuals=1),
        RegionEstimate(name="BRCA1", chromosome="17", variants=2400 * 1668, individuals=1),
    ]
    result = recommend_capacity(regions, populations=3, model=DEFAULT_PERFORMANCE_MODEL)

    assert math.ceil(result.slots_exact) == 4
    assert 3.2 <= result.slots_exact <= 4.8
    assert result.slots == 4


def test_q3_multi_region_reproduces_section_8_capacity():
    regions = [
        RegionEstimate(name="CFTR", chromosome="7", variants=int(1.1e7), individuals=1),
        RegionEstimate(name="HBB", chromosome="11", variants=136 * 2504, individuals=1),
        RegionEstimate(name="APOE", chromosome="19", variants=113 * 2504, individuals=1),
    ]
    result = recommend_capacity(regions, populations=5, model=DEFAULT_PERFORMANCE_MODEL)

    assert math.ceil(result.slots_exact) == 14
    assert 11.4 <= result.slots_exact <= 17.2
    assert result.slots == 14
