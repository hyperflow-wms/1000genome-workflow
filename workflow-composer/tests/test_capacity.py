"""
Arithmetic tests for core/capacity.py.

Covers the mechanism validation for this milestone: hand-computed
per-stage work/span and the knee-tolerance search for slots on one region,
the chr6 worked example for optimal_ind_jobs, the max_ind_jobs clamp at
both ends, and the ValueError contract. Also covers the four A5 properties
from CAPACITY-IMPLEMENTATION-PLAN.md section 3 -- C* never exceeding the
DAG's maximum width, J* minimising predicted span, region-count
monotonicity of W and C*, and predicted_cost's monotonicity in capacity
(non-decreasing, floors at total work while every stage is work-bound) --
plus the Q1/Q3 slots gate and a check against measured Q1 makespans.

Work and span are accumulated per stage ("individuals", "merge",
"analysis"), not only for the workflow as a whole: predicted_makespan(c)
sums max(stage_work[k]/c, stage_span[k]) over stages, which lets a fully
serial stage (Q1's merge, W/S=1.0) keep contributing its span at every
capacity instead of being hidden inside a single whole-workflow max(). See
capacity.py's module docstring for the full derivation.

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


def _hand_compute_stage_work_span(
    d: float, p: int, model: PerformanceModel, j: int
) -> tuple[dict[str, float], dict[str, float]]:
    """Independent re-derivation of the per-stage work/span accumulation
    for a single region, mirroring capacity.py's per-stage loop body but
    written separately so this test does not just replay the module's own
    arithmetic back at it."""
    t_ind = model.a_ind + model.b_ind * d / j
    t_merge = model.b_merge * d + model.c_merge * j
    t_mo = model.a_mo + model.b_mo * d
    t_fr = model.a_fr + model.b_fr * d

    stage_work = {
        "individuals": j * t_ind,
        "merge": t_merge,
        "analysis": p * (t_mo + t_fr),
    }
    stage_span = {
        "individuals": t_ind,
        "merge": t_merge,
        "analysis": max(t_mo, t_fr),
    }
    return stage_work, stage_span


def _hand_compute_slots_exact(
    stage_work: dict[str, float], stage_span: dict[str, float], model: PerformanceModel
) -> float:
    """Independent re-derivation of the knee search: the smallest capacity
    whose predicted makespan is within ``model.knee_tolerance`` of the
    floor (the sum of stage spans), found by bisection -- see capacity.py's
    module docstring, "Where to stop"."""
    floor = sum(stage_span.values())
    limit = floor * (1.0 + model.knee_tolerance)

    def predicted(c: float) -> float:
        return sum(max(stage_work[k] / c, stage_span[k]) for k in stage_work)

    if predicted(1.0) <= limit:
        return 1.0

    widest = max(
        (stage_work[k] / stage_span[k] for k in stage_work if stage_span[k] > 0),
        default=1.0,
    )
    lo, hi = 1.0, max(widest, 1.0)
    for _ in range(60):
        mid = (lo + hi) / 2
        if predicted(mid) > limit:
            lo = mid
        else:
            hi = mid
    return hi


def test_recommend_capacity_single_region_matches_hand_computation():
    model = DEFAULT_PERFORMANCE_MODEL
    variants, individuals, populations = 166_052, 1153, 3
    region = RegionEstimate(name="HLA", chromosome="6", variants=variants, individuals=individuals)
    d = region.data_volume

    j_expected = round(math.sqrt(model.b_ind * d / model.c_merge))
    j_expected = max(1, j_expected)
    expected_work, expected_span = _hand_compute_one_region(d, populations, model, j_expected)
    expected_stage_work, expected_stage_span = _hand_compute_stage_work_span(
        d, populations, model, j_expected
    )
    expected_slots_exact = _hand_compute_slots_exact(expected_stage_work, expected_stage_span, model)
    expected_slots = max(1, math.ceil(expected_slots_exact))

    result = recommend_capacity([region], populations, model)

    assert result.ind_jobs["HLA"] == j_expected
    assert result.work_seconds == pytest.approx(expected_work)
    assert result.span_seconds == pytest.approx(expected_span)
    for key in ("individuals", "merge", "analysis"):
        assert result.stage_work[key] == pytest.approx(expected_stage_work[key])
        assert result.stage_span[key] == pytest.approx(expected_stage_span[key])
    assert result.slots_exact == pytest.approx(expected_slots_exact)
    assert result.slots == expected_slots
    assert result.model_version == model.version


def test_recommend_capacity_slots_is_smallest_capacity_within_knee_tolerance_of_floor():
    region = RegionEstimate(name="HLA", chromosome="6", variants=166_052, individuals=1153)
    model = DEFAULT_PERFORMANCE_MODEL
    result = recommend_capacity([region], 3, model)

    floor = sum(result.stage_span.values())
    limit = floor * (1.0 + model.knee_tolerance)

    # slots_exact itself must land at (or just past) the tolerance boundary.
    assert result.predicted_makespan(result.slots_exact) == pytest.approx(limit, rel=1e-6)
    # One slot fewer must fall outside the tolerance band (unless already at
    # the floor of 1 slot).
    if result.slots_exact > 1.0:
        assert result.predicted_makespan(result.slots_exact - 1.0) > limit
    # slots is the ceiling of slots_exact, and achieves a makespan at or
    # below the tolerance boundary.
    assert result.slots == max(1, math.ceil(result.slots_exact))
    assert result.predicted_makespan(result.slots) <= limit + 1e-6


def test_recommend_capacity_slots_never_below_one():
    # A minimal single-variant, single-individual region should still
    # recommend at least one slot.
    region = RegionEstimate(name="tiny", chromosome="1", variants=1, individuals=1)
    result = recommend_capacity([region], 1, DEFAULT_PERFORMANCE_MODEL)
    assert result.slots >= 1


# ---------------------------------------------------------------------------
# Capacity.predicted_makespan / predicted_cost
# ---------------------------------------------------------------------------

def _two_stage_capacity() -> Capacity:
    # "individuals" is work-bound below c=25 (W/S=2500/100=25) and span-bound
    # above it. "merge" has W/S=1.0 -- fully span-bound at every c>=1, the
    # Q1 shape described in capacity.py's module docstring -- so it
    # contributes a constant c*300 to cost from c=1 upward.
    stage_work = {"individuals": 2500.0, "merge": 300.0}
    stage_span = {"individuals": 100.0, "merge": 300.0}
    return Capacity(
        slots=4,
        slots_exact=3.5,
        work_seconds=sum(stage_work.values()),
        span_seconds=max(stage_span.values()),
        stage_work=stage_work,
        stage_span=stage_span,
        ind_jobs={"HLA": 26},
        model_version="1.0.0",
        reason="test",
    )


def test_predicted_makespan_sums_per_stage_max_of_work_over_c_and_span():
    capacity = _two_stage_capacity()
    # "individuals" is work-bound at c=2, "merge" is already span-bound;
    # predicted_makespan is their sum, not a single whole-workflow max().
    assert capacity.predicted_makespan(2) == pytest.approx(2500.0 / 2 + 300.0)
    # Above c=25, "individuals" is span-bound too, so the sum is both floors.
    assert capacity.predicted_makespan(30) == pytest.approx(100.0 + 300.0)


def test_predicted_makespan_is_pinned_by_a_fully_serial_stage():
    # A stage with W/S=1.0 (like Q1's merge) keeps contributing its span at
    # every capacity, so the whole prediction can never fall below it --
    # this is the property the old whole-workflow max(W/C, S) formula could
    # not express, since it collapsed every stage into one W and one S.
    capacity = _two_stage_capacity()
    for c in (1, 5, 25, 1000):
        assert capacity.predicted_makespan(c) >= capacity.stage_span["merge"]


def test_predicted_cost_is_c_times_predicted_makespan():
    capacity = _two_stage_capacity()
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


# --- Property 4: predicted_cost is non-decreasing, and floors at total work -
#
# The old whole-workflow max(W/C, S) made cost flat at W below the knee
# C*=W/S. That assumed every stage was work-bound at low C; it does not hold
# once a stage is accumulated separately, because a fully serial stage
# (W_i/S_i=1.0, like Q1's merge -- see capacity.py's module docstring)
# contributes c*S_i from c=1 upward and so raises cost across the whole
# range, not just above a knee. What still holds per stage -- and therefore
# in the sum -- is: predicted_cost(c) = sum_i max(W_i, c*S_i) is
# non-decreasing in c (each max() term is), and it equals total work exactly
# at any c where every stage is work-bound (c <= W_i/S_i for every i).

@pytest.mark.parametrize("set_name", sorted(REGION_SETS))
def test_predicted_cost_is_non_decreasing_in_capacity(set_name):
    triples, populations = REGION_SETS[set_name]
    result = recommend_capacity(_regions(triples), populations, DEFAULT_PERFORMANCE_MODEL)

    costs = [result.predicted_cost(c) for c in range(1, 30)]
    for earlier, later in zip(costs, costs[1:]):
        assert later >= earlier - 1e-6


@pytest.mark.parametrize("set_name", sorted(REGION_SETS))
def test_predicted_cost_equals_total_work_while_every_stage_is_work_bound(set_name):
    triples, populations = REGION_SETS[set_name]
    result = recommend_capacity(_regions(triples), populations, DEFAULT_PERFORMANCE_MODEL)

    # The largest c at which every stage is still work-bound (c <= W_i/S_i
    # for all i) -- below capacity.py's "widest stage" quantity, which is
    # the smallest per-stage W_i/S_i.
    narrowest = min(
        result.stage_work[k] / result.stage_span[k]
        for k in result.stage_work
        if result.stage_span[k] > 0
    )
    below = max(1, math.floor(narrowest))
    for c in range(1, below + 1):
        assert result.predicted_cost(c) == pytest.approx(result.work_seconds, rel=1e-9)


# ---------------------------------------------------------------------------
# M1 gate: Q1 and Q3 per-stage-knee slots, from the region estimates in
# CAPACITY-IMPLEMENTATION-PLAN.md's worked examples (real variant and
# individual counts, not the D-only fixtures used elsewhere in this file).
#
#   Q1 (HLA + BRCA1, P=3): chr6 166,052 variants over 1,653 individuals;
#     chr17 (BRCA1) 2,369 variants over the same 1,653.
#
#   Q3 (HBB + CFTR + APOE, P=5): chr11 (HBB) 136 variants, chr7 (CFTR) 4,391
#     variants, chr19 (APOE) 113 variants, all over 2,480 individuals.
#
# slots is no longer ceil(W/S): it is the smallest capacity whose predicted
# makespan (summed per stage, see Capacity.predicted_makespan) is within
# model.knee_tolerance of the floor set by the per-stage spans. Both regions
# in Q1 and all three in Q3 push individuals/analysis work high relative to
# their spans, so the knee sits well above the old W/S ratio -- 9 for Q1
# (not 4) and 15 for Q3 (not 14). See capacity.py's "Where to stop" comment
# for why the old ceil(W/S) formula undershot: it let a single serial stage
# (merge) drag down a whole-workflow ratio that hid headroom the individuals
# and analysis stages actually had.

def test_q1_hla_brca1_reproduces_section_8_capacity():
    regions = [
        RegionEstimate(name="HLA", chromosome="6", variants=166_052, individuals=1653),
        RegionEstimate(name="BRCA1", chromosome="17", variants=2369, individuals=1653),
    ]
    result = recommend_capacity(regions, populations=3, model=DEFAULT_PERFORMANCE_MODEL)

    assert result.slots == 9


def test_q3_multi_region_reproduces_section_8_capacity():
    regions = [
        RegionEstimate(name="HBB", chromosome="11", variants=136, individuals=2480),
        RegionEstimate(name="CFTR", chromosome="7", variants=4391, individuals=2480),
        RegionEstimate(name="APOE", chromosome="19", variants=113, individuals=2480),
    ]
    result = recommend_capacity(regions, populations=5, model=DEFAULT_PERFORMANCE_MODEL)

    assert result.slots == 15


# ---------------------------------------------------------------------------
# Validation against real measurements: the property that actually matters
# is not the arithmetic but whether predicted_makespan tracks measured
# runs. Q1's makespan was measured on a 16-core host with ind_jobs=38 (the
# policy default, not J* -- see optimal_ind_jobs) at three capacities.
# ---------------------------------------------------------------------------

def test_predicted_makespan_is_within_20_percent_of_measured_q1_runs():
    regions = [
        RegionEstimate(name="HLA", chromosome="6", variants=166_052, individuals=1653),
        RegionEstimate(name="BRCA1", chromosome="17", variants=2369, individuals=1653),
    ]
    result = recommend_capacity(regions, populations=3, model=DEFAULT_PERFORMANCE_MODEL)

    # Measured on a 16-core host, ind_jobs=38. predicted_makespan under-
    # predicts every one of these -- it models neither the contention that
    # inflates per-task time at high capacity nor the scheduling stalls
    # that appear at low capacity (see Capacity.predicted_makespan's
    # docstring) -- but it stays within 20% throughout.
    measured = {4: 1032.0, 7: 785.0, 15: 802.0}
    for c, measured_seconds in measured.items():
        predicted = result.predicted_makespan(c)
        assert predicted <= measured_seconds
        assert predicted == pytest.approx(measured_seconds, rel=0.20)
