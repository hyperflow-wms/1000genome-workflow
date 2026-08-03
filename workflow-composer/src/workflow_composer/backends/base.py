"""
The backend protocol: what an execution engine must supply to turn a resolved
plan into something runnable.

Both engines make the same parallelism decision -- `core/parallelism.py`'s
`recommend_parallelism` is engine-neutral -- and differ only in how the
resolved values are committed and in the resources each engine itself
reserves before task memory is budgeted. `Backend` is the seam between those
two concerns: `reserve()` supplies the one genuinely engine-specific piece of
resource policy, and `materialize()` turns an intent plus a resolved
`Parallelism` into the files, command and environment needed to launch it.

See RFC-004 section 2.2.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..core.parallelism import Parallelism


@dataclass(frozen=True)
class EngineReserve:
    """Cores and host memory an engine holds back before task budgeting.

    The skill documents the concept -- that an engine, its coordination
    process, and any merge step consume resources of their own -- and the
    backend supplies the number.
    """
    cores: int
    host_mb: int
    rationale: str


@dataclass(frozen=True)
class LaunchSpec:
    """Everything needed to launch a resolved plan on one engine."""
    files: dict[str, str]      # relative path -> content
    command: list[str]
    env: dict[str, str]


class Backend(Protocol):
    """What an execution engine backend must supply.

    `name` identifies the backend in the registry. `skill_fragment` names a
    file under `knowledge/backends/` documenting how to invoke this engine,
    or `None` if the backend has no such fragment.
    """
    name: str
    skill_fragment: str | None

    def reserve(self) -> EngineReserve:
        ...

    def materialize(self, intent, measurements, resolution: Parallelism) -> LaunchSpec:
        ...
