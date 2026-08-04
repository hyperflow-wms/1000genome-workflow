"""
Tests for planner.estimate_region_volumes.

Covers the acceptance criteria for "Expose per-region variant estimates from
the planner (A2)":

1. For every intent shape (regions given; chromosomes given; neither), the
   maximum ``variants`` across ``estimate_region_volumes(intent)`` equals
   ``_estimate_max_variants_per_chromosome(intent)`` -- the new per-region
   view and the existing scalar agree.
2. ``estimate_region_volumes`` returns a non-empty list for all three shapes,
   with exactly one entry per region, per chromosome, and 22 entries for the
   genome-wide case.
3. ``individuals`` is identical across every entry and equals
   ``_estimate_individuals(intent.populations)``.
4. The Q1 intent (HLA + BRCA1) gives two entries whose chromosomes are "6"
   and "17", with the chr6 (HLA) entry the larger.
"""
from __future__ import annotations

import pytest

from workflow_composer.core.data_resolver import KNOWN_REGIONS
from workflow_composer.core.models import ResearchIntent
from workflow_composer.core.planner import (
    _estimate_individuals,
    _estimate_max_variants_per_chromosome,
    estimate_region_volumes,
)

HLA_REGION = KNOWN_REGIONS["HLA"]
BRCA1_REGION = KNOWN_REGIONS["BRCA1"]


def _regions_intent() -> ResearchIntent:
    return ResearchIntent(
        analysis_type="region_analysis",
        populations=["EUR", "AFR"],
        regions=[HLA_REGION, BRCA1_REGION],
    )


def _chromosomes_intent() -> ResearchIntent:
    return ResearchIntent(
        analysis_type="population_comparison",
        populations=["EUR", "AFR"],
        chromosomes=["6", "9"],
    )


def _genome_wide_intent() -> ResearchIntent:
    return ResearchIntent(
        analysis_type="population_comparison",
        populations=["EUR", "AFR"],
    )


ALL_INTENTS = {
    "regions": _regions_intent,
    "chromosomes": _chromosomes_intent,
    "genome_wide": _genome_wide_intent,
}


@pytest.mark.parametrize("make_intent", ALL_INTENTS.values(), ids=ALL_INTENTS.keys())
def test_non_empty(make_intent) -> None:
    intent = make_intent()
    estimates = estimate_region_volumes(intent)
    assert len(estimates) > 0


@pytest.mark.parametrize("make_intent", ALL_INTENTS.values(), ids=ALL_INTENTS.keys())
def test_max_variants_matches_scalar(make_intent) -> None:
    """The per-region view and the existing scalar function must agree on
    the maximum -- this is the equivalence the whole task hinges on."""
    intent = make_intent()
    estimates = estimate_region_volumes(intent)
    assert max(e.variants for e in estimates) == _estimate_max_variants_per_chromosome(intent)


def test_entry_count_regions() -> None:
    intent = _regions_intent()
    estimates = estimate_region_volumes(intent)
    assert len(estimates) == len(intent.regions)


def test_entry_count_chromosomes() -> None:
    intent = _chromosomes_intent()
    estimates = estimate_region_volumes(intent)
    assert len(estimates) == len(intent.chromosomes)


def test_entry_count_genome_wide() -> None:
    intent = _genome_wide_intent()
    estimates = estimate_region_volumes(intent)
    assert len(estimates) == 22


@pytest.mark.parametrize("make_intent", ALL_INTENTS.values(), ids=ALL_INTENTS.keys())
def test_individuals_identical_across_entries(make_intent) -> None:
    intent = make_intent()
    estimates = estimate_region_volumes(intent)
    expected = _estimate_individuals(intent.populations)
    assert all(e.individuals == expected for e in estimates)


def test_q1_hla_brca1_chromosomes_and_ordering() -> None:
    """The Q1 intent (HLA + BRCA1) gives two entries on chromosomes 6 and 17,
    with the chr6 (HLA) entry the larger -- matching RFC-006-REVIEW.md
    section 8's expectation that HLA dominates the Q1 span."""
    intent = _regions_intent()
    estimates = estimate_region_volumes(intent)

    assert len(estimates) == 2
    chromosomes = {e.chromosome for e in estimates}
    assert chromosomes == {"6", "17"}

    by_chromosome = {e.chromosome: e for e in estimates}
    assert by_chromosome["6"].variants > by_chromosome["17"].variants
