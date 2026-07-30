"""
Tests for parallelism observability.

Both dials (``ind_jobs``, ``max_parallelism``) and the reason behind them
must be emitted wherever a workflow is planned or generated, and the same
fields must land in ``plan.json`` (``WorkflowPlan.parameters_used``),
consistent with ``ExecutionHints``. Section 5's central worry: a
safe-looking task count can hide an unsafe concurrency, so nothing may
report one dial without the other, and the recorded-vs-executed divergence
(``plan.json`` said 50, the harness ran 10) must be unrepresentable.

Covers the acceptance criteria:

1. ``format_parallelism_reason`` is the single source of the reason
   format: the first worked example matches exactly, every emitted variant
   (``recommend_parallelism``, ``resolve_parallelism``'s explicit-hint
   override, ``generate_workflow``'s clamp metadata, ``create_advisory_plan``
   / ``plan_workflow``'s ``ExecutionHints.parallelism_reason``, the CLI)
   matches a regex requiring both ``ind_jobs=`` and ``max_parallelism=``,
   and a source scan guards against a second, independently-typed f-string
   producing the same shape.
2. ``plan_workflow(...).parameters_used`` carries ``ind_jobs``,
   ``max_parallelism``, ``est_peak_mb``, and ``parallelism_reason``,
   consistent with ``ExecutionHints`` for the same call, and survives a
   ``model_dump_json()``/parse round trip.
3. An explicit ``ind_jobs`` hint (250) that ``generate_workflow`` clamps
   down is recorded as the effective (post-clamp) value, not the hint.
4. ``max_parallelism`` is exposed under the same key ``plan.json`` records
   and ``ExecutionHints`` returns -- the value a run script would read for
   ``HF_VAR_REDIS_CMD_MAX_PARALLELISM`` -- and the two are identical.
5. CLI emission: ``generate`` and ``plan`` emit the reason to stderr while
   stdout stays valid JSON, verified through ``CliRunner`` without a full
   run.
6. Full suite passes (checked by the test runner, not by this file).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from workflow_composer import cli as cli_module
from workflow_composer.cli import cli
from workflow_composer.core.generator import BUNDLED_POPULATIONS_DIR, generate_workflow
from workflow_composer.core.models import GenomicRegion, ResearchIntent, WorkflowPlan
from workflow_composer.core.parallelism import format_parallelism_reason, recommend_parallelism
from workflow_composer.core.planner import (
    create_advisory_plan,
    plan_workflow,
    resolve_parallelism,
)

# The preserved HLA baseline: one chromosome (6), row_count=166052 -- the
# exact V in the first worked example (V=166,052,
# I=1153, 16 vCPUs -> ind_jobs=15, max_parallelism=15, core-bound,
# est_peak=27MB/task).
BASELINE_DIR = Path(__file__).parent.parent.parent / "tests" / "integration" / "workflow-eur-afr-hla-baseline"
HLA_DATA_CSV = BASELINE_DIR / "data.csv"
HLA_ROW_COUNT = 166_052
HLA_INDIVIDUALS = 1153

pytestmark = pytest.mark.skipif(
    not HLA_DATA_CSV.exists(),
    reason="tests/integration/workflow-eur-afr-hla-baseline/data.csv not available",
)

HLA_REGION = GenomicRegion(name="HLA", chromosome="6", start=28477797, end=33448354, context="immune function")

# The reason format: both dials required together, the binding
# constraint named as one of the three allowed labels.
REASON_RE = re.compile(
    r"ind_jobs=\d+ max_parallelism=\d+ \((core|memory|min_work)-bound; "
    r"V=[\d,]+ I=\d+ C=\d+ est_peak=\d+MB/task\)"
)


def _hla_eur_afr_intent() -> ResearchIntent:
    return ResearchIntent(
        analysis_type="population_comparison",
        populations=["EUR", "AFR"],
        regions=[HLA_REGION],
    )


def _assert_both_dials(reason: str) -> None:
    assert "ind_jobs=" in reason, reason
    assert "max_parallelism=" in reason, reason
    assert REASON_RE.search(reason), reason


# ---------------------------------------------------------------------------
# Acceptance criterion 1: format_parallelism_reason is the single source of
# the reason format.
# ---------------------------------------------------------------------------

def test_format_helper_matches_section_4_4_first_row_exactly():
    reason = format_parallelism_reason(
        ind_jobs=15,
        max_parallelism=15,
        binding="cores",
        variants=166_052,
        individuals=1153,
        cores=15,
        est_peak_mb=27,
    )
    assert reason == (
        "ind_jobs=15 max_parallelism=15 "
        "(core-bound; V=166,052 I=1153 C=15 est_peak=27MB/task)"
    )


def test_recommend_parallelism_worked_example_matches_helper_output():
    """The first worked example, produced end-to-end, matches the helper's
    output exactly -- recommend_parallelism has no format string of its own
    left after routing through format_parallelism_reason."""
    result = recommend_parallelism(
        variants=166_052, individuals=1153, vcpus=16,
        mem_budget_mb=512, host_mem_mb=31744, engine_reserve=1,
    )
    assert result.ind_jobs == 15
    assert result.max_parallelism == 15
    assert result.reason == (
        "ind_jobs=15 max_parallelism=15 "
        "(core-bound; V=166,052 I=1153 C=15 est_peak=27MB/task)"
    )


def test_every_emitted_variant_has_both_dials():
    """Every reporting call site emits a reason with both
    dials in the same shape -- recommend_parallelism itself, the
    planner's explicit-hint override, generate_workflow's clamp metadata,
    and both planning entry points' ExecutionHints.parallelism_reason."""
    intent = _hla_eur_afr_intent()

    reasons = [
        recommend_parallelism(variants=166_052, individuals=1153, vcpus=16, host_mem_mb=31744).reason,
        resolve_parallelism(intent, compute_environment="local", ind_jobs=250).reason,
        create_advisory_plan(intent, compute_environment="local").execution_hints.parallelism_reason,
        plan_workflow(
            intent, compute_environment="local",
            data_csv=HLA_DATA_CSV, populations_dir=BUNDLED_POPULATIONS_DIR,
        ).execution_hints.parallelism_reason,
        generate_workflow(
            data_csv=HLA_DATA_CSV, populations_dir=BUNDLED_POPULATIONS_DIR,
            ind_jobs=250, chromosome_filter=["6"], population_filter=["EUR", "AFR"],
            individuals=HLA_INDIVIDUALS, compute_environment="local",
        )["metadata"]["parallelism"][0]["reason"],
    ]

    assert len(reasons) == 5
    for reason in reasons:
        _assert_both_dials(reason)


def test_format_helper_is_the_only_source_of_the_format():
    """No file in the package may contain a second,
    independently-typed f-string producing the two-dial shape
    (``ind_jobs={...} max_parallelism={...}``) -- only
    core/parallelism.py's format_parallelism_reason may. A duplicate is
    exactly how one caller's reason silently stops matching another's.
    """
    src_root = Path(cli_module.__file__).parent  # .../workflow_composer/
    pattern = re.compile(r"ind_jobs=\{[^}]*\}\s*max_parallelism=\{")

    hits: dict[str, int] = {}
    for py_file in src_root.rglob("*.py"):
        count = len(pattern.findall(py_file.read_text()))
        if count:
            hits[str(py_file.relative_to(src_root))] = count

    assert hits == {"core/parallelism.py": 1}, hits


# ---------------------------------------------------------------------------
# Acceptance criterion 2: plan.json carries all four fields, consistent
# with ExecutionHints, and survives a model_dump_json()/parse round trip.
# ---------------------------------------------------------------------------

def test_plan_workflow_parameters_used_matches_execution_hints_and_round_trips():
    intent = _hla_eur_afr_intent()
    plan = plan_workflow(
        intent, compute_environment="local",
        data_csv=HLA_DATA_CSV, populations_dir=BUNDLED_POPULATIONS_DIR,
    )

    for key in ("ind_jobs", "max_parallelism", "est_peak_mb", "parallelism_reason"):
        assert key in plan.parameters_used, plan.parameters_used

    # Consistent with ExecutionHints for the same call.
    assert plan.parameters_used["max_parallelism"] == plan.execution_hints.max_parallelism
    assert plan.parameters_used["est_peak_mb"] == plan.execution_hints.est_peak_mb
    assert plan.parameters_used["parallelism_reason"] == plan.execution_hints.parallelism_reason
    # ind_jobs has no same-named ExecutionHints field, but must agree with
    # the value embedded in the (ExecutionHints) reason string.
    match = re.match(r"^ind_jobs=(\d+)", plan.execution_hints.parallelism_reason)
    assert match is not None
    assert int(match.group(1)) == plan.parameters_used["ind_jobs"]

    # plan.json on disk carries the same fields: round trip through
    # model_dump_json()/parse.
    restored = WorkflowPlan.model_validate_json(plan.model_dump_json())
    assert restored.parameters_used == plan.parameters_used
    assert restored.execution_hints == plan.execution_hints


def test_create_advisory_plan_parameters_used_matches_execution_hints_and_round_trips():
    intent = _hla_eur_afr_intent()
    plan = create_advisory_plan(intent, compute_environment="local")

    for key in ("ind_jobs", "max_parallelism", "est_peak_mb", "parallelism_reason"):
        assert key in plan.parameters_used, plan.parameters_used
    assert plan.parameters_used["max_parallelism"] == plan.execution_hints.max_parallelism
    assert plan.parameters_used["est_peak_mb"] == plan.execution_hints.est_peak_mb
    assert plan.parameters_used["parallelism_reason"] == plan.execution_hints.parallelism_reason

    restored = WorkflowPlan.model_validate_json(plan.model_dump_json())
    assert restored.parameters_used == plan.parameters_used


# ---------------------------------------------------------------------------
# Acceptance criterion 3: a clamped explicit hint is recorded as the
# effective value, not the pre-clamp hint.
# ---------------------------------------------------------------------------

def test_explicit_hint_clamped_by_generator_is_recorded_not_the_hint():
    """A requested ind_jobs=250 on the 16-core/31GB "local" environment is
    memory/core-safe at 15 (the documented worked example); plan_workflow
    must record 15, not the 250 that went in -- the exact
    failure mode (plan.json stating a value nothing executed with)."""
    intent = _hla_eur_afr_intent()
    plan = plan_workflow(
        intent, compute_environment="local", ind_jobs=250,
        data_csv=HLA_DATA_CSV, populations_dir=BUNDLED_POPULATIONS_DIR,
    )

    assert plan.parameters_used["ind_jobs"] != 250
    assert plan.parameters_used["ind_jobs"] == 15
    assert plan.execution_hints.max_parallelism == 15
    assert plan.execution_hints.recommended_parallelism == 15
    # The workflow actually generated has exactly the recorded ind_jobs
    # tasks for the touched chromosome -- the recorded number is what ran.
    individuals_tasks = [p for p in plan.workflow["processes"] if p["name"] == "individuals"]
    assert len(individuals_tasks) == 15


def test_generate_workflow_clamp_metadata_reports_effective_not_hint():
    """The lower-level equivalent of the test above, directly against
    generate_workflow rather than through the planner."""
    wf = generate_workflow(
        data_csv=HLA_DATA_CSV, populations_dir=BUNDLED_POPULATIONS_DIR,
        ind_jobs=250, chromosome_filter=["6"], population_filter=["EUR", "AFR"],
        individuals=HLA_INDIVIDUALS, compute_environment="local",
    )
    entry = wf["metadata"]["parallelism"][0]
    assert entry["ind_jobs_hint"] == 250
    assert entry["ind_jobs"] == 15
    assert "ind_jobs=15" in entry["reason"]
    assert "250" in entry["reason"]  # both the hint and the effective value


# ---------------------------------------------------------------------------
# Acceptance criterion 4: max_parallelism recorded in plan.json and
# returned via ExecutionHints are the same value -- what a run script would
# read for HF_VAR_REDIS_CMD_MAX_PARALLELISM.
# ---------------------------------------------------------------------------

def test_max_parallelism_recorded_and_returned_are_identical():
    intent = _hla_eur_afr_intent()
    plan = create_advisory_plan(intent, compute_environment="local")

    assert isinstance(plan.parameters_used["max_parallelism"], int)
    assert plan.parameters_used["max_parallelism"] == plan.execution_hints.max_parallelism == 15

    full = plan_workflow(
        intent, compute_environment="local",
        data_csv=HLA_DATA_CSV, populations_dir=BUNDLED_POPULATIONS_DIR,
    )
    assert full.parameters_used["max_parallelism"] == full.execution_hints.max_parallelism == 15


# ---------------------------------------------------------------------------
# Acceptance criterion 5: CLI emission, verified through CliRunner.
# ---------------------------------------------------------------------------

def test_cli_generate_emits_reason_to_stderr_stdout_stays_json():
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(cli, [
        "generate",
        "--data-csv", str(HLA_DATA_CSV),
        "--populations-dir", str(BUNDLED_POPULATIONS_DIR),
        "--env", "local",
        "--populations", "EUR,AFR",
    ])
    assert result.exit_code == 0, result.output or result.exception
    assert REASON_RE.search(result.stderr), result.stderr
    workflow = json.loads(result.stdout)  # stdout must be pure, parseable JSON
    assert "processes" in workflow


def test_cli_generate_explicit_ind_jobs_still_emits_reason_with_effective_value():
    """The reason must be emitted even when --ind-jobs is given explicitly
    (previously only the auto-recommend path printed anything), and it must
    show the effective, clamped value."""
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(cli, [
        "generate",
        "--data-csv", str(HLA_DATA_CSV),
        "--populations-dir", str(BUNDLED_POPULATIONS_DIR),
        "--env", "local",
        "--populations", "EUR,AFR",
        "--ind-jobs", "250",
    ])
    assert result.exit_code == 0, result.output or result.exception
    assert REASON_RE.search(result.stderr), result.stderr
    assert "ind_jobs=15" in result.stderr
    workflow = json.loads(result.stdout)
    individuals_tasks = [p for p in workflow["processes"] if p["name"] == "individuals"]
    assert len(individuals_tasks) == 15  # the emitted workflow matches the reported reason


def test_cli_plan_emits_reason_to_stderr_stdout_stays_json():
    runner = CliRunner(mix_stderr=False)
    intent_json = _hla_eur_afr_intent().model_dump_json()
    result = runner.invoke(cli, ["plan", intent_json, "--env", "local"])
    assert result.exit_code == 0, result.output or result.exception
    assert REASON_RE.search(result.stderr), result.stderr
    plan = json.loads(result.stdout)
    assert "parameters_used" in plan
    assert "max_parallelism" in plan["parameters_used"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
