"""
Tests for RFC-003 section 7 item 6 / section 3.1: the parallelism guidance
in SKILL.md and the resource-policy split.

RFC-003 section 3.1 splits parallelism policy into two audiences: domain
policy (which populations/regions a question implies, and how much work a
task is worth) belongs to the genomics curator and stays in SKILL.md as
prose; resource policy (memory budget per task, vCPUs, host memory) belongs
to whoever knows the target machine and lives in resource-policy.md. This
file guards both halves of that split plus the acceptance criteria in the
task prompt:

1. ``load_skill_context()`` includes the guidance section, and it mentions
   ``recommend_parallelism``, the ~10,000-variants-per-task floor, the
   cohort-size scaling, and that the value is clamped.
2. No file under ``skills/`` still states an absolute preset task count.
3. ``resource-policy.md`` exists, is registered in ``SKILL_FILES``, appears
   in ``load_skill_context()`` output, names an owner for each resource
   field, and documents every ``ComputeEnvironment`` field.
4. The guidance does not restate the clamp formula (``clamp(``,
   ``max_work``, ``1.2``) -- the mechanism stays in code (RFC-003 section
   3.1).
5. Every numeric value in the guidance section carries a justification
   nearby, and the quoted per-task memory budget equals
   ``MEMORY_BUDGET_PRESETS["medium"]``.
6. The full suite passes (asserted by the test runner invoking this file,
   not by a test within it).
"""
from __future__ import annotations

import dataclasses
import re

import pytest

from workflow_composer.core.environment import ComputeEnvironment, MEMORY_BUDGET_PRESETS
from workflow_composer.interpretation.skill_loader import (
    SKILL_DIR,
    SKILL_FILES,
    load_skill_context,
)

GUIDANCE_HEADING = "## Choosing individuals parallelism"


def _next_heading_index(text: str, start: int) -> int:
    """Index of the next ``## `` heading after ``start``, or ``len(text)``."""
    match = re.search(r"^## ", text[start:], flags=re.MULTILINE)
    return start + match.start() if match else len(text)


def _guidance_section() -> str:
    """Extract the "Choosing individuals parallelism" section from the
    skill context returned by ``load_skill_context()`` (not the raw file),
    so this test exercises the same text an agent actually receives.
    """
    context = load_skill_context()
    start = context.find(GUIDANCE_HEADING)
    assert start != -1, "SKILL.md is missing the 'Choosing individuals parallelism' section"
    end = _next_heading_index(context, start + len(GUIDANCE_HEADING))
    return context[start:end]


# ---------------------------------------------------------------------------
# Acceptance criterion 1: guidance section content
# ---------------------------------------------------------------------------

def test_load_skill_context_includes_guidance_section():
    context = load_skill_context()
    assert GUIDANCE_HEADING in context


def test_guidance_mentions_recommend_parallelism():
    section = _guidance_section()
    assert "recommend_parallelism" in section


def test_guidance_states_ten_thousand_variant_floor():
    section = _guidance_section()
    assert "10,000" in section
    assert "variants" in section


def test_guidance_states_cohort_size_scaling():
    section = _guidance_section()
    lowered = section.lower()
    assert "cohort" in lowered
    assert "scale" in lowered  # "scales with cohort size"


def test_guidance_states_value_is_clamped():
    section = _guidance_section()
    assert "clamp" in section.lower()
    assert "hint" in section.lower()


# ---------------------------------------------------------------------------
# Acceptance criterion 2: no absolute preset task counts anywhere in skills/
# ---------------------------------------------------------------------------

STALE_PRESET_PATTERNS = [
    r'small"\s*\(10\)',
    r'medium"\s*\(50\)',
    r'large"\s*\(250\)',
    r"ind_jobs\s*=\s*50\b",
]


@pytest.mark.parametrize("filename", SKILL_FILES)
def test_no_stale_absolute_preset_counts(filename):
    filepath = SKILL_DIR / filename
    if not filepath.exists():
        pytest.skip(f"{filename} does not exist")
    content = filepath.read_text()
    for pattern in STALE_PRESET_PATTERNS:
        assert not re.search(pattern, content), (
            f"{filename} still states an absolute preset task count "
            f"matching {pattern!r}"
        )


def test_generate_workflow_parallelism_doc_names_memory_budget():
    skill_md = (SKILL_DIR / "SKILL.md").read_text()
    # The parallelism preset description (in both plan_workflow and
    # generate_workflow parameter lists) must describe a memory budget, not
    # a bare task count.
    assert "memory budget" in skill_md.lower()
    assert skill_md.count('"small"') >= 2  # named once per parameter list
    assert skill_md.count('"medium"') >= 2
    assert skill_md.count('"large"') >= 2


# ---------------------------------------------------------------------------
# Acceptance criterion 3: resource-policy.md
# ---------------------------------------------------------------------------

def test_resource_policy_file_exists():
    assert (SKILL_DIR / "resource-policy.md").exists()


def test_resource_policy_registered_in_skill_files():
    assert "resource-policy.md" in SKILL_FILES


def test_resource_policy_appears_in_skill_context():
    context = load_skill_context()
    assert "# resource-policy.md" in context


def test_resource_policy_names_an_owner():
    content = (SKILL_DIR / "resource-policy.md").read_text()
    assert "whoever knows the target machine" in content


@pytest.mark.parametrize("field", [f.name for f in dataclasses.fields(ComputeEnvironment)])
def test_resource_policy_documents_every_compute_environment_field(field):
    content = (SKILL_DIR / "resource-policy.md").read_text()
    assert f"`{field}`" in content, (
        f"resource-policy.md does not document ComputeEnvironment field "
        f"{field!r}"
    )


# ---------------------------------------------------------------------------
# Acceptance criterion 4: the clamp formula is not restated
# ---------------------------------------------------------------------------

FORBIDDEN_MECHANISM_STRINGS = ["clamp(", "max_work", "1.2"]


@pytest.mark.parametrize("filename", SKILL_FILES)
@pytest.mark.parametrize("forbidden", FORBIDDEN_MECHANISM_STRINGS)
def test_skill_markdown_does_not_restate_clamp_formula(filename, forbidden):
    filepath = SKILL_DIR / filename
    if not filepath.exists():
        pytest.skip(f"{filename} does not exist")
    content = filepath.read_text()
    assert forbidden not in content, (
        f"{filename} restates clamp mechanism string {forbidden!r}; "
        f"RFC-003 section 3.1 keeps the formula in code"
    )


# ---------------------------------------------------------------------------
# Acceptance criterion 5: numeric values carry justification; 512 matches code
# ---------------------------------------------------------------------------

# Each numeric literal expected in the guidance section, and the
# justification cue(s) that must appear within a nearby window of text
# (same or adjacent line/sentence) explaining why that number is what it is.
NUMERIC_JUSTIFICATIONS = {
    "10,000": ["fixed cost", "amortise"],
    "1,000": ["fixed cost", "amortise", "cohort"],
    "2,000": ["scales inversely", "half", "cohort"],
    "5,000": ["half", "scales inversely", "cohort"],
}


def test_cohort_scaling_direction_matches_recommend_parallelism():
    """The prose claims a *larger* cohort needs *fewer* variants per task to
    clear the fixed-cost floor. Verify that against recommend_parallelism
    itself (RFC-003 section 4.3: in the min_work-bound regime,
    rows_per_task = min_work / individuals, so it is inversely proportional
    to individuals) rather than trusting the prose to describe its own
    mechanism correctly -- a bare "assert the sentence contains a cue word"
    test cannot catch a sentence whose claim is simply backwards.
    """
    from workflow_composer.core.parallelism import recommend_parallelism

    small = recommend_parallelism(
        variants=8000, individuals=1000, vcpus=64, host_mem_mb=1_000_000
    )
    large = recommend_parallelism(
        variants=8000, individuals=2000, vcpus=64, host_mem_mb=1_000_000
    )
    assert small.binding == "min_work"
    assert large.binding == "min_work"

    rows_per_task_small = 8000 / small.ind_jobs
    rows_per_task_large = 8000 / large.ind_jobs
    # Doubling individuals halves the min_work-bound rows-per-task floor --
    # a larger cohort needs FEWER variants per task, not more.
    assert rows_per_task_large < rows_per_task_small


@pytest.mark.parametrize("number,cues", list(NUMERIC_JUSTIFICATIONS.items()))
def test_numeric_values_carry_adjacent_justification(number, cues):
    section = _guidance_section()
    idx = section.find(number)
    assert idx != -1, f"expected {number!r} in the guidance section"
    window = section[max(0, idx - 300): idx + 300]
    assert any(cue in window for cue in cues), (
        f"{number!r} in the guidance section has no justification nearby "
        f"(looked for one of {cues!r})"
    )


def test_per_task_memory_budget_in_prose_matches_medium_preset():
    section = _guidance_section()
    medium_mb = MEMORY_BUDGET_PRESETS["medium"]
    assert str(medium_mb) in section, (
        f"guidance section does not quote the medium preset's memory "
        f"budget ({medium_mb} MB) -- prose and code have drifted"
    )
    idx = section.find(str(medium_mb))
    window = section[max(0, idx - 200): idx + 200]
    assert "calibrated" in window or "budget" in window, (
        f"{medium_mb!r} in the guidance section has no nearby justification"
    )
