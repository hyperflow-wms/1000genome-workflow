"""
Test that the maintainer-facing capacity model document and the shipped
``PerformanceModel`` configuration agree on every coefficient.

``docs/capacity-model.md`` is prose for humans, not a knowledge document
loaded into any prompt (see CAPACITY-IMPLEMENTATION-PLAN.md section 3.C).
Nothing enforces that its coefficient table matches
``DEFAULT_PERFORMANCE_MODEL`` except this test: it parses the markdown table
rather than hardcoding a second copy of the numbers, so a coefficient change
in one place without the other fails here instead of silently drifting.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from workflow_composer.core.performance_model import DEFAULT_PERFORMANCE_MODEL

DOCS_DIR = Path(__file__).parent.parent / "docs"
CAPACITY_MODEL_DOC = DOCS_DIR / "capacity-model.md"

# One row: "| `a_ind` | 8 s | fixed cost per individuals task ... |"
_TABLE_ROW_RE = re.compile(
    r"^\|\s*`(?P<name>[a-z_]+)`\s*\|\s*(?P<value>[0-9.eE+-]+)\s*s\s*\|",
    re.MULTILINE,
)

# Every coefficient PerformanceModel carries, excluding fields that are not
# fitted coefficients: `name`, `version`, `provenance` are metadata, and
# `knee_tolerance` is deliberately a policy choice, not a measurement -- see
# the doc's own section on it.
_COEFFICIENT_FIELDS = ["a_ind", "b_ind", "b_merge", "c_merge", "a_mo", "b_mo", "a_fr", "b_fr"]


def _parse_coefficient_table() -> dict[str, float]:
    text = CAPACITY_MODEL_DOC.read_text()
    found = {}
    for match in _TABLE_ROW_RE.finditer(text):
        name = match.group("name")
        if name in _COEFFICIENT_FIELDS:
            found[name] = float(match.group("value"))
    return found


def test_capacity_model_doc_exists():
    assert CAPACITY_MODEL_DOC.exists(), (
        f"{CAPACITY_MODEL_DOC} is missing; the maintainer documentation "
        "for the capacity model must exist next to core/capacity.py"
    )


def test_capacity_model_doc_is_not_under_knowledge():
    """This is maintainer documentation, not a prompt-delivered document --
    it must not live anywhere `skill_loader.py` could pick it up."""
    package_dir = Path(__file__).parent.parent / "src" / "workflow_composer"
    knowledge_dir = package_dir / "knowledge"
    assert not CAPACITY_MODEL_DOC.is_relative_to(knowledge_dir)


def test_coefficient_table_parses_every_coefficient():
    table = _parse_coefficient_table()
    missing = set(_COEFFICIENT_FIELDS) - set(table)
    assert not missing, (
        f"docs/capacity-model.md's coefficient table is missing rows for "
        f"{sorted(missing)}; expected a markdown table row per field like "
        f"'| `a_ind` | 8 s | ... |'"
    )


@pytest.mark.parametrize("field", _COEFFICIENT_FIELDS)
def test_doc_coefficient_matches_default_performance_model(field):
    table = _parse_coefficient_table()
    doc_value = table[field]
    model_value = getattr(DEFAULT_PERFORMANCE_MODEL, field)
    assert doc_value == model_value, (
        f"docs/capacity-model.md states {field}={doc_value!r}, but "
        f"DEFAULT_PERFORMANCE_MODEL.{field} is {model_value!r} -- "
        f"documentation and configuration have drifted"
    )


def test_doc_table_has_no_extra_stale_coefficients():
    """Every row the table parses as a coefficient must be a real
    PerformanceModel field, so a renamed or removed field is caught here
    rather than leaving a stale row nobody notices."""
    table = _parse_coefficient_table()
    from dataclasses import fields

    model_fields = {f.name for f in fields(DEFAULT_PERFORMANCE_MODEL)}
    unknown = set(table) - model_fields
    assert not unknown, (
        f"docs/capacity-model.md's coefficient table names {sorted(unknown)}, "
        f"which are not fields of PerformanceModel"
    )


def test_knee_tolerance_is_documented_as_a_policy_choice_not_a_coefficient():
    """knee_tolerance is a real PerformanceModel field but must not appear as
    a row in the fitted-coefficient table -- it isn't fitted, and the doc
    says so in prose instead."""
    table = _parse_coefficient_table()
    assert "knee_tolerance" not in table
    text = CAPACITY_MODEL_DOC.read_text()
    assert "knee_tolerance" in text, (
        "docs/capacity-model.md must discuss knee_tolerance even though it "
        "is excluded from the coefficient table"
    )
