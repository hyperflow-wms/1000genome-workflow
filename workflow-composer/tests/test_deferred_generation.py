"""
Tests for the deferred-generation check.

A workflow is planned from an estimated variant count and regenerated once the
exact count is known. Only the individuals stage may change between the two --
its task count is derived from the row count. Any other difference means the
workflow that was reviewed is not the workflow that will run, so the estimate
proved nothing.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tests" / "integration" / "lib"))

from test_framework import compare_estimated_to_final


def wf(**stages):
    """Build a minimal workflow with the given per-stage task counts."""
    procs = []
    for name, count in stages.items():
        procs.extend({"name": name} for _ in range(count))
    return {"processes": procs}


TWO_POPULATIONS = dict(
    individuals_merge=1, sifting=1, mutation_overlap=2, frequency=2
)


def test_repartitioned_individuals_is_accepted():
    """More individuals tasks from a higher exact count is the expected case."""
    r = compare_estimated_to_final(
        wf(individuals=11, **TWO_POPULATIONS),
        wf(individuals=15, **TWO_POPULATIONS),
    )
    assert r["ok"]
    assert r["problems"] == []
    assert (r["estimated_individuals"], r["final_individuals"]) == (11, 15)


def test_fewer_individuals_is_accepted():
    """The exact count can also be lower than the estimate."""
    r = compare_estimated_to_final(
        wf(individuals=20, **TWO_POPULATIONS),
        wf(individuals=8, **TWO_POPULATIONS),
    )
    assert r["ok"], r["problems"]


def test_identical_workflows_are_accepted():
    same = wf(individuals=15, **TWO_POPULATIONS)
    assert compare_estimated_to_final(same, same)["ok"]


def test_changed_population_count_is_rejected():
    """Analysing a different number of populations is not a repartitioning."""
    r = compare_estimated_to_final(
        wf(individuals=11, individuals_merge=1, sifting=1, mutation_overlap=2, frequency=2),
        wf(individuals=11, individuals_merge=1, sifting=1, mutation_overlap=5, frequency=5),
    )
    assert not r["ok"]
    assert any("mutation_overlap" in p for p in r["problems"])
    assert any("frequency" in p for p in r["problems"])


def test_dropped_sifting_is_rejected():
    r = compare_estimated_to_final(
        wf(individuals=11, **TWO_POPULATIONS),
        wf(individuals=11, individuals_merge=1, mutation_overlap=2, frequency=2),
    )
    assert not r["ok"]
    assert any("sifting" in p for p in r["problems"])


def test_dropped_merge_is_rejected():
    r = compare_estimated_to_final(
        wf(individuals=11, **TWO_POPULATIONS),
        wf(individuals=11, sifting=1, mutation_overlap=2, frequency=2),
    )
    assert not r["ok"]
    assert any("individuals_merge" in p for p in r["problems"])


def test_no_individuals_tasks_is_rejected():
    """A final workflow that splits nothing cannot process the input."""
    r = compare_estimated_to_final(
        wf(individuals=11, **TWO_POPULATIONS),
        wf(**TWO_POPULATIONS),
    )
    assert not r["ok"]
    assert any("no individuals tasks" in p for p in r["problems"])


def test_new_stage_is_rejected():
    r = compare_estimated_to_final(
        wf(individuals=11, **TWO_POPULATIONS),
        wf(individuals=11, unexpected_stage=1, **TWO_POPULATIONS),
    )
    assert not r["ok"]
    assert any("unexpected_stage" in p for p in r["problems"])


@pytest.mark.parametrize("est,fin", [(1, 1), (1, 250), (250, 1)])
def test_any_individuals_count_pair_is_accepted(est, fin):
    r = compare_estimated_to_final(
        wf(individuals=est, **TWO_POPULATIONS),
        wf(individuals=fin, **TWO_POPULATIONS),
    )
    assert r["ok"], r["problems"]
