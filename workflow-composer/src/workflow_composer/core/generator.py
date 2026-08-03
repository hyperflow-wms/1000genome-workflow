"""
Re-export shim.

The native HyperFlow workflow generator moved to
``backends/hyperflow/generator.py`` (RFC-004 section 4.2), alongside the
``hyperflow`` backend registration. Kept here so existing importers
(``cli.py``, ``mcp_server.py``, ``core/planner.py``, and the test suite)
keep working unchanged.
"""
from __future__ import annotations

from ..backends.hyperflow.generator import (
    BUNDLED_POPULATIONS_DIR,
    ChromosomeData,
    HyperFlowGenerator,
    clamp_ind_jobs,
    copy_population_files,
    generate_columns_txt,
    generate_workflow,
    load_data_csv,
    load_populations,
    parse_chromosome_number,
    validate_ind_jobs,
)

__all__ = [
    "BUNDLED_POPULATIONS_DIR",
    "ChromosomeData",
    "HyperFlowGenerator",
    "clamp_ind_jobs",
    "copy_population_files",
    "generate_columns_txt",
    "generate_workflow",
    "load_data_csv",
    "load_populations",
    "parse_chromosome_number",
    "validate_ind_jobs",
]
