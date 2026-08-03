"""
Tests for the ind_jobs clamp in generate_workflow.

Covers the acceptance criteria:

1. Clamp behaviour: a hint of 250 on the "local" (16-core / 31 GB) compute
   environment with the HLA data.csv row_count of 166052 emits exactly
   recommend_parallelism's ind_jobs (15), not 250; a hint of 1 stays 1; a
   hint above row_count is capped at row_count. A parametrized test over
   hints {1, 10, 50, 250, 100000} asserts the emitted task count never
   implies a per-task estimate above mem_budget_mb.

2. Non-binding equivalence: when the clamp does not touch a caller-supplied
   ind_jobs, the generated task graph (processes/signals/ins/outs) is
   unchanged from calling generate_workflow without the new arguments at
   all -- the clamp mechanism is additive (it only adds a "metadata" key)
   and never perturbs the chunking it did not need to touch.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from workflow_composer.core.environment import ComputeEnvironment
from workflow_composer.core.generator import (
    BUNDLED_POPULATIONS_DIR,
    clamp_ind_jobs,
    generate_workflow,
)
from workflow_composer.core.parallelism import recommend_parallelism

# The preserved HLA baseline: one chromosome (6), row_count=166052,
# 1153 individuals in columns.txt (see engines/hyperflow/harness/
# workflow-eur-afr-hla-baseline/{data.csv,columns.txt}).
BASELINE_DIR = Path(__file__).parent.parent.parent / "engines" / "hyperflow" / "harness" / "workflow-eur-afr-hla-baseline"
HLA_DATA_CSV = BASELINE_DIR / "data.csv"
HLA_ROW_COUNT = 166052
HLA_INDIVIDUALS = 1153

pytestmark = pytest.mark.skipif(
    not HLA_DATA_CSV.exists(), reason="engines/hyperflow/harness/workflow-eur-afr-hla-baseline/data.csv not available"
)


def _generate(ind_jobs: int, **kwargs) -> dict:
    return generate_workflow(
        data_csv=HLA_DATA_CSV,
        populations_dir=BUNDLED_POPULATIONS_DIR,
        ind_jobs=ind_jobs,
        chromosome_filter=["6"],
        population_filter=["EUR", "AFR"],
        **kwargs,
    )


def _individuals_task_count(wf: dict) -> int:
    return len([p for p in wf["processes"] if p["name"] == "individuals"])


# ---------------------------------------------------------------------------
# Acceptance criterion 1: clamp behaviour
# ---------------------------------------------------------------------------

def test_hint_above_recommended_is_clamped_down():
    """A hint of 250 on a 16-core/31 GB environment is clamped to
    recommend_parallelism's own ind_jobs, not honoured as 250."""
    env = ComputeEnvironment.resolve("local")
    expected = recommend_parallelism(
        variants=HLA_ROW_COUNT,
        individuals=HLA_INDIVIDUALS,
        vcpus=env.vcpus,
        host_mem_mb=env.host_mem_mb,
        mem_budget_mb=env.mem_budget_mb,
        engine_reserve=env.engine_reserve,
        host_reserve_mb=env.host_reserve_mb,
    )
    assert expected.ind_jobs == 15  # documented worked example

    wf = _generate(250, individuals=HLA_INDIVIDUALS, compute_environment="local")

    assert _individuals_task_count(wf) == expected.ind_jobs
    assert wf["metadata"]["parallelism"][0]["ind_jobs"] == expected.ind_jobs
    assert wf["metadata"]["parallelism"][0]["ind_jobs_hint"] == 250


def test_hint_of_one_stays_one():
    """The clamp is two-sided: a hint already at the floor is left alone."""
    wf = _generate(1, individuals=HLA_INDIVIDUALS, compute_environment="local")
    assert _individuals_task_count(wf) == 1
    assert wf["metadata"]["parallelism"][0]["ind_jobs"] == 1


def test_hint_above_row_count_is_capped_at_row_count():
    """A hint that exceeds row_count is capped at row_count (the pre-existing
    validate_ind_jobs rule), even when the memory-safe recommendation itself
    exceeds row_count. Engineer that case with a very large individuals
    count and a razor-thin mem_budget_mb: recommend_parallelism's "memory"
    binding then drives rows_per_task below 1, pushing its own ind_jobs past
    row_count -- exactly the case the row_count cap exists to catch.
    """
    huge_individuals = 1_000_000
    tiny_budget_env = ComputeEnvironment.resolve("local", mem_budget_mb=13)
    recommended = recommend_parallelism(
        variants=HLA_ROW_COUNT,
        individuals=huge_individuals,
        vcpus=tiny_budget_env.vcpus,
        host_mem_mb=tiny_budget_env.host_mem_mb,
        mem_budget_mb=tiny_budget_env.mem_budget_mb,
        engine_reserve=tiny_budget_env.engine_reserve,
        host_reserve_mb=tiny_budget_env.host_reserve_mb,
    )
    assert recommended.ind_jobs > HLA_ROW_COUNT, (
        "test setup: recommend_parallelism must recommend more ind_jobs than "
        "row_count for this test to isolate the row_count cap"
    )

    wf = _generate(
        HLA_ROW_COUNT * 10,
        individuals=huge_individuals,
        compute_environment=tiny_budget_env,
    )
    # The clamped, effective ind_jobs (reported in metadata) is exactly
    # row_count. The emitted task count is threshold-1, not threshold, when
    # actual_ind_jobs == threshold: step = ceil(threshold/threshold) = 1, and
    # the counter-walking loop (`while counter < threshold`, unchanged by
    # this task) then visits counter = 1..threshold-1. That off-by-one is a
    # property of the pre-existing chunking arithmetic at this exact
    # boundary, not of the clamp -- see TestRemainderHandling in
    # test_generator.py, which exercises the same loop unchanged.
    assert wf["metadata"]["parallelism"][0]["ind_jobs"] == HLA_ROW_COUNT
    # RFC-005: 0-based ranges emit exactly ind_jobs tasks. The former
    # 1-based start produced one fewer, leaving a variant unprocessed.
    assert _individuals_task_count(wf) == HLA_ROW_COUNT


@pytest.mark.parametrize("hint", [1, 10, 50, 250, 100_000])
def test_emitted_task_count_never_implies_estimate_above_mem_budget(hint):
    """For every hint, the *actual* per-task work implied by the emitted
    chunk boundaries stays within mem_budget_mb -- not just the recommended
    ind_jobs in isolation, but the real step size generate_workflow used."""
    env = ComputeEnvironment.resolve("local")
    wf = _generate(hint, individuals=HLA_INDIVIDUALS, compute_environment="local")

    ind_tasks = [p for p in wf["processes"] if p["name"] == "individuals"]
    starts_stops = [
        (int(p["config"]["executor"]["args"][2]), int(p["config"]["executor"]["args"][3]))
        for p in ind_tasks
    ]
    max_step = max(stop - start for start, stop in starts_stops)

    work = max_step * HLA_INDIVIDUALS
    est_peak_mb = 12 + 1.2 * work / 1e6  # cost model

    assert est_peak_mb <= env.mem_budget_mb, (
        f"hint={hint}: emitted step {max_step} implies {est_peak_mb:.1f} MB/task, "
        f"exceeding the {env.mem_budget_mb} MB budget"
    )


# ---------------------------------------------------------------------------
# Acceptance criterion 2: non-binding equivalence
# ---------------------------------------------------------------------------

def test_clamp_metadata_is_additive_only_when_clamp_does_not_bind():
    """A hint the clamp does not need to touch (already <= the memory-safe
    recommendation) produces the exact same task graph as calling
    generate_workflow without the new arguments at all -- clamping is purely
    additive (a new "metadata" key), never a mutation of existing structure.
    """
    baseline = _generate(1)  # no individuals/compute_environment: old behaviour
    assert "metadata" not in baseline

    # ind_jobs=1 is already <= any memory-safe recommendation, so the clamp
    # cannot bind (see test_hint_of_one_stays_one above).
    clamped = _generate(1, individuals=HLA_INDIVIDUALS, compute_environment="local")

    assert clamped["processes"] == baseline["processes"]
    assert clamped["signals"] == baseline["signals"]
    assert clamped["ins"] == baseline["ins"]
    assert clamped["outs"] == baseline["outs"]

    # The only difference is the additive metadata key this task introduces.
    clamped_without_metadata = {k: v for k, v in clamped.items() if k != "metadata"}
    assert json.dumps(clamped_without_metadata, sort_keys=True) == json.dumps(
        baseline, sort_keys=True
    )


def test_no_individuals_arg_skips_clamping_entirely():
    """Existing callers that never pass individuals/compute_environment see
    ind_jobs used exactly as given (modulo the pre-existing threshold rule),
    with no metadata key -- this is what keeps every pre-existing
    test_generator.py assertion passing unchanged."""
    wf = _generate(250)
    assert _individuals_task_count(wf) == 250
    assert "metadata" not in wf


# ---------------------------------------------------------------------------
# clamp_ind_jobs unit coverage
# ---------------------------------------------------------------------------

def test_clamp_ind_jobs_never_exceeds_recommended():
    env = ComputeEnvironment.resolve("local")
    clamped, recommended = clamp_ind_jobs(100_000, HLA_ROW_COUNT, HLA_INDIVIDUALS, env)
    assert clamped == recommended.ind_jobs


def test_clamp_ind_jobs_never_goes_below_one():
    env = ComputeEnvironment.resolve("local")
    clamped, _ = clamp_ind_jobs(0, HLA_ROW_COUNT, HLA_INDIVIDUALS, env)
    assert clamped == 1
    clamped, _ = clamp_ind_jobs(-5, HLA_ROW_COUNT, HLA_INDIVIDUALS, env)
    assert clamped == 1


def test_clamp_ind_jobs_leaves_in_range_hint_untouched():
    env = ComputeEnvironment.resolve("local")
    _, recommended = clamp_ind_jobs(1, HLA_ROW_COUNT, HLA_INDIVIDUALS, env)
    # Pick a hint strictly inside (1, recommended.ind_jobs) if one exists.
    if recommended.ind_jobs > 2:
        in_range_hint = recommended.ind_jobs - 1
        clamped, _ = clamp_ind_jobs(in_range_hint, HLA_ROW_COUNT, HLA_INDIVIDUALS, env)
        assert clamped == in_range_hint


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
