#!/usr/bin/env python3
"""
E1: Intent Extraction Accuracy
E2: Skills Ablation (same script with --skip-skills)

Runs each query through the Workflow Composer's LLM interpreter,
compares extracted ResearchIntent to ground truth, and computes
per-field precision/recall and overall intent match accuracy.

Usage:
    python e1_intent_accuracy.py [--dataset PATH] [--output-dir PATH] [--skip-skills] [--model MODEL]
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
from pathlib import Path
from dataclasses import dataclass, field

import yaml

# Add workflow-composer src to path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "workflow-composer" / "src"))

from workflow_composer.interpretation.llm_interpreter import (
    interpret_research_question, LLMConfig,
)
from workflow_composer.interpretation import skill_loader

# Maximum concurrent LLM calls
MAX_CONCURRENT = 5


# ============================================================================
# Scoring
# ============================================================================

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
    clarification_correct: int = 0  # T4: correctly requested clarification
    invalid_detected: int = 0       # T5: correctly avoided invalid terms
    populations: FieldScore = field(default_factory=FieldScore)
    chromosomes: FieldScore = field(default_factory=FieldScore)
    regions: FieldScore = field(default_factory=FieldScore)


def score_single(predicted, ground_truth: dict) -> dict:
    """Score a single predicted ResearchIntent against ground truth."""
    gt_pops = set(ground_truth.get("populations") or [])
    gt_chroms = set(ground_truth.get("chromosomes") or [])
    gt_regions = set()
    if ground_truth.get("regions"):
        gt_regions = {r["name"] for r in ground_truth["regions"]}

    pred_pops = set(predicted.populations or [])
    pred_chroms = set(predicted.chromosomes or [])
    pred_regions = set()
    if predicted.regions:
        pred_regions = {r.name for r in predicted.regions}

    pops_match = pred_pops == gt_pops
    chroms_match = pred_chroms == gt_chroms
    regions_match = pred_regions == gt_regions

    # T4: check clarification behavior
    gt_needs_clarification = ground_truth.get("clarification_needed", False)
    clarification_correct = (
        predicted.clarification_needed == gt_needs_clarification
    )

    # T5: check invalid term handling
    invalid_terms = ground_truth.get("invalid_terms", [])
    invalid_avoided = True
    for inv in invalid_terms:
        term = inv["term"]
        # Check the invalid term didn't end up in populations
        if term in pred_pops:
            invalid_avoided = False

    # Full match: all fields correct (and clarification correct if applicable)
    full_match = pops_match and chroms_match and regions_match
    if gt_needs_clarification:
        full_match = full_match and clarification_correct

    return {
        "pops_match": pops_match,
        "chroms_match": chroms_match,
        "regions_match": regions_match,
        "full_match": full_match,
        "clarification_needed_gt": gt_needs_clarification,
        "clarification_needed_pred": predicted.clarification_needed,
        "clarification_reason_pred": predicted.clarification_reason,
        "clarification_correct": clarification_correct,
        "invalid_terms": invalid_terms,
        "invalid_avoided": invalid_avoided,
        "pred_pops": sorted(pred_pops),
        "pred_chroms": sorted(pred_chroms),
        "pred_regions": sorted(pred_regions),
        "gt_pops": sorted(gt_pops),
        "gt_chroms": sorted(gt_chroms),
        "gt_regions": sorted(gt_regions),
    }


# ============================================================================
# Async runner
# ============================================================================

async def run_single_query(
    semaphore: asyncio.Semaphore,
    q: dict,
    config: LLMConfig,
    index: int,
    total: int,
) -> dict:
    """Run a single query through the interpreter (in a thread for sync API)."""
    qid = q["id"]
    tier = q["tier"]
    question = q["question"]
    gt = q["ground_truth"]

    async with semaphore:
        print(f"[{index}/{total}] {qid} ({tier}): {question[:60]}...")

        try:
            loop = asyncio.get_event_loop()
            intent = await loop.run_in_executor(
                None,
                lambda: interpret_research_question(question, config)
            )

            scores = score_single(intent, gt)

            result_entry = {
                "id": qid,
                "tier": tier,
                "question": question,
                "ground_truth": gt,
                "predicted": {
                    "analysis_type": intent.analysis_type,
                    "populations": list(intent.populations or []),
                    "chromosomes": list(intent.chromosomes) if intent.chromosomes else None,
                    "regions": [
                        {"name": r.name, "chromosome": r.chromosome,
                         "start": r.start, "end": r.end}
                        for r in (intent.regions or [])
                    ] or None,
                    "focus": intent.focus,
                    "clarification_needed": intent.clarification_needed,
                    "clarification_reason": intent.clarification_reason,
                },
                "scores": scores,
            }

            status = "MATCH" if scores["full_match"] else "MISMATCH"
            extra = ""
            if intent.clarification_needed:
                extra = f"  CLARIFICATION: {intent.clarification_reason}"
            print(f"  -> {status}  pops={scores['pred_pops']}  chroms={scores['pred_chroms']}  regions={scores['pred_regions']}{extra}")

            return result_entry

        except Exception as e:
            print(f"  -> ERROR: {e}")
            return {
                "id": qid, "tier": tier, "question": question,
                "ground_truth": gt, "predicted": None,
                "scores": None, "error": str(e),
            }


# ============================================================================
# Main evaluation
# ============================================================================

def load_dataset(path: Path) -> list[dict]:
    """Load queries from YAML dataset."""
    with open(path) as f:
        data = yaml.safe_load(f)
    return data["queries"]


async def run_evaluation_async(
    dataset_path: Path,
    output_dir: Path,
    skip_skills: bool = False,
    model: str | None = None,
):
    """Run full E1 (or E2 with skip_skills=True) evaluation."""

    queries = load_dataset(dataset_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    config = LLMConfig()
    if model:
        config.model = model

    # E2 ablation: monkey-patch skill_loader to return empty context
    if skip_skills:
        original_load = skill_loader.load_skill_context
        skill_loader.load_skill_context = lambda: ""
        label = "without_skills"
    else:
        label = "with_skills"

    print(f"Running evaluation ({label}) on {len(queries)} queries...")
    print(f"Model: {config.model}")
    print(f"Max concurrent: {MAX_CONCURRENT}")
    print()

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    tasks = [
        run_single_query(semaphore, q, config, i + 1, len(queries))
        for i, q in enumerate(queries)
    ]
    all_results = await asyncio.gather(*tasks)

    # Restore original function if patched
    if skip_skills:
        skill_loader.load_skill_context = original_load

    # ---- Aggregate per-tier ----
    tier_results: dict[str, TierResult] = {}
    for r in all_results:
        tier = r["tier"]
        if tier not in tier_results:
            tier_results[tier] = TierResult(tier=tier)
        tr = tier_results[tier]
        tr.total += 1

        scores = r.get("scores")
        if scores is None:
            continue

        gt = r["ground_truth"]
        gt_pops = set(gt.get("populations") or [])
        gt_chroms = set(gt.get("chromosomes") or [])
        gt_regions = set()
        if gt.get("regions"):
            gt_regions = {reg["name"] for reg in gt["regions"]}

        pred = r["predicted"]
        pred_pops = set(pred["populations"] or [])
        pred_chroms = set(pred["chromosomes"] or []) if pred["chromosomes"] else set()
        pred_regions = set()
        if pred["regions"]:
            pred_regions = {reg["name"] for reg in pred["regions"]}

        tr.populations.update(pred_pops, gt_pops)
        tr.chromosomes.update(pred_chroms, gt_chroms)
        tr.regions.update(pred_regions, gt_regions)

        if scores["full_match"]:
            tr.full_match += 1
        if scores.get("clarification_correct"):
            tr.clarification_correct += 1
        if scores.get("invalid_avoided"):
            tr.invalid_detected += 1

    # ---- Write detailed results JSON ----
    results_file = output_dir / f"e1_results_{label}.json"
    with open(results_file, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nDetailed results: {results_file}")

    # ---- Write scores CSV ----
    scores_file = output_dir / f"e1_scores_{label}.csv"
    with open(scores_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "tier", "n",
            "pop_precision", "pop_recall",
            "chrom_precision", "chrom_recall",
            "region_precision", "region_recall",
            "full_match_pct",
            "clarification_correct_pct",
            "invalid_avoided_pct",
        ])
        for tier in sorted(tier_results):
            tr = tier_results[tier]
            writer.writerow([
                tr.tier, tr.total,
                f"{tr.populations.precision:.3f}", f"{tr.populations.recall:.3f}",
                f"{tr.chromosomes.precision:.3f}", f"{tr.chromosomes.recall:.3f}",
                f"{tr.regions.precision:.3f}", f"{tr.regions.recall:.3f}",
                f"{tr.full_match / tr.total * 100:.1f}" if tr.total > 0 else "0.0",
                f"{tr.clarification_correct / tr.total * 100:.1f}" if tr.total > 0 else "N/A",
                f"{tr.invalid_detected / tr.total * 100:.1f}" if tr.total > 0 else "N/A",
            ])
    print(f"Scores table:    {scores_file}")

    # ---- Print summary ----
    print(f"\n{'='*70}")
    print(f"Summary ({label})")
    print(f"{'='*70}")
    print(f"{'Tier':<6} {'N':>4} {'Pop P/R':>10} {'Chr P/R':>10} {'Reg P/R':>10} {'Match%':>8} {'Clar%':>7} {'Inv%':>7}")
    print(f"{'-'*6} {'-'*4} {'-'*10} {'-'*10} {'-'*10} {'-'*8} {'-'*7} {'-'*7}")
    for tier in sorted(tier_results):
        tr = tier_results[tier]
        pp, pr = tr.populations.precision, tr.populations.recall
        cp, cr = tr.chromosomes.precision, tr.chromosomes.recall
        rp, rr = tr.regions.precision, tr.regions.recall
        fm = tr.full_match / tr.total * 100 if tr.total > 0 else 0
        cc = tr.clarification_correct / tr.total * 100 if tr.total > 0 else 0
        ia = tr.invalid_detected / tr.total * 100 if tr.total > 0 else 0
        print(f"{tr.tier:<6} {tr.total:>4} {pp:.0%}/{pr:.0%}{'':<2} {cp:.0%}/{cr:.0%}{'':<2} {rp:.0%}/{rr:.0%}{'':<2} {fm:>6.1f}% {cc:>5.1f}% {ia:>5.1f}%")

    total_q = sum(tr.total for tr in tier_results.values())
    total_match = sum(tr.full_match for tr in tier_results.values())
    if total_q:
        print(f"\nOverall: {total_match}/{total_q} full matches ({total_match/total_q*100:.1f}%)")

    return all_results


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="E1: Intent Extraction Accuracy")
    parser.add_argument(
        "--dataset", type=Path,
        default=Path(__file__).parent.parent / "datasets" / "intent-extraction" / "queries.yaml",
        help="Path to queries.yaml",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path(__file__).parent.parent / "results",
        help="Output directory for results",
    )
    parser.add_argument(
        "--skip-skills", action="store_true",
        help="E2 mode: run without Skills (ablation study)",
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="LLM model override (e.g. openai/gpt-5.4)",
    )
    args = parser.parse_args()

    asyncio.run(run_evaluation_async(
        dataset_path=args.dataset,
        output_dir=args.output_dir,
        skip_skills=args.skip_skills,
        model=args.model,
    ))


if __name__ == "__main__":
    main()
