"""
Backend registry: where execution-engine backends register themselves so
callers can look one up by name instead of importing it directly.

See RFC-004 section 4.1.
"""
from __future__ import annotations

from .base import Backend, EngineReserve, LaunchSpec

__all__ = [
    "Backend",
    "EngineReserve",
    "LaunchSpec",
    "register",
    "get_backend",
    "list_backends",
]

_REGISTRY: dict[str, Backend] = {}


def register(backend: Backend) -> None:
    """Register `backend` under its `name`, replacing any prior registration."""
    _REGISTRY[backend.name] = backend


def get_backend(name: str) -> Backend:
    """Look up a registered backend by name.

    Raises:
        ValueError: if `name` is not registered.
    """
    if name not in _REGISTRY:
        raise ValueError(
            f"Unknown backend '{name}'. "
            f"Known backends: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]


def list_backends() -> list[str]:
    """Names of all registered backends, sorted."""
    return sorted(_REGISTRY)
