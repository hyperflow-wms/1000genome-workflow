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
    source: str | None = None           # Input file or URL
    region: str | None = None           # e.g., "6:28477797-33448354"
    population: str | None = None       # e.g., "EUR"
    input_file: str | None = None       # Local input file
    output_file: str                    # Output file name


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
    """Structured guidance for execution agents."""
    prefer_remote_extraction: bool = True
    parallel_population_analysis: bool = True
    estimated_memory_per_task_gb: float = 2.0
    recommended_parallelism: int = 10


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
