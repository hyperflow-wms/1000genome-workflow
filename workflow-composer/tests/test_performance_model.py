"""
Tests for PerformanceModel and the shipped "rfc-006-review" profile.
"""
from __future__ import annotations

import dataclasses

import pytest

from workflow_composer.core.performance_model import (
    DEFAULT_PERFORMANCE_MODEL,
    PerformanceModel,
)


# ---------------------------------------------------------------------------
# Shipped coefficients pin section 2.1's table exactly
# ---------------------------------------------------------------------------

def test_shipped_coefficients_match_section_2_1_table():
    model = PerformanceModel.resolve("rfc-006-review")
    assert model.a_ind == 8.0
    assert model.b_ind == 2.0e-6
    assert model.b_merge == 1.3e-6
    assert model.c_merge == 0.8
    assert model.a_mo == 15.0
    assert model.b_mo == 3.5e-8
    assert model.a_fr == 105.0
    assert model.b_fr == 6.0e-7


def test_default_performance_model_is_shipped_profile():
    assert DEFAULT_PERFORMANCE_MODEL == PerformanceModel.resolve("rfc-006-review")
    assert DEFAULT_PERFORMANCE_MODEL.name == "rfc-006-review"


# ---------------------------------------------------------------------------
# version and provenance are non-empty
# ---------------------------------------------------------------------------

def test_version_is_non_empty_string():
    model = PerformanceModel.resolve("rfc-006-review")
    assert isinstance(model.version, str)
    assert model.version != ""


def test_provenance_is_non_empty_string():
    model = PerformanceModel.resolve("rfc-006-review")
    assert isinstance(model.provenance, str)
    assert model.provenance != ""


# ---------------------------------------------------------------------------
# resolve() applies explicit overrides
# ---------------------------------------------------------------------------

def test_resolve_applies_explicit_override():
    model = PerformanceModel.resolve("rfc-006-review", a_ind=99.0)
    assert model.a_ind == 99.0
    # Everything else stays at the shipped value.
    assert model.b_ind == 2.0e-6


def test_resolve_applies_multiple_overrides():
    model = PerformanceModel.resolve(
        "rfc-006-review", a_ind=1.0, b_ind=2.0, c_merge=3.0
    )
    assert model.a_ind == 1.0
    assert model.b_ind == 2.0
    assert model.c_merge == 3.0


def test_resolve_without_overrides_matches_shipped_profile():
    model = PerformanceModel.resolve("rfc-006-review")
    assert model == DEFAULT_PERFORMANCE_MODEL


# ---------------------------------------------------------------------------
# Error contract, mirroring ComputeEnvironment.resolve
# ---------------------------------------------------------------------------

def test_unknown_profile_raises_value_error_listing_known_names():
    with pytest.raises(ValueError) as excinfo:
        PerformanceModel.resolve("nonexistent")
    message = str(excinfo.value)
    assert "nonexistent" in message
    assert "rfc-006-review" in message


def test_unknown_override_key_raises_type_error_naming_the_key():
    with pytest.raises(TypeError) as excinfo:
        PerformanceModel.resolve("rfc-006-review", not_a_field=1.0)
    assert "not_a_field" in str(excinfo.value)


def test_name_itself_is_not_overridable():
    with pytest.raises(TypeError):
        PerformanceModel.resolve("rfc-006-review", name="renamed")


# ---------------------------------------------------------------------------
# Frozen dataclass
# ---------------------------------------------------------------------------

def test_performance_model_is_frozen():
    model = PerformanceModel.resolve("rfc-006-review")
    with pytest.raises(dataclasses.FrozenInstanceError):
        model.a_ind = 0.0  # type: ignore[misc]
