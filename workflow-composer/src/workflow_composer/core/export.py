"""
Workflow format converters.

``to_wfcommons`` moved to ``backends/wfcommons/exporter.py`` (RFC-004 section
4.2) and is re-exported here for existing importers. ``convert_workflow``
stays here: it is the engine-neutral dispatcher between output formats, not
part of any one backend.
"""
from __future__ import annotations

from ..backends.wfcommons.exporter import to_wfcommons
from .models import OutputFormat

__all__ = ["convert_workflow", "to_wfcommons"]


def convert_workflow(hyperflow_workflow: dict, target_format: OutputFormat) -> dict:
    """Convert HyperFlow workflow to target format.

    Args:
        hyperflow_workflow: HyperFlow JSON from generator.py
        target_format: Target format

    Returns:
        Workflow in target format
    """
    match target_format:
        case OutputFormat.HYPERFLOW:
            return hyperflow_workflow  # No conversion needed
        case OutputFormat.WFCOMMONS:
            return to_wfcommons(hyperflow_workflow)
        case _:
            raise ValueError(f"Unsupported format: {target_format}")
