"""
The Nextflow backend: launches the Nextflow port of the pipeline.

Registers itself in the backend registry (``backends.register``) on import,
so importing this package is enough to make ``"nextflow"`` resolvable
through ``backends.get_backend``.

See RFC-004 section 4.5.
"""
from __future__ import annotations

from .. import register
from .params import (
    NextflowBackend,
    NextflowParams,
    build_command,
    intent_to_params,
    write_extract_csv,
)

__all__ = [
    "NextflowBackend",
    "NextflowParams",
    "build_command",
    "intent_to_params",
    "write_extract_csv",
]

register(NextflowBackend())
