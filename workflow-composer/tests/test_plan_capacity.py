"""
Tests for WorkflowPlan.capacity (CAPACITY-IMPLEMENTATION-PLAN.md section 3,
workstreams B1/B2).

Covers:

1. ``plan_workflow`` on the HLA EUR+AFR fixture returns a plan whose
   ``capacity`` is populated, with a sane ``slots``, one ``ind_jobs`` entry
   per touched region, and the shipped model's version.
2. ``plan_workflow`` and ``create_advisory_plan`` agree on ``slots``,
   ``work_seconds`` and ``span_seconds`` for the same intent -- both
   builders must compute the identical recommendation.
3. ``WorkflowPlan`` round-trips through ``model_dump()``/reconstruction
   and ``model_dump_json()`` with a populated ``capacity``.
4. A plan built without a ``capacity`` argument -- every existing
   construction of ``WorkflowPlan`` in the tree -- still validates, with
   ``capacity is None``.
5. The regression check that matters: adding capacity moves no task
   boundary. The same pinned values ``tests/test_observability.py``
   already asserts (``ind_jobs=15``, ``max_parallelism=15``, 15
   ``individuals`` processes) still hold on the same fixture.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from workflow_composer.core.generator import BUNDLED_POPULATIONS_DIR
from workflow_composer.core.models import (
    DataPreparationPlan,
    ExecutionHints,
    GenomicRegion,
    OutputFormat,
    ResearchIntent,
    WorkflowPlan,
)
from workflow_composer.core.performance_model import DEFAULT_PERFORMANCE_MODEL
from workflow_composer.core.planner import create_advisory_plan, plan_workflow

# The preserved HLA baseline, the same fixture tests/test_observability.py
# pins: one chromosome (6), row_count=166052, I=1153 -- 16 vCPUs ->
# ind_jobs=15, max_parallelism=15, core-bound.
BASELINE_DIR = Path(__file__).parent.parent.parent / "engines" / "hyperflow" / "harness" / "workflow-eur-afr-hla-baseline"
HLA_DATA_CSV = BASELINE_DIR / "data.csv"

pytestmark = pytest.mark.skipif(
    not HLA_DATA_CSV.exists(),
    reason="engines/hyperflow/harness/workflow-eur-afr-hla-baseline/data.csv not available",
)

HLA_REGION = GenomicRegion(name="HLA", chromosome="6", start=28477797, end=33448354, context="immune function")


def _hla_eur_afr_intent() -> ResearchIntent:
    return ResearchIntent(
        analysis_type="population_comparison",
        populations=["EUR", "AFR"],
        regions=[HLA_REGION],
    )


# ---------------------------------------------------------------------------
# 1. plan_workflow populates capacity
# ---------------------------------------------------------------------------

def test_plan_workflow_populates_capacity():
    intent = _hla_eur_afr_intent()
    plan = plan_workflow(
        intent, compute_environment="local",
        data_csv=HLA_DATA_CSV, populations_dir=BUNDLED_POPULATIONS_DIR,
    )

    assert plan.capacity is not None
    assert plan.capacity.slots >= 1
    assert plan.capacity.slots_exact > 0
    assert plan.capacity.work_seconds > 0
    assert plan.capacity.span_seconds > 0
    assert plan.capacity.span_region == "HLA"
    # One region touched (HLA on chr6) -> exactly one ind_jobs entry.
    assert set(plan.capacity.ind_jobs) == {"HLA"}
    assert plan.capacity.model_version == DEFAULT_PERFORMANCE_MODEL.version
    assert plan.capacity.reason


# ---------------------------------------------------------------------------
# 2. plan_workflow and create_advisory_plan agree
# ---------------------------------------------------------------------------

def test_plan_workflow_and_advisory_plan_agree_on_capacity():
    intent = _hla_eur_afr_intent()

    full = plan_workflow(
        intent, compute_environment="local",
        data_csv=HLA_DATA_CSV, populations_dir=BUNDLED_POPULATIONS_DIR,
    )
    advisory = create_advisory_plan(intent, compute_environment="local")

    assert full.capacity is not None
    assert advisory.capacity is not None
    assert full.capacity.slots == advisory.capacity.slots
    assert full.capacity.work_seconds == pytest.approx(advisory.capacity.work_seconds)
    assert full.capacity.span_seconds == pytest.approx(advisory.capacity.span_seconds)


# ---------------------------------------------------------------------------
# 3. Round-trips
# ---------------------------------------------------------------------------

def test_workflow_plan_with_capacity_round_trips_through_model_dump():
    intent = _hla_eur_afr_intent()
    plan = plan_workflow(
        intent, compute_environment="local",
        data_csv=HLA_DATA_CSV, populations_dir=BUNDLED_POPULATIONS_DIR,
    )

    restored = WorkflowPlan(**plan.model_dump())
    assert restored.capacity == plan.capacity

    # And model_dump_json() succeeds and round-trips too.
    restored_json = WorkflowPlan.model_validate_json(plan.model_dump_json())
    assert restored_json.capacity == plan.capacity


# ---------------------------------------------------------------------------
# 4. capacity defaults to None; existing constructions stay valid
# ---------------------------------------------------------------------------

def test_workflow_plan_without_capacity_argument_defaults_to_none():
    plan = WorkflowPlan(
        description="d",
        rationale="r",
        data_preparation=DataPreparationPlan(
            source_type="ftp",
            base_url="ftp://example.invalid/",
            steps=[],
            use_remote_extraction=False,
            estimated_transfer_mb=0.0,
            estimated_disk_mb=0.0,
        ),
        workflow={},
        output_format=OutputFormat.HYPERFLOW,
        execution_hints=ExecutionHints(),
        parameters_used={},
        estimated_runtime_minutes=1,
        estimated_storage_gb=0.0,
        task_count=0,
        file_count=0,
    )
    assert plan.capacity is None


# ---------------------------------------------------------------------------
# 5. Regression: adding capacity moves no task boundary.
# Values pinned by tests/test_observability.py on the same fixture.
# ---------------------------------------------------------------------------

def test_capacity_does_not_change_existing_parallelism_dials():
    intent = _hla_eur_afr_intent()
    plan = plan_workflow(
        intent, compute_environment="local",
        data_csv=HLA_DATA_CSV, populations_dir=BUNDLED_POPULATIONS_DIR,
    )

    assert plan.parameters_used["ind_jobs"] == 15
    assert plan.execution_hints.max_parallelism == 15
    individuals_tasks = [p for p in plan.workflow["processes"] if p["name"] == "individuals"]
    assert len(individuals_tasks) == 15
