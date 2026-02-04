"""
Main planning logic - wraps generator.py and adds metadata.

IMPORTANT: This module does NOT generate workflows itself.
It calls generator.py (the authoritative source) and wraps the output
with descriptions, rationale, and execution hints.
"""
from __future__ import annotations

from pathlib import Path

from .models import (
    ResearchIntent,
    WorkflowPlan,
    OutputFormat,
    ExecutionHints,
    DataPreparationPlan,
)
from .generator import generate_workflow, PARALLELISM_PRESETS, DEFAULT_PARALLELISM
from .data_resolver import create_data_preparation_plan
from .export import convert_workflow

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


def calculate_ind_jobs(intent: ResearchIntent, parallelism: str | None = None) -> int:
    """Calculate appropriate number of individuals jobs based on analysis scope.

    Args:
        intent: Research intent with scope information
        parallelism: Preset name ("small", "medium", "large") or None for auto

    Returns:
        Number of parallel individuals tasks per chromosome
    """
    # If explicit preset requested, use it
    if parallelism:
        if parallelism not in PARALLELISM_PRESETS:
            raise ValueError(
                f"Unknown parallelism preset: {parallelism}. "
                f"Valid options: {list(PARALLELISM_PRESETS.keys())}"
            )
        return PARALLELISM_PRESETS[parallelism]

    # Auto-select based on analysis scope
    if intent.regions:
        total_size = sum(r.end - r.start for r in intent.regions)
        if total_size < 1_000_000:  # < 1 Mb
            return PARALLELISM_PRESETS["small"]
        elif total_size < 10_000_000:  # < 10 Mb
            return PARALLELISM_PRESETS["medium"]

    return PARALLELISM_PRESETS[DEFAULT_PARALLELISM]


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
      ind_jobs = parallelism preset (10/50/250)
      P = number of populations

    Returns:
        Tuple of (task_count, file_count)
    """
    # Determine chromosome count
    if intent.regions:
        # For regions, count unique chromosomes
        num_chromosomes = len(set(r.chromosome for r in intent.regions))
    elif intent.chromosomes:
        num_chromosomes = len(intent.chromosomes)
    else:
        # Default to full genome (22 autosomes + X + Y)
        num_chromosomes = 24

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
    parallelism: str | None = None
) -> WorkflowPlan:
    """Create an advisory workflow plan without requiring actual data files.

    This is designed for MCP server use where we want to describe what a workflow
    would look like without needing data.csv or population files.

    Args:
        intent: Structured research intent
        output_format: Target workflow format
        compute_environment: Target environment (aws, gcp, local)
        parallelism: Parallelism preset ("small", "medium", "large") or None for auto

    Returns:
        WorkflowPlan with estimated counts (workflow field will be empty)
    """
    # Step 1: Create data preparation plan
    data_plan = create_data_preparation_plan(intent, compute_environment)

    # Step 2: Calculate appropriate parallelism
    ind_jobs = calculate_ind_jobs(intent, parallelism)

    # Step 3: Estimate task counts (without generating)
    task_count, file_count = estimate_task_counts(intent, ind_jobs)

    # Step 4: Generate descriptions
    description = generate_description(intent, data_plan, task_count)
    rationale = generate_rationale(intent, data_plan, compute_environment)

    # Step 5: Calculate hints and estimates
    execution_hints = ExecutionHints(
        prefer_remote_extraction=data_plan.use_remote_extraction,
        parallel_population_analysis=len(intent.populations) > 1,
        estimated_memory_per_task_gb=2.0,
        recommended_parallelism=min(task_count, 100)
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
        parameters_used={
            "analysis_type": intent.analysis_type,
            "populations": intent.populations,
            "chromosomes": intent.chromosomes,
            "regions": [r.model_dump() for r in intent.regions] if intent.regions else None,
            "focus": intent.focus,
            "compute_environment": compute_environment,
            "ind_jobs": ind_jobs
        },
        estimated_runtime_minutes=estimated_runtime,
        estimated_storage_gb=estimated_storage,
        task_count=task_count,
        file_count=file_count
    )


def plan_workflow(
    intent: ResearchIntent,
    output_format: OutputFormat = OutputFormat.HYPERFLOW,
    compute_environment: str = "aws",
    data_csv: Path | None = None,
    populations_dir: Path | None = None,
    parallelism: str | None = None
) -> WorkflowPlan:
    """Generate complete workflow plan from research intent.

    This is the main entry point for workflow planning.

    Args:
        intent: Structured research intent
        output_format: Target workflow format (hyperflow, wfcommons)
        compute_environment: Target environment (aws, gcp, local)
        data_csv: Path to data.csv (uses default if not provided)
        populations_dir: Path to populations directory (uses default if not provided)
        parallelism: Parallelism preset ("small", "medium", "large") or None for auto

    Returns:
        Complete WorkflowPlan with workflow JSON, metadata, and hints
    """
    # Use defaults if paths not provided
    data_csv = data_csv or DEFAULT_DATA_CSV
    populations_dir = populations_dir or DEFAULT_POPULATIONS_DIR

    # Step 1: Create data preparation plan
    data_plan = create_data_preparation_plan(intent, compute_environment)

    # Step 2: Calculate appropriate parallelism
    ind_jobs = calculate_ind_jobs(intent, parallelism)

    # Step 3: Determine chromosome filter from regions or explicit chromosomes
    chromosome_filter = None
    if intent.regions:
        # Extract unique chromosomes from specified regions
        chromosome_filter = list(set(r.chromosome for r in intent.regions))
    elif intent.chromosomes:
        chromosome_filter = intent.chromosomes

    # Step 4: Generate workflow using native generator
    # THIS IS THE AUTHORITATIVE SOURCE - generator.py mirrors daxgen.py exactly
    workflow = generate_workflow(
        data_csv=data_csv,
        populations_dir=populations_dir,
        ind_jobs=ind_jobs,
        chromosome_filter=chromosome_filter,
        population_filter=intent.populations if intent.populations else None
    )

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
        estimated_memory_per_task_gb=2.0,
        recommended_parallelism=min(task_count, 100)
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
        parameters_used={
            "analysis_type": intent.analysis_type,
            "populations": intent.populations,
            "chromosomes": intent.chromosomes,
            "regions": [r.model_dump() for r in intent.regions] if intent.regions else None,
            "focus": intent.focus,
            "compute_environment": compute_environment,
            "ind_jobs": ind_jobs
        },
        estimated_runtime_minutes=estimated_runtime,
        estimated_storage_gb=estimated_storage,
        task_count=task_count,
        file_count=file_count
    )
