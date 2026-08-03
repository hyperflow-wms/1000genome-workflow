"""
Tests for the backend protocol and registry (RFC-004 section 4.1).
"""
from __future__ import annotations

import pytest

from workflow_composer.backends import get_backend, list_backends, register
from workflow_composer.backends.base import EngineReserve, LaunchSpec
from workflow_composer.core.parallelism import Parallelism


# ---------------------------------------------------------------------------
# EngineReserve / LaunchSpec: field shape
# ---------------------------------------------------------------------------

def test_engine_reserve_fields():
    reserve = EngineReserve(cores=1, host_mb=256, rationale="engine + Redis")
    assert reserve.cores == 1
    assert reserve.host_mb == 256
    assert reserve.rationale == "engine + Redis"


def test_engine_reserve_is_frozen():
    reserve = EngineReserve(cores=1, host_mb=256, rationale="x")
    with pytest.raises(Exception):
        reserve.cores = 2


def test_launch_spec_fields():
    spec = LaunchSpec(
        files={"workflow.json": "{}"},
        command=["hflow", "run"],
        env={"HF_VAR_REDIS_URL": "redis://localhost"},
    )
    assert spec.files == {"workflow.json": "{}"}
    assert spec.command == ["hflow", "run"]
    assert spec.env == {"HF_VAR_REDIS_URL": "redis://localhost"}


def test_launch_spec_is_frozen():
    spec = LaunchSpec(files={}, command=[], env={})
    with pytest.raises(Exception):
        spec.command = ["x"]


# ---------------------------------------------------------------------------
# Registry round-trip with a stub backend
# ---------------------------------------------------------------------------

class _StubBackend:
    """Minimal object satisfying the Backend protocol, for registry tests."""

    name = "stub"
    skill_fragment = None

    def reserve(self) -> EngineReserve:
        return EngineReserve(cores=1, host_mb=128, rationale="stub reserve")

    def materialize(self, intent, measurements, resolution: Parallelism) -> LaunchSpec:
        return LaunchSpec(
            files={"stub.txt": f"ind_jobs={resolution.ind_jobs}"},
            command=["stub", "run"],
            env={},
        )


def test_stub_backend_satisfies_protocol():
    # Backend is a structural Protocol, not decorated @runtime_checkable (per
    # RFC-004 section 2.2), so conformance is checked by attribute shape
    # rather than isinstance().
    stub = _StubBackend()
    assert hasattr(stub, "name") and hasattr(stub, "skill_fragment")
    assert callable(stub.reserve)
    assert callable(stub.materialize)


def test_register_and_get_backend_round_trip():
    stub = _StubBackend()
    register(stub)
    try:
        assert get_backend("stub") is stub
    finally:
        pass


def test_list_backends_includes_registered_stub():
    stub = _StubBackend()
    register(stub)
    assert "stub" in list_backends()


def test_registered_backend_materializes_a_launch_spec():
    stub = _StubBackend()
    register(stub)
    resolution = Parallelism(
        ind_jobs=4,
        max_parallelism=4,
        est_peak_mb=512,
        binding="cores",
        reason="bound by cores",
    )
    spec = get_backend("stub").materialize(intent=None, measurements=None, resolution=resolution)
    assert spec.files == {"stub.txt": "ind_jobs=4"}
    assert spec.command == ["stub", "run"]


# ---------------------------------------------------------------------------
# get_backend: unknown name
# ---------------------------------------------------------------------------

def test_get_backend_unknown_name_raises_clear_error():
    with pytest.raises(ValueError) as exc_info:
        get_backend("does-not-exist")
    message = str(exc_info.value)
    assert "does-not-exist" in message
    assert "Known backends" in message


def test_get_backend_unknown_name_lists_registered_names():
    register(_StubBackend())
    with pytest.raises(ValueError) as exc_info:
        get_backend("does-not-exist")
    assert "stub" in str(exc_info.value)
