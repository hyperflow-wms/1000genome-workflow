"""
Capacity planning: how many slots a workflow's own shape asks for.

``recommend_capacity`` turns a set of per-region variant estimates plus a
population count into a deterministic recommendation, computed before any
data is downloaded and before ``recommend_parallelism`` sizes a single task.
See ``CAPACITY-IMPLEMENTATION-PLAN.md`` section 2 and
``RFC-006-REVIEW.md`` section 8 for the derivation this module implements;
what follows is the shape of the model, not a restatement of the coefficient
table.

DAG shape
---------
Each region ``r`` is an independent branch: ``individuals`` (``J_r`` chunked
tasks) feeds ``individuals_merge`` (one task), which feeds ``sifting`` (one
task, omitted below -- see ``PerformanceModel``'s docstring), which feeds
``mutation_overlap`` and ``frequency`` (``P`` tasks each, one per
population). Extraction completes every region's VCF before execution
starts, so all regions' branches start together at ``t = 0``.

Work sums, span takes the maximum
----------------------------------
Because the branches are independent and start together, the *total work*
across regions is additive -- every task, on every branch, consumes real
compute time regardless of what else is running -- while the *wall-clock
span* is set by whichever single branch takes longest, not by their sum. A
region that finishes early does not shorten the run; a region that finishes
late does not lengthen any other region's own branch. So:

    W = sum_r work_r        (total compute, vCPU-seconds if C were 1)
    S = max_r span_r        (wall time, assuming enough slots to run every
                              region's tasks without queuing on each other)

``C* = max(1, ceil(W / S))`` is then the smallest capacity that achieves
makespan ``S`` -- see "the two regimes" below for why smaller is better
whenever it is achievable.

J* first, then W and S at J*
-----------------------------
``span_r`` depends on how the region is chunked (``J_r``), so ``C*`` cannot
be computed before ``J`` is known -- and ``J`` must not be derived from
``C``, or the definition is circular. It doesn't need to be: ``J_r`` sits on
a trade-off contained entirely within the region's own span, independent of
capacity. Finer chunking shrinks the per-chunk individuals cost
(``b_ind * D_r / J_r``, falling in ``J``) but grows the merge overhead
(``c_merge * J_r``, rising in ``J``). Differentiating their sum with respect
to ``J`` and setting the derivative to zero:

    d/dJ [ b_ind*D/J + c_merge*J ] = -b_ind*D/J^2 + c_merge = 0
    =>  J* = sqrt(b_ind * D / c_merge)

This term has no ``C`` in it, which is what makes the whole computation
acyclic: ``J*`` per region, then ``W`` and ``S`` evaluated at ``J*``, then
``C* = W/S``. See ``optimal_ind_jobs`` for the implementation and its
docstring for the same derivation in code-adjacent form.

The two regimes (section 2.2)
------------------------------
With ``T(C) = max(W/C, S)``, cost ``= C * T(C)`` behaves differently on
either side of ``C* = W/S``:

    C <= W/S   (work-bound)   T = W/C    cost = W          constant
    C >  W/S   (span-bound)   T = S      cost = C*S         linear in C

Below the knee, adding slots only buys time, for free -- the total cost
stays at ``W`` no matter how few slots are used, because fewer slots simply
take proportionally longer. Above the knee, the makespan cannot improve
(it is already at the floor ``S``), so every extra slot is paid for with
nothing in return. ``C*`` is therefore simultaneously the *cheapest*
capacity achieving the minimum makespan and the *largest* capacity that
still costs only ``W``.

This asymmetry is the reason to round the recommendation down rather than
up. The estimate is computed from pre-extraction variant counts, which the
harness has measured to be wrong by anywhere from +6% to +18% -- so it will
be wrong, and under-provisioning relative to the true ``C*`` costs only
wall-clock time (degrading gracefully to ``W/C``), while over-provisioning
costs money for a makespan that does not improve.

Slots, not vCPUs
-----------------
``Capacity.slots`` is a count of concurrent task slots, not a vCPU
allocation. They are not interchangeable: a deployment may request less
than one full core per worker (the reference chart requests 0.5 vCPU per
worker), so converting slots to a resource quota is a multiplication by
that per-worker request, not an identity. That conversion is the
consuming code's job, not this module's -- see
``CAPACITY-IMPLEMENTATION-PLAN.md`` section 4.2.

Nothing in the composer consumes this module yet. It is a pure function of
region estimates and a ``PerformanceModel``, with no side effects and no
dependency on ``core.parallelism`` or ``core.planner``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .performance_model import DEFAULT_PERFORMANCE_MODEL, PerformanceModel


@dataclass(frozen=True)
class RegionEstimate:
    """One genomic region's pre-extraction size estimate.

    ``variants`` and ``individuals`` are estimates available at plan time,
    before the region's VCF has been extracted -- see
    ``CAPACITY-IMPLEMENTATION-PLAN.md`` section 7 for the measured error on
    those estimates (+6.4% on HLA, +17.6% on BRCA1).
    """

    name: str
    chromosome: str
    variants: int         # V_r
    individuals: int      # I

    @property
    def data_volume(self) -> int:
        """``D_r = V_r * I`` -- the size term every stage cost scales with."""
        return self.variants * self.individuals


def optimal_ind_jobs(
    data_volume: float,
    model: PerformanceModel = DEFAULT_PERFORMANCE_MODEL,
    max_ind_jobs: int | None = None,
) -> int:
    """The chunk count that minimises one region's own span.

    ``span_r`` contains two terms in tension over ``J``: the individuals
    stage's per-chunk cost ``b_ind * D / J`` (falls as chunks get finer) and
    the merge stage's per-archive cost ``c_merge * J`` (rises as chunks get
    finer, because more chunks means more archives to merge). Differentiating
    their sum with respect to ``J`` and setting it to zero:

        d/dJ [ b_ind*D/J + c_merge*J ] = -b_ind*D/J^2 + c_merge = 0
        =>  J* = sqrt(b_ind * D / c_merge)

    This expression has no dependency on capacity ``C``, which is what makes
    the overall computation acyclic: ``J*`` is resolved first, purely from
    the span trade-off, and only then are ``W`` and ``S`` (and so ``C*``)
    evaluated at ``J*`` -- see the module docstring's "J* first" section.

    Args:
        data_volume: ``D_r = V_r * I`` for the region.
        model: the calibrated coefficients; only ``b_ind`` and ``c_merge``
            are used.
        max_ind_jobs: an optional ceiling (e.g. a per-task memory limit);
            ``None`` means no ceiling. The result is always clamped to at
            least 1 regardless.

    Returns:
        ``J*``, rounded to the nearest integer and clamped to
        ``[1, max_ind_jobs]`` when a ceiling is given.
    """
    raw = math.sqrt(model.b_ind * data_volume / model.c_merge)
    j = round(raw)
    j = max(1, j)
    if max_ind_jobs is not None:
        j = min(j, max(1, max_ind_jobs))
    return j


@dataclass(frozen=True)
class Capacity:
    """A deterministic capacity recommendation plus the numbers behind it.

    ``slots`` is the recommendation itself: the smallest number of
    concurrent task slots that achieves the minimum possible makespan for
    this workload, per the two-regime cost argument in the module
    docstring. ``slots_exact`` is the unrounded ``W/S`` the recommendation
    was rounded up from -- kept for callers that want to reason about how
    close to an integer boundary the recommendation sits.

    Units: slots, not vCPUs -- see the module docstring's "Slots, not
    vCPUs" section.
    """

    slots: int                         # C* = max(1, ceil(W/S))
    slots_exact: float                 # W/S before rounding
    work_seconds: float                # W
    span_seconds: float                # S
    ind_jobs: dict[str, int]           # region name -> J*_r
    model_version: str                 # PerformanceModel.version used
    reason: str                        # one line naming slots, W, S, and the spanning region

    def predicted_makespan(self, c: float) -> float:
        """Wall-clock time at capacity ``c``: ``max(W/c, S)``.

        Work-bound below ``C*`` (time falls as ``1/c``); span-bound at or
        above it (time is floored at ``S``, the longest branch).
        """
        return max(self.work_seconds / c, self.span_seconds)

    def predicted_cost(self, c: float) -> float:
        """vCPU-seconds at capacity ``c``: ``c * predicted_makespan(c)``.

        Constant at ``W`` for ``c <= W/S``; linear in ``c`` above it -- see
        the module docstring's "two regimes" section.
        """
        return c * self.predicted_makespan(c)


def recommend_capacity(
    regions: list[RegionEstimate],
    populations: int,
    model: PerformanceModel = DEFAULT_PERFORMANCE_MODEL,
    max_ind_jobs: int | None = None,
) -> Capacity:
    """Recommend a capacity from region estimates and a population count.

    Per region ``r``, with ``D_r = V_r * I`` and ``J_r = J*_r``
    (``optimal_ind_jobs(D_r, model, max_ind_jobs)``):

        work_r = a_ind*J_r + b_ind*D_r
               + b_merge*D_r + c_merge*J_r
               + P*(a_mo + b_mo*D_r)
               + P*(a_fr + b_fr*D_r)

        span_r = (a_ind + b_ind*D_r/J_r)
               + (b_merge*D_r + c_merge*J_r)
               + max(a_mo + b_mo*D_r, a_fr + b_fr*D_r)

        W = sum_r work_r        S = max_r span_r        C* = max(1, ceil(W/S))

    ``sifting`` enters neither sum -- see ``PerformanceModel``'s module
    docstring for why.

    Args:
        regions: one estimate per region; must be non-empty.
        populations: ``P``, the population count; must be positive.
        model: the calibrated coefficients.
        max_ind_jobs: an optional ceiling passed through to
            ``optimal_ind_jobs`` for every region (e.g. a per-task memory
            limit). ``None`` means no ceiling.

    Returns:
        A frozen ``Capacity`` with ``slots``, the exact ``W``/``S``, the
        per-region ``J*``, the model version used, and a one-line reason.

    Raises:
        ValueError: if ``regions`` is empty, if ``populations`` is not
            positive, or if any region has non-positive ``variants`` or
            ``individuals``.
    """
    if not regions:
        raise ValueError("recommend_capacity requires at least one region")
    if populations <= 0:
        raise ValueError(f"populations must be positive, got {populations}")
    for r in regions:
        if r.variants <= 0:
            raise ValueError(
                f"region {r.name!r} must have positive variants, got {r.variants}"
            )
        if r.individuals <= 0:
            raise ValueError(
                f"region {r.name!r} must have positive individuals, "
                f"got {r.individuals}"
            )

    p = populations
    ind_jobs: dict[str, int] = {}
    total_work = 0.0
    max_span = 0.0
    spanning_region = regions[0].name

    for r in regions:
        d = r.data_volume
        j = optimal_ind_jobs(d, model, max_ind_jobs)
        ind_jobs[r.name] = j

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

        total_work += work_r
        if span_r > max_span:
            max_span = span_r
            spanning_region = r.name

    slots_exact = total_work / max_span
    slots = max(1, math.ceil(slots_exact))

    reason = (
        f"slots={slots} (W={total_work:.0f}s S={max_span:.0f}s "
        f"span set by region {spanning_region!r})"
    )

    return Capacity(
        slots=slots,
        slots_exact=slots_exact,
        work_seconds=total_work,
        span_seconds=max_span,
        ind_jobs=ind_jobs,
        model_version=model.version,
        reason=reason,
    )
