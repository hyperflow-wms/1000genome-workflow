"""
WfCommons export: converts HyperFlow workflow JSON to the WfCommons
workflow-trace format. Not an execution engine, so it has no `Backend`
registration -- see `core/export.py:convert_workflow` for the dispatcher
that reaches it.

See RFC-004 section 4.2.
"""
from __future__ import annotations

from .exporter import to_wfcommons

__all__ = ["to_wfcommons"]
