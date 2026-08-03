"""
The HyperFlow backend: emits native HyperFlow workflow JSON.

Registers itself in the backend registry (``backends.register``) on import,
so importing this package -- directly, or transitively via
``core.generator``'s re-export shim -- is enough to make ``"hyperflow"``
resolvable through ``backends.get_backend``.

See RFC-004 section 4.2.
"""
from __future__ import annotations

from .. import register
from ..base import EngineReserve, LaunchSpec
from ...core.parallelism import Parallelism

__all__ = ["HyperFlowBackend"]


class HyperFlowBackend:
    """`Backend` implementation for the native HyperFlow generator."""

    name = "hyperflow"
    skill_fragment = "hyperflow.md"

    def reserve(self) -> EngineReserve:
        """Cores and host memory the HyperFlow engine holds back.

        Every shipped `ComputeEnvironment` profile sets
        `engine_reserve=1` and `host_reserve_mb=2048`
        (`core/environment.py`); this backend reports that same reserve.
        """
        return EngineReserve(
            cores=1,
            host_mb=2048,
            rationale=(
                "cores reserved for the HyperFlow engine, redis, and "
                "the merge step"
            ),
        )

    def materialize(self, intent, measurements, resolution: Parallelism) -> LaunchSpec:
        """Not yet wired -- HyperFlow workflows are still produced by
        `core.planner.plan_workflow` calling `generate_workflow` directly.
        Routing that call through this method is out of scope for RFC-004
        section 4.2, which only moves the generator behind the protocol and
        registers this backend's `reserve()`.
        """
        raise NotImplementedError(
            "HyperFlowBackend.materialize is not yet wired; "
            "core.planner.plan_workflow calls generate_workflow directly."
        )


register(HyperFlowBackend())
