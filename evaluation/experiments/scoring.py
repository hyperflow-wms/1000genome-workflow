"""
Shared scoring utilities for intent extraction evaluation.

Provides:
- FieldScore: precision/recall for set-valued fields
- TierResult: aggregated per-tier metrics
- score_single: score one predicted ResearchIntent vs ground truth
- load_dataset: load queries.yaml
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class FieldScore:
    """Precision/recall for a single set-valued field."""
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom > 0 else 1.0

    @property
    def recall(self) -> float:
        denom = self.true_positives + self.false_negatives
        return self.true_positives / denom if denom > 0 else 1.0

    def update(self, predicted: set, expected: set):
        self.true_positives += len(predicted & expected)
        self.false_positives += len(predicted - expected)
        self.false_negatives += len(expected - predicted)


@dataclass
class TierResult:
    """Aggregated results for one tier."""
    tier: str
    total: int = 0
    full_match: int = 0
    clarification_correct: int = 0
    invalid_detected: int = 0
    populations: FieldScore = field(default_factory=FieldScore)
    chromosomes: FieldScore = field(default_factory=FieldScore)
    regions: FieldScore = field(default_factory=FieldScore)


def _region_key(name: str, chrom: str, start: int | None, end: int | None) -> tuple:
    """Normalize a region into a hashable (name, chromosome, start, end) tuple."""
    return (name, str(chrom), int(start) if start is not None else None,
            int(end) if end is not None else None)


def score_single(predicted, ground_truth: dict) -> dict:
    """Score a single predicted ResearchIntent against ground truth.

    Regions are compared by (name, chromosome, start, end) — not just name.
    """
    gt_pops = set(ground_truth.get("populations") or [])
    gt_chroms = set(ground_truth.get("chromosomes") or [])
    gt_regions = set()
    if ground_truth.get("regions"):
        gt_regions = {
            _region_key(r["name"], r["chromosome"], r.get("start"), r.get("end"))
            for r in ground_truth["regions"]
        }

    pred_pops = set(predicted.populations or [])
    pred_chroms = set(predicted.chromosomes or [])
    pred_regions = set()
    if predicted.regions:
        pred_regions = {
            _region_key(r.name, r.chromosome, r.start, r.end)
            for r in predicted.regions
        }

    pops_match = pred_pops == gt_pops
    chroms_match = pred_chroms == gt_chroms
    regions_match = pred_regions == gt_regions

    # Also track name-only match for diagnostics
    gt_region_names = {r[0] for r in gt_regions}
    pred_region_names = {r[0] for r in pred_regions}
    region_names_match = pred_region_names == gt_region_names

    gt_needs_clarification = ground_truth.get("clarification_needed", False)
    clarification_correct = (
        predicted.clarification_needed == gt_needs_clarification
    )

    invalid_terms = ground_truth.get("invalid_terms", [])
    invalid_avoided = True
    for inv in invalid_terms:
        term = inv["term"]
        if term in pred_pops:
            invalid_avoided = False

    full_match = pops_match and chroms_match and regions_match
    if gt_needs_clarification:
        full_match = full_match and clarification_correct

    return {
        "pops_match": pops_match,
        "chroms_match": chroms_match,
        "regions_match": regions_match,
        "region_names_match": region_names_match,
        "full_match": full_match,
        "clarification_needed_gt": gt_needs_clarification,
        "clarification_needed_pred": predicted.clarification_needed,
        "clarification_reason_pred": predicted.clarification_reason,
        "clarification_correct": clarification_correct,
        "invalid_terms": invalid_terms,
        "invalid_avoided": invalid_avoided,
        "pred_pops": sorted(pred_pops),
        "pred_chroms": sorted(pred_chroms),
        "pred_regions": sorted(str(r) for r in pred_regions),
        "gt_pops": sorted(gt_pops),
        "gt_chroms": sorted(gt_chroms),
        "gt_regions": sorted(str(r) for r in gt_regions),
    }


def load_dataset(path: Path) -> list[dict]:
    """Load queries from YAML dataset."""
    with open(path) as f:
        data = yaml.safe_load(f)
    return data["queries"]
