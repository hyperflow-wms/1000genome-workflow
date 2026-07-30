"""
Pydantic models defining the workflow-composer contracts.
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal
from enum import Enum


# ============================================================================
# Input Models (Research Intent)
# ============================================================================

class GenomicRegion(BaseModel):
    """A specific genomic region with coordinates."""
    name: str                           # e.g., "HLA", "BRCA1"
    chromosome: str                     # e.g., "6", "17"
    start: int                          # 1-based start
    end: int                            # 1-based end (inclusive)
    context: str | None = None          # e.g., "immune function"


class ResearchIntent(BaseModel):
    """Structured interpretation of a research question."""
    analysis_type: Literal[
        "single_population",
        "population_comparison",
        "multi_population",
        "region_analysis"
    ]
    populations: list[str]              # e.g., ["EUR", "AFR"]
    chromosomes: list[str] | None = None  # e.g., ["6", "22"] or None for all
    regions: list[GenomicRegion] | None = None
    focus: Literal["all_variants", "deleterious", "common", "rare"] = "all_variants"
    clarification_needed: bool = False
    clarification_reason: str | None = None


# ============================================================================
# Data Preparation Models
# ============================================================================

class DataPrepAction(str, Enum):
    DOWNLOAD = "download"
    EXTRACT_REGION = "extract_region"
    SUBSET_POPULATION = "subset_population"
    INDEX = "index"


class DataPrepStep(BaseModel):
    """A single data preparation step."""
    action: DataPrepAction
    source: str | None = None           # VCF file URL
    annotation_source: str | None = None  # Annotation VCF URL
    region: str | None = None           # e.g., "6:28477797-33448354"
    population: str | None = None       # e.g., "EUR"
    input_file: str | None = None       # Local input file
    output_file: str                    # VCF output file name
    output_annotation: str | None = None  # Annotation output file name
    commands: list[str] = []            # Ready-to-execute shell commands


class DataPreparationPlan(BaseModel):
    """Complete data preparation specification."""
    source_type: Literal["s3", "gcs", "ftp"]
    base_url: str
    steps: list[DataPrepStep]
    estimated_transfer_mb: float  # Compressed network transfer
    estimated_disk_mb: float = 0.0  # Uncompressed on-disk size
    use_remote_extraction: bool = False  # True if using tabix remote


# ============================================================================
# Output Format
# ============================================================================

class OutputFormat(str, Enum):
    HYPERFLOW = "hyperflow"
    WFCOMMONS = "wfcommons"
    # Future: PEGASUS = "pegasus", MAKEFLOW = "makeflow"


# ============================================================================
# Execution Hints
# ============================================================================

class ExecutionHints(BaseModel):
    """Structured guidance for execution agents.

    ``max_parallelism``, ``est_peak_mb``, and ``parallelism_reason`` come
    straight from ``core.parallelism.recommend_parallelism``: the global concurrency dial
    (``HF_VAR_REDIS_CMD_MAX_PARALLELISM``), the per-task memory estimate it
    was sized against, and the one-line explanation of which constraint
    bound. Recording all three next to ``recommended_parallelism`` is what
    keeps a plan self-describing -- otherwise a run
    where ``plan.json`` recorded one parallelism value while the harness
    used another, and nothing in the plan surfaced the mismatch.
    """
    prefer_remote_extraction: bool = True
    parallel_population_analysis: bool = True
    estimated_memory_per_task_gb: float = 2.0
    recommended_parallelism: int = 10
    max_parallelism: int = 10
    est_peak_mb: int = 0
    parallelism_reason: str = ""


# ============================================================================
# Output Model (Complete Plan)
# ============================================================================

class WorkflowPlan(BaseModel):
    """Complete output of workflow-composer."""

    # For human review
    description: str                    # What the workflow does
    rationale: str                      # Why these choices were made

    # For execution (authoritative)
    data_preparation: DataPreparationPlan
    workflow: dict                      # HyperFlow JSON (from generator.py)
    output_format: OutputFormat = OutputFormat.HYPERFLOW
    execution_hints: ExecutionHints

    # Metadata
    parameters_used: dict               # Input parameters (reproducibility)
    estimated_runtime_minutes: int
    estimated_storage_gb: float

    # Statistics
    task_count: int
    file_count: int
