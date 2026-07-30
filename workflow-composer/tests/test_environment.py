"""
Tests for ComputeEnvironment and MEMORY_BUDGET_PRESETS.
"""
from __future__ import annotations

import pytest

from workflow_composer.core.environment import (
    MEMORY_BUDGET_PRESETS,
    ComputeEnvironment,
    check_budget_consistency,
    recommend_for_environment,
)
from workflow_composer.core.parallelism import recommend_parallelism


# ---------------------------------------------------------------------------
# Acceptance criterion 1: MEMORY_BUDGET_PRESETS is memory-only
# ---------------------------------------------------------------------------

def test_memory_budget_presets_medium_is_512():
    assert MEMORY_BUDGET_PRESETS["medium"] == 512


def test_memory_budget_presets_has_small_medium_large():
    assert set(MEMORY_BUDGET_PRESETS) == {"small", "medium", "large"}


def test_memory_budget_presets_values_are_ints():
    for name, value in MEMORY_BUDGET_PRESETS.items():
        assert isinstance(value, int), f"{name} preset is not an int mem budget"


def test_memory_budget_presets_do_not_carry_machine_fields():
    """Presets must not bundle vcpus/host_mem_mb/C.

    A preset is a dict-of-dicts only if someone adds machine fields to it;
    today each entry is a bare int, which already satisfies this, but the
    test pins the *shape* so a future edit that turns an entry into a dict
    carrying vcpus/host_mem_mb/cores is caught here rather than discovered
    downstream.
    """
    machine_keys = {"vcpus", "host_mem_mb", "cores", "c", "C", "core_count"}
    for name, value in MEMORY_BUDGET_PRESETS.items():
        assert not isinstance(value, dict), (
            f"preset '{name}' is a dict, not a bare memory budget: {value!r}"
        )
        # Belt on top of the isinstance check above: even if a future
        # version stores metadata in a non-dict container, none of its
        # attributes should be a machine-describing key.
        if hasattr(value, "__dict__"):
            assert not (machine_keys & set(vars(value))), (
                f"preset '{name}' carries a machine-describing field: {value!r}"
            )


# ---------------------------------------------------------------------------
# resolve("local") reproduces the first worked example
# ---------------------------------------------------------------------------

def test_resolve_local_reproduces_section_4_4_first_row():
    env = ComputeEnvironment.resolve("local")
    result = recommend_parallelism(
        variants=166_052,
        individuals=1153,
        vcpus=env.vcpus,
        host_mem_mb=env.host_mem_mb,
        mem_budget_mb=env.mem_budget_mb,
        engine_reserve=env.engine_reserve,
        host_reserve_mb=env.host_reserve_mb,
    )
    assert result.ind_jobs == 15
    assert result.max_parallelism == 15
    assert result.est_peak_mb == 27


def test_resolve_local_has_16_vcpus_and_31gb_host():
    env = ComputeEnvironment.resolve("local")
    assert env.vcpus == 16
    assert env.host_mem_mb == 31744
    assert env.mem_budget_mb == 512
    assert env.engine_reserve == 1


# ---------------------------------------------------------------------------
# Acceptance criterion 3: explicit argument > env var > shipped profile
# ---------------------------------------------------------------------------

def test_explicit_argument_beats_shipped_profile():
    env = ComputeEnvironment.resolve("local", vcpus=32)
    assert env.vcpus == 32


def test_env_var_beats_shipped_profile(monkeypatch):
    monkeypatch.setenv("G1KWF_VCPUS", "4")
    env = ComputeEnvironment.resolve("local")
    assert env.vcpus == 4


def test_explicit_argument_beats_env_var(monkeypatch):
    monkeypatch.setenv("G1KWF_VCPUS", "4")
    env = ComputeEnvironment.resolve("local", vcpus=32)
    assert env.vcpus == 32


def test_explicit_argument_beats_env_var_all_three_fields(monkeypatch):
    monkeypatch.setenv("G1KWF_VCPUS", "2")
    monkeypatch.setenv("G1KWF_HOST_MEM_MB", "1024")
    monkeypatch.setenv("G1KWF_MEM_BUDGET_MB", "64")
    env = ComputeEnvironment.resolve(
        "local", vcpus=32, host_mem_mb=65536, mem_budget_mb=1024
    )
    assert env.vcpus == 32
    assert env.host_mem_mb == 65536
    assert env.mem_budget_mb == 1024


def test_no_override_uses_shipped_profile(monkeypatch):
    monkeypatch.delenv("G1KWF_VCPUS", raising=False)
    env = ComputeEnvironment.resolve("local")
    assert env.vcpus == 16


def test_unknown_override_field_raises_type_error():
    with pytest.raises(TypeError):
        ComputeEnvironment.resolve("local", cores=4)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Acceptance criterion 4: check_budget_consistency
# ---------------------------------------------------------------------------

def test_check_budget_consistency_raises_when_task_cannot_fit_host():
    env = ComputeEnvironment(
        name="broken",
        vcpus=16,
        host_mem_mb=2048,
        engine_reserve=1,
        host_reserve_mb=2048,
        mem_budget_mb=512,
    )
    with pytest.raises(ValueError, match="mem_budget_mb"):
        check_budget_consistency(env)


def test_check_budget_consistency_raises_when_no_cores_remain():
    env = ComputeEnvironment(
        name="broken",
        vcpus=1,
        host_mem_mb=31744,
        engine_reserve=1,
        host_reserve_mb=2048,
        mem_budget_mb=512,
    )
    with pytest.raises(ValueError, match="vcpus"):
        check_budget_consistency(env)


def test_check_budget_consistency_passes_for_reference_environment():
    env = ComputeEnvironment.resolve("local")
    check_budget_consistency(env)  # must not raise


def test_check_budget_consistency_passes_for_aws_and_gcp_profiles():
    check_budget_consistency(ComputeEnvironment.resolve("aws"))
    check_budget_consistency(ComputeEnvironment.resolve("gcp"))


# ---------------------------------------------------------------------------
# Acceptance criterion 5: recommend_for_environment matches recommend_parallelism
# ---------------------------------------------------------------------------

def test_recommend_for_environment_matches_direct_call():
    env = ComputeEnvironment.resolve("local")
    via_env = recommend_for_environment(variants=166_052, individuals=1153, env=env)
    direct = recommend_parallelism(
        variants=166_052,
        individuals=1153,
        vcpus=env.vcpus,
        host_mem_mb=env.host_mem_mb,
        mem_budget_mb=env.mem_budget_mb,
        engine_reserve=env.engine_reserve,
        host_reserve_mb=env.host_reserve_mb,
    )
    assert via_env == direct


def test_recommend_for_environment_forwards_chromosomes():
    env = ComputeEnvironment.resolve("local")
    via_env = recommend_for_environment(
        variants=166_052, individuals=1153, env=env, chromosomes=5
    )
    direct = recommend_parallelism(
        variants=166_052,
        individuals=1153,
        vcpus=env.vcpus,
        host_mem_mb=env.host_mem_mb,
        mem_budget_mb=env.mem_budget_mb,
        engine_reserve=env.engine_reserve,
        host_reserve_mb=env.host_reserve_mb,
        chromosomes=5,
    )
    assert via_env == direct


# ---------------------------------------------------------------------------
# Acceptance criterion 6: unknown environment name
# ---------------------------------------------------------------------------

def test_unknown_environment_name_raises_value_error_listing_known_names():
    with pytest.raises(ValueError) as excinfo:
        ComputeEnvironment.resolve("azure")
    message = str(excinfo.value)
    assert "azure" in message
    for known in ("local", "aws", "gcp"):
        assert known in message


# ---------------------------------------------------------------------------
# Shipped profiles sanity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["local", "aws", "gcp"])
def test_shipped_profiles_resolve_without_error(name):
    env = ComputeEnvironment.resolve(name)
    assert env.name == name
    assert env.vcpus > 0
    assert env.host_mem_mb > 0
    assert env.mem_budget_mb > 0


def test_compute_environment_is_frozen():
    env = ComputeEnvironment.resolve("local")
    with pytest.raises(Exception):
        env.vcpus = 99  # type: ignore[misc]
