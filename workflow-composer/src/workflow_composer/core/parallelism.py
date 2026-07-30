"""
Resource-aware parallelism recommendation.

RFC-003 section 7 item 1: the single mechanism that replaces both the
region-span preset in ``planner.calculate_ind_jobs`` and the vCPU-only
formula in the RFC-002 section 5 test harness. See RFC-003 sections 4.1
(cost model), 4.2 (two dials), 4.3 (formula), 4.4 (worked examples), 4.5
(multi-chromosome runs), and 5 (reason string format).

Nothing calls ``recommend_parallelism`` yet; wiring it into the planner,
generator, and test harness is tracked separately by RFC-003 section 7
items 2-5.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Parallelism:
    """Both dials plus the estimate and reasoning behind them.

    Returning ind_jobs and max_parallelism together (RFC-003 section 4.2)
    means a caller cannot set one without seeing the other -- a task count
    that looks safe in isolation can still hide an unsafe concurrency.
    """
    ind_jobs: int            # tasks per chromosome
    max_parallelism: int     # global concurrency (HF_VAR_REDIS_CMD_MAX_PARALLELISM)
    est_peak_mb: int         # per-task estimate from RFC-003 section 4.1
    binding: str             # "cores" | "memory" | "min_work"
    reason: str              # RFC-003 section 5 one-line string


_BINDING_LABEL = {
    "cores": "core",
    "memory": "memory",
    "min_work": "min_work",
}


def format_parallelism_reason(
    *,
    ind_jobs: int,
    max_parallelism: int,
    binding: str,
    variants: int,
    individuals: int,
    cores: int,
    est_peak_mb: int,
) -> str:
    """Render the RFC-003 section 5 one-line parallelism reason.

    The single formatting helper for that line (RFC-003 section 7 item 5):
    every place that reports ``ind_jobs``/``max_parallelism`` --
    ``recommend_parallelism`` itself, the planner's explicit-``ind_jobs``
    override, the generator's hint-vs-clamped-effective-value report, the
    CLI, and the MCP server -- renders through this function, so there is
    exactly one f-string producing the section 5 format in the tree. That
    matters because section 5 requires both dials to always appear
    together: a caller that reports ``ind_jobs`` without ``max_parallelism``
    (or vice versa) can look safe while hiding an unsafe concurrency, and a
    second, independently-typed format string is exactly how that
    invariant silently breaks.

    Args:
        ind_jobs: the value actually in effect -- the post-clamp value when
            a caller has one, never the pre-clamp hint (RFC-003 section
            1.1's ``plan.json`` vs. harness divergence is this line stating
            a number nothing actually ran with).
        max_parallelism: the paired concurrency dial (RFC-003 section 4.2).
        binding: one of ``"cores"``, ``"memory"``, ``"min_work"`` (the
            ``Parallelism.binding`` values); rendered as ``"core-bound"``,
            ``"memory-bound"``, ``"min_work-bound"``.
        variants: V, rendered with thousands separators.
        individuals: I.
        cores: C, the vCPU count after reserving for the engine/redis/merge.
        est_peak_mb: the per-task memory estimate the dials were sized
            against.

    Returns:
        Exactly: ``"ind_jobs=<n> max_parallelism=<n> (<binding>-bound; "
        "V=<v,> I=<i> C=<c> est_peak=<mb>MB/task)"``.
    """
    return (
        f"ind_jobs={ind_jobs} max_parallelism={max_parallelism} "
        f"({_BINDING_LABEL[binding]}-bound; "
        f"V={variants:,} I={individuals} C={cores} est_peak={est_peak_mb}MB/task)"
    )


def recommend_parallelism(
    variants: int,
    individuals: int,
    vcpus: int,
    host_mem_mb: int,
    chromosomes: int = 1,
    mem_budget_mb: int = 512,
    engine_reserve: int = 1,
    host_reserve_mb: int = 2048,
    min_work: float = 1e7,
) -> Parallelism:
    """Recommend ind_jobs and max_parallelism for an individuals-stage run.

    Implements the formula in RFC-003 section 4.3 exactly:

        work_per_task = clamp(V*I/C, min_work, max_work)   # work = rows * individuals
        rows_per_task = work_per_task / I
        ind_jobs      = ceil(V / rows_per_task)
        est_peak_mb   = 12 + 1.2 * work_per_task / 1e6      # section 4.1, inverted below
        concurrency   = min(ind_jobs*chromosomes, C, floor((host_mem_mb-host_reserve_mb)/est_peak_mb))

        C        = vcpus - engine_reserve
        max_work = (mem_budget_mb - 12) * 1e6 / 1.2

    Args:
        variants: V, actual row_count of the input (not bp span).
        individuals: I, individual count after population filtering.
        vcpus: vCPUs available on the target host.
        host_mem_mb: total host memory in MB.
        chromosomes: number of chromosomes running concurrently. ind_jobs is
            computed per chromosome (RFC-003 section 4.5); max_parallelism is
            the global concurrency shared across all chromosomes in flight,
            not ind_jobs multiplied by chromosomes.
        mem_budget_mb: memory ceiling for a single task.
        engine_reserve: cores reserved for the HyperFlow engine, redis, and
            the merge step.
        host_reserve_mb: host memory reserved for the OS and everything that
            is not an individuals-stage task.
        min_work: floor on rows*individuals per task, so a task's useful
            work is not dwarfed by its fixed per-task cost (RFC-003 section 8).

    Returns:
        A frozen Parallelism with both dials, the peak-memory estimate, the
        binding constraint, and a human-readable reason string.

    Raises:
        ValueError: if variants, individuals, vcpus, or host_mem_mb is not
            positive, or if mem_budget_mb is at or below the section 4.1
            model's fixed 12 MB base cost.
    """
    if variants <= 0:
        raise ValueError(f"variants must be positive, got {variants}")
    if individuals <= 0:
        raise ValueError(f"individuals must be positive, got {individuals}")
    if vcpus <= 0:
        raise ValueError(f"vcpus must be positive, got {vcpus}")
    if host_mem_mb <= 0:
        raise ValueError(f"host_mem_mb must be positive, got {host_mem_mb}")
    if mem_budget_mb <= 12:
        # The section 4.1 cost model charges a fixed 12 MB base cost before
        # any work-dependent term. A budget at or below that leaves no room
        # for max_work, which would go negative and drive ind_jobs to 0 --
        # violating the function's own ind_jobs >= 1 invariant.
        raise ValueError(
            f"mem_budget_mb must be greater than 12 (the fixed base cost in "
            f"the section 4.1 cost model), got {mem_budget_mb}"
        )

    # vcpus <= engine_reserve leaves no cores for tasks after reserving the
    # engine/redis/merge; clamp to a single core rather than going to zero
    # or negative.
    cores = max(1, vcpus - engine_reserve)

    # RFC-003 section 4.1 charges 1.2 MB per 1e6 row*individuals of work, so
    # inverting the model to solve for the work that fits mem_budget_mb
    # multiplies by 1e6 -- not divides by 1.2e-3. Getting this backwards
    # caps a task at a few hundred rows and inflates ind_jobs by three
    # orders of magnitude (RFC-003 section 4.3).
    max_work = (mem_budget_mb - 12) * 1e6 / 1.2

    raw_work = variants * individuals / cores
    if raw_work > max_work:
        work_per_task = max_work
        binding = "memory"
    elif raw_work < min_work:
        work_per_task = min_work
        binding = "min_work"
    else:
        work_per_task = raw_work
        binding = "cores"

    rows_per_task = work_per_task / individuals
    ind_jobs = math.ceil(variants / rows_per_task)

    est_peak_mb_exact = 12 + 1.2 * work_per_task / 1e6
    # Truncate (not round) so the reported estimate never overstates what
    # the formula guarantees, and so concurrency, derived from this same
    # integer below, cannot be pushed over the memory budget by rounding up.
    est_peak_mb = int(est_peak_mb_exact)

    mem_cap = (host_mem_mb - host_reserve_mb) // max(1, est_peak_mb)

    # max_parallelism is global (RFC-003 section 4.5): dividing the shared
    # core/memory budget across the chromosomes in flight, rather than
    # applying ind_jobs*chromosomes as if each chromosome got its own
    # concurrency budget, is what keeps a five-chromosome plan from running
    # five times the intended number of concurrent tasks.
    max_parallelism = min(ind_jobs * chromosomes, cores, mem_cap)
    max_parallelism = max(1, max_parallelism)

    reason = format_parallelism_reason(
        ind_jobs=ind_jobs,
        max_parallelism=max_parallelism,
        binding=binding,
        variants=variants,
        individuals=individuals,
        cores=cores,
        est_peak_mb=est_peak_mb,
    )

    return Parallelism(
        ind_jobs=ind_jobs,
        max_parallelism=max_parallelism,
        est_peak_mb=est_peak_mb,
        binding=binding,
        reason=reason,
    )
