"""
Tests for pointing the test harness's --vcpus flag at recommend_parallelism
and deleting the duplicate rule (RFC-003 section 7 item 3).

Covers the acceptance criteria:

1. The RFC-002 section 5 rule (``compute_adaptive_parallelism``) is gone
   from ``tests/integration/lib/test_framework.py``, not merely bypassed.
2. The harness's ``generate_estimated_workflow`` and the composer's
   ``planner.resolve_parallelism`` agree on the individuals task count for
   the same intent and environment -- the RFC-003 section 1.1 divergence
   can no longer happen.
3. The ``adaptive-parallelism`` CLI subcommand (invoked as a subprocess,
   exactly as ``run-research-tests.sh`` does) exits 0 and prints both
   dials, the memory estimate, and the reason as JSON.
4. ``run-research-tests.sh`` no longer hardcodes ``MAX_PARALLELISM=20``;
   it assigns MAX_PARALLELISM from the tool's output, and the script is
   still valid bash.
5. ``--vcpus 2`` and ``--vcpus 64`` on the same intent produce different,
   correctly ordered ind_jobs, and neither implies a per-task memory
   estimate above the environment's mem_budget_mb.

No Docker, no FTP, no LLM call: only the pure-Python entry points and the
``adaptive-parallelism`` CLI subcommand (which takes an intent JSON
directly, no LLM interpretation) are exercised.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "integration" / "lib"))

import test_framework  # noqa: E402  (path-inserted import)

from workflow_composer.core.models import GenomicRegion, ResearchIntent  # noqa: E402
from workflow_composer.core.planner import resolve_parallelism  # noqa: E402

RUN_RESEARCH_TESTS_SH = REPO_ROOT / "tests" / "integration" / "run-research-tests.sh"
FRAMEWORK_PY = REPO_ROOT / "tests" / "integration" / "lib" / "test_framework.py"

HLA_REGION_DICT = {
    "name": "HLA",
    "chromosome": "6",
    "start": 28477797,
    "end": 33448354,
    "context": "immune function",
}

HLA_EUR_AFR_INTENT_DICT = {
    "analysis_type": "population_comparison",
    "populations": ["EUR", "AFR"],
    "chromosomes": None,
    "regions": [HLA_REGION_DICT],
    "focus": "all_variants",
}


def _hla_eur_afr_intent() -> ResearchIntent:
    return ResearchIntent(
        analysis_type="population_comparison",
        populations=["EUR", "AFR"],
        regions=[GenomicRegion(**HLA_REGION_DICT)],
    )


# ``cases.yaml``'s ``brca-breast-cancer`` fixture: two regions on different
# chromosomes (BRCA1/chr17, BRCA2/chr13). A summed-V rule and a max-per-
# chromosome rule diverge on this intent (chr17's region is the larger one),
# which is exactly what a previous version of ``recommend_harness_parallelism``
# got wrong -- it summed V across both regions instead of taking the max.
BRCA1_REGION_DICT = {
    "name": "BRCA1",
    "chromosome": "17",
    "start": 43044295,
    "end": 43125483,
    "context": "breast cancer",
}
BRCA2_REGION_DICT = {
    "name": "BRCA2",
    "chromosome": "13",
    "start": 32315086,
    "end": 32400266,
    "context": "breast cancer",
}

BRCA_INTENT_DICT = {
    "analysis_type": "multi_population",
    "populations": ["AFR", "EUR", "EAS", "SAS", "AMR"],
    "chromosomes": None,
    "regions": [BRCA1_REGION_DICT, BRCA2_REGION_DICT],
    "focus": "all_variants",
}


def _brca_intent() -> ResearchIntent:
    return ResearchIntent(
        analysis_type="multi_population",
        populations=["AFR", "EUR", "EAS", "SAS", "AMR"],
        regions=[GenomicRegion(**BRCA1_REGION_DICT), GenomicRegion(**BRCA2_REGION_DICT)],
    )


# ``cases.yaml``'s ``genome-wide-null`` fixture: no regions, no explicit
# chromosomes -- all 22 autosomes. Summing V across all 22 gave an 11x
# divergence from the max-per-chromosome rule in the previous version of
# ``recommend_harness_parallelism``.
GENOME_WIDE_NULL_INTENT_DICT = {
    "analysis_type": "multi_population",
    "populations": ["AFR", "ALL", "AMR", "EAS", "EUR", "GBR", "SAS"],
    "chromosomes": None,
    "regions": None,
    "focus": "all_variants",
}


def _genome_wide_null_intent() -> ResearchIntent:
    return ResearchIntent(
        analysis_type="multi_population",
        populations=["AFR", "ALL", "AMR", "EAS", "EUR", "GBR", "SAS"],
    )


MULTI_CHROMOSOME_FIXTURES = [
    pytest.param(HLA_EUR_AFR_INTENT_DICT, _hla_eur_afr_intent, id="eur-afr-hla"),
    pytest.param(BRCA_INTENT_DICT, _brca_intent, id="brca-breast-cancer"),
    pytest.param(GENOME_WIDE_NULL_INTENT_DICT, _genome_wide_null_intent, id="genome-wide-null"),
]


# ---------------------------------------------------------------------------
# Acceptance criterion 1: the duplicate rule is gone, not merely bypassed.
# ---------------------------------------------------------------------------

def test_compute_adaptive_parallelism_removed_from_test_framework():
    assert not hasattr(test_framework, "compute_adaptive_parallelism")


def test_test_framework_source_has_no_duplicate_rule_symbols():
    source = FRAMEWORK_PY.read_text()
    for banned in (
        "compute_adaptive_parallelism",
        "target_variants_per_task",
        "max_tasks_per_vcpu",
        "PARALLELISM_PRESETS",
    ):
        assert banned not in source, f"{banned!r} still present in test_framework.py"


def test_generator_no_longer_exports_parallelism_presets():
    from workflow_composer.core import generator

    assert not hasattr(generator, "PARALLELISM_PRESETS")


# ---------------------------------------------------------------------------
# Acceptance criterion 2: the two entry points agree.
# ---------------------------------------------------------------------------

def test_harness_and_planner_agree_on_individuals_task_count():
    workflow = test_framework.generate_estimated_workflow(
        HLA_EUR_AFR_INTENT_DICT, vcpus=16
    )
    individuals_task_count = sum(
        1 for p in workflow["processes"] if p["name"] == "individuals"
    )

    expected = resolve_parallelism(
        _hla_eur_afr_intent(), compute_environment="aws", vcpus=16
    )

    assert individuals_task_count == expected.ind_jobs


def test_recommend_harness_parallelism_matches_resolve_parallelism_directly():
    """Same check at the Parallelism level, independent of workflow chunking."""
    harness_result = test_framework.recommend_harness_parallelism(
        HLA_EUR_AFR_INTENT_DICT, vcpus=16
    )
    planner_result = resolve_parallelism(
        _hla_eur_afr_intent(), compute_environment="aws", vcpus=16
    )

    assert harness_result.ind_jobs == planner_result.ind_jobs
    assert harness_result.max_parallelism == planner_result.max_parallelism
    assert harness_result.est_peak_mb == planner_result.est_peak_mb


@pytest.mark.parametrize("intent_dict, intent_factory", MULTI_CHROMOSOME_FIXTURES)
def test_harness_and_planner_agree_across_multi_chromosome_fixtures(intent_dict, intent_factory):
    """Regression test for the summed-V bug: a previous version of
    ``recommend_harness_parallelism`` summed V across every region/chromosome
    an intent touches, while ``planner.resolve_parallelism`` takes the max
    per chromosome (``ind_jobs`` is defined per chromosome, RFC-003 section
    4.3/4.5). That disagreed on two real ``cases.yaml`` fixtures that touch
    more than one chromosome -- ``brca-breast-cancer`` (chr17 + chr13,
    harness ind_jobs=2 vs planner ind_jobs=1) and ``genome-wide-null`` (all
    22 autosomes, harness ind_jobs=328 vs planner ind_jobs=29) -- even
    though the single-region ``eur-afr-hla`` fixture (included here too)
    could not expose the bug, since summing and maxing coincide when there
    is only one region/chromosome to sum or max over.

    Compared directly at the ``Parallelism`` level (not via
    ``generate_estimated_workflow``'s total task count, which sums tasks
    across all chromosomes and so is not comparable to a single
    per-chromosome ``ind_jobs`` once more than one chromosome is touched).
    """
    harness_result = test_framework.recommend_harness_parallelism(intent_dict, vcpus=16)
    planner_result = resolve_parallelism(
        intent_factory(), compute_environment="aws", vcpus=16
    )

    assert harness_result.ind_jobs == planner_result.ind_jobs
    assert harness_result.max_parallelism == planner_result.max_parallelism
    assert harness_result.est_peak_mb == planner_result.est_peak_mb


@pytest.mark.parametrize("intent_dict, intent_factory", MULTI_CHROMOSOME_FIXTURES)
def test_harness_workflow_individuals_count_matches_num_chromosomes_times_ind_jobs(
    intent_dict, intent_factory
):
    """``generate_estimated_workflow`` applies the single resolved
    ``ind_jobs`` identically to every chromosome the intent touches (no
    per-chromosome clamping happens on this path -- ``generator.generate``
    is called without ``individuals``/``compute_environment``). So the total
    number of ``individuals`` tasks in the generated workflow should be
    ``num_chromosomes * ind_jobs``, given every chromosome's estimated
    variant count comfortably exceeds ``ind_jobs`` (true for all three
    fixtures here -- ``ind_jobs`` never exceeds a few dozen).

    Uses ``generate_estimated_workflow``'s own chromosome count, not
    ``planner._num_chromosomes``: for an intent with neither regions nor an
    explicit chromosome list, the harness's chromosome-data loop covers the
    22 autosomes only, while ``_num_chromosomes`` (used for the
    ``max_parallelism`` concurrency divide, RFC-003 section 4.5) counts 24
    (autosomes + X + Y). That divergence is pre-existing, orthogonal to the
    ind_jobs/max_parallelism agreement this test suite is about, and out of
    this task's scope.
    """
    if intent_dict.get("regions"):
        num_chromosomes = len(intent_dict["regions"])
    elif intent_dict.get("chromosomes"):
        num_chromosomes = len(intent_dict["chromosomes"])
    else:
        num_chromosomes = 22

    workflow = test_framework.generate_estimated_workflow(intent_dict, vcpus=16)
    individuals_task_count = sum(
        1 for p in workflow["processes"] if p["name"] == "individuals"
    )

    expected = resolve_parallelism(
        intent_factory(), compute_environment="aws", vcpus=16
    )

    assert individuals_task_count == num_chromosomes * expected.ind_jobs


# ---------------------------------------------------------------------------
# Acceptance criterion 3: the adaptive-parallelism CLI subcommand.
# ---------------------------------------------------------------------------

def test_adaptive_parallelism_cli_subcommand():
    result = subprocess.run(
        [
            sys.executable,
            str(FRAMEWORK_PY),
            "adaptive-parallelism",
            "--intent-json",
            json.dumps(HLA_EUR_AFR_INTENT_DICT),
            "--vcpus",
            "16",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr

    payload = json.loads(result.stdout)
    for key in ("ind_jobs", "max_parallelism", "est_peak_mb", "reason"):
        assert key in payload, f"missing {key!r} in {payload}"

    assert payload["max_parallelism"] <= 15
    assert payload["ind_jobs"] >= 1
    assert isinstance(payload["reason"], str) and payload["reason"]


# ---------------------------------------------------------------------------
# Acceptance criterion 4: run-research-tests.sh.
# ---------------------------------------------------------------------------

def test_run_research_tests_sh_no_hardcoded_max_parallelism():
    source = RUN_RESEARCH_TESTS_SH.read_text()
    assert "export MAX_PARALLELISM=20" not in source
    assert "export MAX_PARALLELISM=$MAX_PARALLELISM_VALUE" in source
    assert "adaptive-parallelism" in source


def test_run_research_tests_sh_is_valid_bash():
    result = subprocess.run(
        ["bash", "-n", str(RUN_RESEARCH_TESTS_SH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# Acceptance criterion 5: --vcpus 2 vs --vcpus 64.
# ---------------------------------------------------------------------------

def test_vcpus_low_and_high_produce_different_correctly_ordered_ind_jobs():
    from workflow_composer.core.environment import ComputeEnvironment

    low = test_framework.recommend_harness_parallelism(
        HLA_EUR_AFR_INTENT_DICT, vcpus=2
    )
    high = test_framework.recommend_harness_parallelism(
        HLA_EUR_AFR_INTENT_DICT, vcpus=64
    )

    assert low.ind_jobs != high.ind_jobs
    assert high.ind_jobs > low.ind_jobs

    mem_budget_mb = ComputeEnvironment.resolve("aws").mem_budget_mb
    assert low.est_peak_mb <= mem_budget_mb
    assert high.est_peak_mb <= mem_budget_mb
