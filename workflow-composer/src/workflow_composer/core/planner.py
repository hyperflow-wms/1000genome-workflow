"""
Main planning logic - wraps generator.py and adds metadata.

IMPORTANT: This module does NOT generate workflows itself.
It calls generator.py (the authoritative source) and wraps the output
with descriptions, rationale, and execution hints.
"""
from __future__ import annotations

import logging
import re
from dataclasses import replace
from pathlib import Path

from .models import (
    ResearchIntent,
    WorkflowPlan,
    OutputFormat,
    ExecutionHints,
    DataPreparationPlan,
    CapacityRecommendation,
)
from .generator import generate_workflow, BUNDLED_POPULATIONS_DIR, load_populations
from .data_resolver import (
    create_data_preparation_plan,
    estimate_variant_count,
    CHROMOSOME_VARIANT_COUNT,
)
from .capacity import RegionEstimate, recommend_capacity
from .performance_model import DEFAULT_PERFORMANCE_MODEL
from .environment import ComputeEnvironment, MEMORY_BUDGET_PRESETS, recommend_for_environment
from .parallelism import Parallelism, format_parallelism_reason
from .export import convert_workflow

logger = logging.getLogger(__name__)

# Default paths (can be overridden)
DEFAULT_DATA_CSV = Path(__file__).parent.parent.parent.parent.parent / "workflow-generator" / "data.csv"
DEFAULT_POPULATIONS_DIR = Path(__file__).parent.parent.parent.parent.parent / "workflow-generator" / "data" / "populations"


def generate_description(intent: ResearchIntent, data_plan: DataPreparationPlan, task_count: int) -> str:
    """Generate human-readable description of the workflow."""

    pop_str = " and ".join(intent.populations)

    if intent.analysis_type == "population_comparison":
        analysis_desc = f"compares {pop_str} populations"
    elif intent.analysis_type == "single_population":
        analysis_desc = f"analyzes the {pop_str} population"
    else:
        analysis_desc = f"analyzes {pop_str} populations"

    region_desc = ""
    if intent.regions:
        region_names = [r.name for r in intent.regions]
        region_desc = f" in the {', '.join(region_names)} region(s)"
    elif intent.chromosomes:
        region_desc = f" on chromosome(s) {', '.join(intent.chromosomes)}"

    transfer_desc = ""
    if data_plan.use_remote_extraction:
        transfer_desc = (
            f" Data will be extracted directly using tabix remote queries, "
            f"transferring ~{data_plan.estimated_transfer_mb:.1f} MB compressed "
            f"(~{data_plan.estimated_disk_mb:.0f} MB on disk)."
        )

    focus_desc = ""
    if intent.focus != "all_variants":
        focus_desc = f" The analysis focuses on {intent.focus.replace('_', ' ')}."

    return (
        f"This workflow {analysis_desc}{region_desc} using {task_count} parallel tasks.{focus_desc}{transfer_desc}"
    )


def generate_rationale(intent: ResearchIntent, data_plan: DataPreparationPlan, compute_env: str) -> str:
    """Generate rationale for planning decisions."""

    reasons = []

    # Data source rationale
    reasons.append(
        f"Selected {data_plan.source_type.upper()} data source based on "
        f"'{compute_env}' compute environment for optimal data transfer."
    )

    # Remote extraction rationale
    if data_plan.use_remote_extraction:
        reasons.append(
            "Using tabix remote extraction because specific genomic region(s) "
            "were requested, significantly reducing data transfer."
        )
    else:
        reasons.append(
            "Downloading full VCF file(s) because analysis covers large regions "
            "or entire chromosomes."
        )

    # Parallelization rationale
    if len(intent.populations) > 1:
        reasons.append(
            f"Analysis of {len(intent.populations)} populations "
            "enables concurrent execution of population-specific tasks."
        )

    return " ".join(reasons)


def _num_chromosomes(intent: ResearchIntent) -> int:
    """Count the chromosomes an intent touches.

    Shared by ``estimate_task_counts`` and ``resolve_parallelism`` so the
    concurrency division -- ind_jobs is per chromosome, but max_parallelism
    is global -- uses the same chromosome count that
    drives the task-count estimate. Default (no regions, no explicit
    chromosomes) is the full genome: 22 autosomes + X + Y.
    """
    if intent.regions:
        return len(set(r.chromosome for r in intent.regions))
    elif intent.chromosomes:
        return len(intent.chromosomes)
    else:
        return 24


def _estimate_max_variants_per_chromosome(intent: ResearchIntent) -> int:
    """Estimate V, the per-chromosome variant count ``recommend_parallelism``
    is sized against.

    ``recommend_parallelism``'s ``ind_jobs`` is defined per chromosome: it is the task count for *one* chromosome's individuals
    stage, and ``generate_workflow`` applies that single value identically to
    every chromosome the intent touches. So V here must be a per-chromosome
    figure, never a sum across chromosomes -- summing (regions first, then
    explicit chromosomes, then all 22 autosomes, mirroring
    ``engines/hyperflow/harness/lib/test_framework.py:estimate_total_variants``) feeds
    ``recommend_parallelism`` a V that belongs to no single chromosome. For a
    multi-region/multi-chromosome intent that inflates ind_jobs far past what
    any individual chromosome's real variant count supports, silently
    violating the ``min_work`` floor on every chromosome smaller
    than the sum (reproducible: two 1 Mb regions on chr6 and chr9 sum to
    V=65,832 and recommend ind_jobs=5, but chr9's own 30,303 variants at
    ind_jobs=5 does ~4.0M row*individuals of real work per task, well under
    the 1e7 floor).

    Take the maximum single-chromosome/region estimate touched by the intent
    instead. This sizes ``ind_jobs`` so the *largest* chromosome touched
    lands exactly where an independent ``recommend_parallelism`` call for
    that chromosome alone would land -- memory-safe by construction, since
    every other, smaller touched chromosome then does strictly less real
    work per task under the same ind_jobs (proportional to its smaller V),
    never more. Smaller chromosomes can still land under the ``min_work``
    floor when the touched set spans a wide range (a single global ind_jobs
    cannot satisfy every chromosome's floor and every chromosome's memory
    bound at once -- true per-chromosome ind_jobs values need
    ``generate_workflow`` to accept more than one, which is out of scope
    here), but that failure mode is bounded fragmentation, never memory
    unsafety, and it no longer triggers on ordinary intents the way summing
    did (the same two-region example above now recommends ind_jobs=3 from
    the driving chr6 region, whose own real per-task work is ~7.9M -- within
    the ceil() rounding slop of the 1e7 floor rather than roughly half of it).
    """
    if intent.regions:
        return max(estimate_variant_count(region=r) for r in intent.regions)
    elif intent.chromosomes:
        return max(estimate_variant_count(chromosome=c) for c in intent.chromosomes)
    else:
        return max(CHROMOSOME_VARIANT_COUNT.get(str(i), 3_000_000) for i in range(1, 23))


def estimate_region_volumes(intent: ResearchIntent) -> list[RegionEstimate]:
    """Estimate ``D_r = V_r * I`` per region, for ``recommend_capacity``.

    ``_estimate_max_variants_per_chromosome`` collapses an intent to the
    single largest chromosome because that is what ``recommend_parallelism``
    needs (one global ``ind_jobs``). ``recommend_capacity`` needs the
    opposite shape: one estimate per independent region branch, since its
    ``W = sum_r work_r`` sums real work across every branch rather than
    taking a maximum. This function returns that per-region view without
    changing the existing scalar one.

    Uses the same three-way fallback as ``_estimate_max_variants_per_chromosome``:
    ``intent.regions`` first (one entry per named region, e.g. "HLA",
    "BRCA1"), else ``intent.chromosomes`` (one entry per chromosome, named
    after the chromosome), else all 22 autosomes (one entry each). Each
    entry's ``variants`` comes from ``estimate_variant_count`` unchanged,
    including its 1.2x safety margin, and ``individuals`` from
    ``_estimate_individuals(intent.populations)`` unchanged, including its
    documented over-count -- both fields carry the same estimation error
    into the capacity model as they already carry into ``ind_jobs``.

    These are pre-extraction estimates and known to run high: measured at
    +6.4% on HLA and +17.6% on BRCA1 (see
    ``docs/CAPACITY-IMPLEMENTATION-PLAN.md`` section 7). That is the safe
    direction to be wrong in: section 2.2's cost asymmetry means
    over-estimating ``D`` (and so ``W`` and the recommended ``C*``) risks
    paying for idle slots, while under-estimating risks a longer makespan --
    which is why ``recommend_capacity`` rounds its recommendation down
    rather than up.

    Args:
        intent: structured research intent.

    Returns:
        A non-empty list of ``RegionEstimate``, one per region, per
        chromosome, or 22 for the genome-wide autosomal case. ``individuals``
        is identical across every entry.
    """
    individuals = _estimate_individuals(intent.populations)
    if intent.regions:
        return [
            RegionEstimate(
                name=r.name,
                chromosome=r.chromosome,
                variants=estimate_variant_count(region=r),
                individuals=individuals,
            )
            for r in intent.regions
        ]
    elif intent.chromosomes:
        return [
            RegionEstimate(
                name=c,
                chromosome=c,
                variants=estimate_variant_count(chromosome=c),
                individuals=individuals,
            )
            for c in intent.chromosomes
        ]
    else:
        return [
            RegionEstimate(
                name=str(i),
                chromosome=str(i),
                variants=CHROMOSOME_VARIANT_COUNT.get(str(i), 3_000_000),
                individuals=individuals,
            )
            for i in range(1, 23)
        ]


# Matches core.capacity.recommend_capacity's reason string, which names
# the spanning region as `region {spanning_region!r}` -- e.g.
# "...span set by region 'HLA')". Capacity itself carries no separate
# spanning-region field (see its docstring), so this is the one place
# that reconstructs it, shared by both plan builders below rather than
# duplicated at each call site.
_SPAN_REGION_RE = re.compile(r"span set by region '([^']*)'")


def _recommend_capacity_for_intent(intent: ResearchIntent) -> CapacityRecommendation:
    """Compute the plan's capacity recommendation from scientific intent alone.

    Pure wiring: estimates per-region volumes via ``estimate_region_volumes``
    and hands them to ``recommend_capacity`` with the shipped
    ``DEFAULT_PERFORMANCE_MODEL``. Shared by ``plan_workflow`` and
    ``create_advisory_plan`` so both builders compute the identical
    recommendation for the same intent -- see
    ``docs/CAPACITY-IMPLEMENTATION-PLAN.md`` section 3, workstream B2.

    Nothing here feeds ``resolve_parallelism``, ``generate_workflow``,
    ``ExecutionHints``, or ``parameters_used``: this is milestone M1, which
    only adds the computation and carries it on the plan. See
    ``core.capacity``'s module docstring for what still consumes nothing.

    Args:
        intent: structured research intent.

    Returns:
        A ``CapacityRecommendation`` with the requested slots, the exact
        work/span, the per-region ``J*`` (reported, not applied -- see
        ``CapacityRecommendation``'s docstring), the model version, and the
        one-line reason.
    """
    regions = estimate_region_volumes(intent)
    populations = len(intent.populations) if intent.populations else 7
    capacity = recommend_capacity(regions, populations, DEFAULT_PERFORMANCE_MODEL)

    match = _SPAN_REGION_RE.search(capacity.reason)
    span_region = match.group(1) if match else ""

    return CapacityRecommendation(
        slots=capacity.slots,
        slots_exact=capacity.slots_exact,
        work_seconds=capacity.work_seconds,
        span_seconds=capacity.span_seconds,
        span_region=span_region,
        ind_jobs=capacity.ind_jobs,
        model_version=capacity.model_version,
        reason=capacity.reason,
    )


def _estimate_individuals(populations: list[str] | None) -> int:
    """Estimate I, the individual count after population filtering.

    Sums line counts from the bundled population files in
    ``workflow_composer/data/populations/`` for the requested populations,
    or all seven bundled files (AFR, ALL, AMR, EAS, EUR, GBR, SAS) when no
    population filter is given.

    This over-counts relative to a real ``columns.txt``: individuals appear
    in more than one bundled file (e.g. GBR is a subset of EUR, and ALL is
    every sample), and the bundled files are not intersected with any
    specific VCF's actual sample list. For EUR+AFR the bundled files sum to
    1675 individuals, versus the 1153 samples actually present in the HLA
    region's VCF (``columns.txt``). Over-counting is the safe direction for
    a memory bound: it can only push ``est_peak_mb``
    and ``ind_jobs`` up, never let a task exceed the real per-task memory it
    will actually use.
    """
    pops = populations if populations else load_populations(BUNDLED_POPULATIONS_DIR)
    total = 0
    for pop in pops:
        pop_path = BUNDLED_POPULATIONS_DIR / pop
        if pop_path.exists():
            total += len(pop_path.read_text().split())
    return total


def _resolve_environment(
    compute_environment: str,
    parallelism: str | None,
    vcpus: int | None,
) -> ComputeEnvironment:
    """Resolve the ``ComputeEnvironment`` a caller's ``parallelism``/``vcpus``
    arguments describe.

    Shared by ``resolve_parallelism`` and ``plan_workflow`` so both resolve
    the *same* environment for the same call -- ``plan_workflow`` needs its
    own copy to pass into ``generate_workflow`` for clamping, and a second, independently-resolved environment
    could silently disagree with the one ``resolve_parallelism`` used.

    Raises:
        ValueError: if ``parallelism`` names an unknown preset.
    """
    env_overrides: dict[str, int] = {}
    if vcpus is not None:
        env_overrides["vcpus"] = vcpus
    if parallelism is not None:
        if parallelism not in MEMORY_BUDGET_PRESETS:
            raise ValueError(
                f"Unknown parallelism preset: {parallelism}. "
                f"Valid options: {sorted(MEMORY_BUDGET_PRESETS)}"
            )
        env_overrides["mem_budget_mb"] = MEMORY_BUDGET_PRESETS[parallelism]
    return ComputeEnvironment.resolve(compute_environment, **env_overrides)


def resolve_parallelism(
    intent: ResearchIntent,
    compute_environment: str = "aws",
    parallelism: str | None = None,
    ind_jobs: int | None = None,
    vcpus: int | None = None,
) -> Parallelism:
    """Resolve both parallelism dials for a research intent.

    Replaces the old region-span preset lookup with a single call into ``recommend_for_environment`` /
    ``recommend_parallelism``. No arithmetic on
    ind_jobs happens in this module any more.

    Args:
        intent: structured research intent. Drives V (variants) via
            ``intent.regions``, else ``intent.chromosomes``, else all
            autosomes (see ``_estimate_max_variants_per_chromosome``), and I
            (individuals) via ``intent.populations`` (see
            ``_estimate_individuals``).
        compute_environment: named ``ComputeEnvironment`` profile ("local",
            "aws", "gcp"). Supplies vcpus, host_mem_mb, and the reserve
            terms unless overridden below.
        parallelism: memory-budget preset name ("small", "medium", "large")
            selecting ``mem_budget_mb`` via
            ``environment.MEMORY_BUDGET_PRESETS``. ``None`` keeps the environment profile's default
            budget. An unknown name raises ``ValueError``.
        ind_jobs: explicit task-count override. Honoured as a hint at this
            layer: the returned ``Parallelism.ind_jobs`` is exactly this
            value, though ``max_parallelism`` and ``est_peak_mb`` still
            reflect the computed recommendation for the actual (V, I).
            Clamping an out-of-range hint against the computed safe range
            is the generator's job,
            not this function's.
        vcpus: explicit vCPU override for the compute environment.

    Returns:
        A ``Parallelism`` with both dials, the peak-memory estimate, and a
        reason string.

    Raises:
        ValueError: if ``parallelism`` names an unknown preset, or if
            ``compute_environment`` names an unknown profile.
    """
    env = _resolve_environment(compute_environment, parallelism, vcpus)

    variants = _estimate_max_variants_per_chromosome(intent)
    individuals = _estimate_individuals(intent.populations)
    chromosomes = _num_chromosomes(intent)

    result = recommend_for_environment(
        variants=variants,
        individuals=individuals,
        env=env,
        chromosomes=chromosomes,
    )

    if ind_jobs is not None:
        # Honoured as a hint (see docstring): report it verbatim, but keep
        # the reason string internally consistent. Reporting one dial
        # without the other, or a reason that names a different ind_jobs than
        # the one actually used, is how a plan comes to record a value
        # nothing ran with. Built through the
        # single format_parallelism_reason helper rather
        # than a second, independent f-string.
        cores = max(1, env.vcpus - env.engine_reserve)
        reason = format_parallelism_reason(
            ind_jobs=ind_jobs,
            max_parallelism=result.max_parallelism,
            binding=result.binding,
            variants=variants,
            individuals=individuals,
            cores=cores,
            est_peak_mb=result.est_peak_mb,
        )
        if ind_jobs != result.ind_jobs:
            reason = f"{reason} [explicit ind_jobs hint; recommended={result.ind_jobs}]"
        result = replace(result, ind_jobs=ind_jobs, reason=reason)

    return result


def calculate_ind_jobs(intent: ResearchIntent, parallelism: str | None = None) -> int:
    """Thin wrapper over ``resolve_parallelism`` returning just ``ind_jobs``.

    Kept for callers that only need the task count, not the full
    ``Parallelism`` (both dials plus the memory estimate and reason). Uses
    the "aws" compute environment default, matching this function's
    default; callers that know their target
    machine should call ``resolve_parallelism`` directly instead.

    Args:
        intent: Research intent with scope information
        parallelism: Preset name ("small", "medium", "large") selecting a
            memory budget, or None for the environment's default

    Returns:
        Number of parallel individuals tasks per chromosome
    """
    return resolve_parallelism(intent, parallelism=parallelism).ind_jobs


def estimate_runtime(intent: ResearchIntent, data_plan: DataPreparationPlan, task_count: int) -> int:
    """Estimate workflow runtime in minutes."""
    base_time = 10  # Base overhead

    # Data transfer time (assume 100 MB/min)
    transfer_time = data_plan.estimated_transfer_mb / 100

    # Analysis time based on task count and parallelism
    # Assume 10 concurrent tasks, ~1 min per task
    parallel_factor = 10
    analysis_time = task_count / parallel_factor

    if intent.regions:
        # Small regions are faster
        analysis_time *= 0.3

    return int(base_time + transfer_time + analysis_time)


def estimate_task_counts(
    intent: ResearchIntent,
    ind_jobs: int
) -> tuple[int, int]:
    """Estimate task and file counts without needing actual data files.

    Formula: total_tasks = C × (ind_jobs + 2 + 2P)
    where:
      C = number of chromosomes (default 1 for region-based)
      ind_jobs = resolved task count per chromosome (see resolve_parallelism)
      P = number of populations

    Returns:
        Tuple of (task_count, file_count)
    """
    num_chromosomes = _num_chromosomes(intent)
    num_populations = len(intent.populations) if intent.populations else 7

    # Task count formula: C × (ind_jobs + 1 merge + 1 sift + 2P analysis)
    task_count = num_chromosomes * (ind_jobs + 2 + 2 * num_populations)

    # File count is approximately 2× task count (inputs + outputs)
    file_count = task_count * 2

    return task_count, file_count


def create_advisory_plan(
    intent: ResearchIntent,
    output_format: OutputFormat = OutputFormat.HYPERFLOW,
    compute_environment: str = "aws",
    parallelism: str | None = None,
    ind_jobs: int | None = None,
    vcpus: int | None = None,
) -> WorkflowPlan:
    """Create an advisory workflow plan without requiring actual data files.

    This is designed for MCP server use where we want to describe what a workflow
    would look like without needing data.csv or population files.

    Args:
        intent: Structured research intent
        output_format: Target workflow format
        compute_environment: Target environment (aws, gcp, local)
        parallelism: Memory-budget preset ("small", "medium", "large") or
            None for the environment's default -- see
            ``resolve_parallelism``.
        ind_jobs: Explicit task-count override, honoured as a hint (see
            ``resolve_parallelism``).
        vcpus: Explicit vCPU override for the compute environment.

    Returns:
        WorkflowPlan with estimated counts (workflow field will be empty)
    """
    # Step 1: Create data preparation plan
    data_plan = create_data_preparation_plan(intent, compute_environment)

    # Step 2: Resolve both parallelism dials together
    resolved = resolve_parallelism(
        intent,
        compute_environment=compute_environment,
        parallelism=parallelism,
        ind_jobs=ind_jobs,
        vcpus=vcpus,
    )

    # Step 3: Estimate task counts (without generating)
    task_count, file_count = estimate_task_counts(intent, resolved.ind_jobs)

    # Step 4: Generate descriptions
    description = generate_description(intent, data_plan, task_count)
    rationale = generate_rationale(intent, data_plan, compute_environment)

    # Step 4.5: Capacity recommendation -- computed from scientific intent
    # alone (K, P, per-region estimates), independent of resolved's
    # machine-sized dials. Nothing downstream consumes it yet (M1) -- see
    # _recommend_capacity_for_intent's docstring.
    capacity = _recommend_capacity_for_intent(intent)

    # Emit the effective dials and the reason
    # wherever a workflow is planned, into the log -- not only into the
    # returned plan -- so the value actually recommended is visible even
    # when nobody inspects plan.json.
    logger.info(resolved.reason)
    logger.info(capacity.reason)

    # Step 5: Calculate hints and estimates
    execution_hints = ExecutionHints(
        prefer_remote_extraction=data_plan.use_remote_extraction,
        parallel_population_analysis=len(intent.populations) > 1,
        estimated_memory_per_task_gb=resolved.est_peak_mb / 1024,
        recommended_parallelism=resolved.max_parallelism,
        max_parallelism=resolved.max_parallelism,
        est_peak_mb=resolved.est_peak_mb,
        parallelism_reason=resolved.reason,
    )

    estimated_runtime = estimate_runtime(intent, data_plan, task_count)
    estimated_storage = data_plan.estimated_transfer_mb / 1024 * 2  # Input + output

    return WorkflowPlan(
        description=description,
        rationale=rationale,
        data_preparation=data_plan,
        workflow={},  # Empty - advisory only
        output_format=output_format,
        execution_hints=execution_hints,
        capacity=capacity,
        parameters_used={
            "analysis_type": intent.analysis_type,
            "populations": intent.populations,
            "chromosomes": intent.chromosomes,
            "regions": [r.model_dump() for r in intent.regions] if intent.regions else None,
            "focus": intent.focus,
            "compute_environment": compute_environment,
            # Both dials, the memory estimate, and the reason, kept
            # consistent with execution_hints above -- so
            # plan.json on disk carries the same artefact the log line does.
            "ind_jobs": resolved.ind_jobs,
            "max_parallelism": resolved.max_parallelism,
            "est_peak_mb": resolved.est_peak_mb,
            "parallelism_reason": resolved.reason,
        },
        estimated_runtime_minutes=estimated_runtime,
        estimated_storage_gb=estimated_storage,
        task_count=task_count,
        file_count=file_count
    )


def _effective_parallelism(workflow: dict, resolved: Parallelism) -> Parallelism:
    """Reconcile ``resolved`` with what ``generate_workflow`` actually used.

    ``resolve_parallelism`` sizes ``resolved`` against planner's *estimated*
    V (``_estimate_max_variants_per_chromosome``); ``generate_workflow``
    independently clamps against each chromosome's *exact* ``row_count``
    from ``data.csv`` and is the authoritative
    source (see the module docstring). Recording the estimate instead of
    what the generator actually did is how a plan comes to record a value
    nothing ran with -- a hint that the generator clamped away
    must not survive into ``plan.json`` unchanged.

    When more than one chromosome is touched, each is clamped independently
    against its own row_count and can land on a different effective
    ind_jobs; this picks the entry with the largest ind_jobs, mirroring the
    "largest chromosome drives" convention
    ``_estimate_max_variants_per_chromosome`` already uses for planning V.

    Returns ``resolved`` unchanged if ``generate_workflow`` did not clamp
    (no ``individuals``/``compute_environment`` given, or no chromosomes
    matched -- ``workflow["metadata"]`` absent either way).
    """
    metadata = workflow.get("metadata", {}).get("parallelism", [])
    if not metadata:
        return resolved
    entry = max(metadata, key=lambda m: m["ind_jobs"])
    return replace(
        resolved,
        ind_jobs=entry["ind_jobs"],
        max_parallelism=entry["max_parallelism"],
        est_peak_mb=entry["est_peak_mb"],
        binding=entry["binding"],
        reason=entry["reason"],
    )


def plan_workflow(
    intent: ResearchIntent,
    output_format: OutputFormat = OutputFormat.HYPERFLOW,
    compute_environment: str = "aws",
    data_csv: Path | None = None,
    populations_dir: Path | None = None,
    parallelism: str | None = None,
    ind_jobs: int | None = None,
    vcpus: int | None = None,
) -> WorkflowPlan:
    """Generate complete workflow plan from research intent.

    This is the main entry point for workflow planning.

    Args:
        intent: Structured research intent
        output_format: Target workflow format (hyperflow, wfcommons)
        compute_environment: Target environment (aws, gcp, local)
        data_csv: Path to data.csv (uses default if not provided)
        populations_dir: Path to populations directory (uses default if not provided)
        parallelism: Memory-budget preset ("small", "medium", "large") or
            None for the environment's default -- see
            ``resolve_parallelism``.
        ind_jobs: Explicit task-count override, honoured as a hint (see
            ``resolve_parallelism``).
        vcpus: Explicit vCPU override for the compute environment.

    Returns:
        Complete WorkflowPlan with workflow JSON, metadata, and hints
    """
    # Use defaults if paths not provided
    data_csv = data_csv or DEFAULT_DATA_CSV
    populations_dir = populations_dir or DEFAULT_POPULATIONS_DIR

    # Step 1: Create data preparation plan
    data_plan = create_data_preparation_plan(intent, compute_environment)

    # Step 2: Resolve both parallelism dials together
    resolved = resolve_parallelism(
        intent,
        compute_environment=compute_environment,
        parallelism=parallelism,
        ind_jobs=ind_jobs,
        vcpus=vcpus,
    )

    # Step 3: Determine chromosome filter from regions or explicit chromosomes
    chromosome_filter = None
    if intent.regions:
        # Extract unique chromosomes from specified regions
        chromosome_filter = list(set(r.chromosome for r in intent.regions))
    elif intent.chromosomes:
        chromosome_filter = intent.chromosomes

    # Step 4: Generate workflow using native generator
    # THIS IS THE AUTHORITATIVE SOURCE - generator.py mirrors daxgen.py exactly.
    # Pass individuals/compute_environment so generate_workflow clamps
    # resolved.ind_jobs -- a hint, trusted but clamped --
    # to the memory-safe range for each chromosome's *exact* row_count from
    # data.csv, the same environment resolve_parallelism used above.
    env = _resolve_environment(compute_environment, parallelism, vcpus)
    individuals_count = _estimate_individuals(intent.populations)
    workflow = generate_workflow(
        data_csv=data_csv,
        populations_dir=populations_dir,
        ind_jobs=resolved.ind_jobs,
        chromosome_filter=chromosome_filter,
        population_filter=intent.populations if intent.populations else None,
        individuals=individuals_count,
        compute_environment=env,
    )

    # Record what generate_workflow actually did, not the pre-clamp hint
    # that went in.
    resolved = _effective_parallelism(workflow, resolved)
    logger.info(resolved.reason)

    # Capacity recommendation -- computed from scientific intent alone,
    # independent of resolved's machine-sized dials and of the workflow
    # generate_workflow just built. Nothing downstream consumes it yet
    # (M1) -- see _recommend_capacity_for_intent's docstring.
    capacity = _recommend_capacity_for_intent(intent)
    logger.info(capacity.reason)

    # Step 5: Convert format if needed
    if output_format != OutputFormat.HYPERFLOW:
        workflow = convert_workflow(workflow, output_format)

    # Step 6: Extract statistics
    task_count = len(workflow["processes"])
    file_count = len(workflow["signals"])

    # Step 7: Generate descriptions
    description = generate_description(intent, data_plan, task_count)
    rationale = generate_rationale(intent, data_plan, compute_environment)

    # Step 8: Calculate hints and estimates
    execution_hints = ExecutionHints(
        prefer_remote_extraction=data_plan.use_remote_extraction,
        parallel_population_analysis=len(intent.populations) > 1,
        estimated_memory_per_task_gb=resolved.est_peak_mb / 1024,
        recommended_parallelism=resolved.max_parallelism,
        max_parallelism=resolved.max_parallelism,
        est_peak_mb=resolved.est_peak_mb,
        parallelism_reason=resolved.reason,
    )

    estimated_runtime = estimate_runtime(intent, data_plan, task_count)
    estimated_storage = data_plan.estimated_transfer_mb / 1024 * 2  # Input + output

    return WorkflowPlan(
        description=description,
        rationale=rationale,
        data_preparation=data_plan,
        workflow=workflow,
        output_format=output_format,
        execution_hints=execution_hints,
        capacity=capacity,
        parameters_used={
            "analysis_type": intent.analysis_type,
            "populations": intent.populations,
            "chromosomes": intent.chromosomes,
            "regions": [r.model_dump() for r in intent.regions] if intent.regions else None,
            "focus": intent.focus,
            "compute_environment": compute_environment,
            # Both dials, the memory estimate, and the reason, kept
            # consistent with execution_hints above -- the
            # effective (post-clamp) value, not the pre-clamp hint.
            "ind_jobs": resolved.ind_jobs,
            "max_parallelism": resolved.max_parallelism,
            "est_peak_mb": resolved.est_peak_mb,
            "parallelism_reason": resolved.reason,
        },
        estimated_runtime_minutes=estimated_runtime,
        estimated_storage_gb=estimated_storage,
        task_count=task_count,
        file_count=file_count
    )
