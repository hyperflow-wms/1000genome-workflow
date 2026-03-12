#!/usr/bin/env python3
"""
E1: Intent Extraction Accuracy with 4-mode skill ablation.

Skill modes:
    S0: no skills (LLM parametric knowledge only)
    S1: vocabulary skills (populations.md, genomic-regions.md)
    S2: strategy skills (research-contexts.md, data-sources.md)
    S3: all five skill documents

Usage:
    # Single mode
    python e1_intent_accuracy.py --skill-mode S3 --model openai/gpt-4.1-mini

    # All modes
    python e1_intent_accuracy.py --all-modes --model openai/gpt-4.1-mini

    # Dry run
    python e1_intent_accuracy.py --all-modes --model openai/gpt-4.1-mini --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
from pathlib import Path

from tqdm import tqdm

# Add paths for imports
# v1_single_run/ is 4 levels below repo root: evaluation/experiments/v1_single_run/<file>
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
EXPERIMENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EXPERIMENTS_DIR))
sys.path.insert(0, str(REPO_ROOT / "workflow-composer" / "src"))

from scoring import FieldScore, TierResult, score_single, load_dataset, _region_key
from workflow_composer.interpretation.llm_interpreter import (
    interpret_research_question, LLMConfig,
)
from workflow_composer.interpretation import llm_interpreter as _llm_mod

# Maximum concurrent LLM calls
MAX_CONCURRENT = 15

# Skills directory
SKILL_DIR = REPO_ROOT / "workflow-composer" / "src" / "workflow_composer" / "skills"

# Skill mode definitions
SKILL_MODES = {
    "S0": [],
    "S1": ["populations.md", "genomic-regions.md"],
    "S2": ["research-contexts.md", "data-sources.md"],
    "S3": ["SKILL.md", "populations.md", "genomic-regions.md",
           "research-contexts.md", "data-sources.md"],
}


def _load_skills_for_mode(skill_files: list[str]) -> str:
    """Load specified skill documents as a single context string."""
    parts = []
    for filename in skill_files:
        filepath = SKILL_DIR / filename
        if filepath.exists():
            content = filepath.read_text()
            parts.append(f"# {filename}\n\n{content}")
    return "\n\n---\n\n".join(parts)


# ============================================================================
# Single query runner
# ============================================================================

async def run_single_query(
    semaphore: asyncio.Semaphore,
    q: dict,
    config: LLMConfig,
    pbar: tqdm,
) -> dict:
    """Run a single query through the interpreter, score it, return result dict."""
    qid = q["id"]
    tier = q["tier"]
    question = q["question"]
    gt = q["ground_truth"]

    async with semaphore:
        start_time = time.time()
        try:
            loop = asyncio.get_event_loop()
            intent = await loop.run_in_executor(
                None,
                lambda: interpret_research_question(question, config)
            )
            duration = time.time() - start_time

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
                "duration_seconds": round(duration, 2),
            }

            status = "MATCH" if scores["full_match"] else "MISMATCH"
            pbar.update(1)
            pbar.set_postfix_str(f"{qid} {status} {duration:.0f}s")

            return result_entry

        except Exception as e:
            duration = time.time() - start_time
            pbar.update(1)
            pbar.set_postfix_str(f"{qid} ERROR {duration:.0f}s")
            return {
                "id": qid, "tier": tier, "question": question,
                "ground_truth": gt, "predicted": None,
                "scores": None, "error": str(e),
                "duration_seconds": round(duration, 2),
            }


# ============================================================================
# Aggregation and output
# ============================================================================

def aggregate_and_write(
    all_results: list[dict],
    output_dir: Path,
    skill_mode: str,
    model: str,
):
    """Aggregate per-tier scores and write results.json + scores.csv."""
    output_dir.mkdir(parents=True, exist_ok=True)

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
            gt_regions = {
                _region_key(reg["name"], reg["chromosome"], reg.get("start"), reg.get("end"))
                for reg in gt["regions"]
            }

        pred = r["predicted"]
        pred_pops = set(pred["populations"] or [])
        pred_chroms = set(pred["chromosomes"] or []) if pred["chromosomes"] else set()
        pred_regions = set()
        if pred["regions"]:
            pred_regions = {
                _region_key(reg["name"], reg["chromosome"], reg.get("start"), reg.get("end"))
                for reg in pred["regions"]
            }

        tr.populations.update(pred_pops, gt_pops)
        tr.chromosomes.update(pred_chroms, gt_chroms)
        tr.regions.update(pred_regions, gt_regions)

        if scores["full_match"]:
            tr.full_match += 1
        if scores.get("clarification_correct"):
            tr.clarification_correct += 1
        if scores.get("invalid_avoided"):
            tr.invalid_detected += 1

    # Write results.json
    results_file = output_dir / f"e1_results_{skill_mode}.json"
    with open(results_file, "w") as f:
        json.dump(all_results, f, indent=2)

    # Write scores.csv
    scores_file = output_dir / f"e1_scores_{skill_mode}.csv"
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

    # Print summary
    print(f"\n{'='*70}")
    print(f"Summary ({skill_mode} / {model})")
    print(f"{'='*70}")
    print(f"{'Tier':<6} {'N':>4} {'Pop P/R':>10} {'Chr P/R':>10} {'Reg P/R':>10} {'Match%':>8} {'Clar%':>7} {'Inv%':>7}")
    print(f"{'-'*6} {'-'*4} {'-'*10} {'-'*10} {'-'*10} {'-'*8} {'-'*7} {'-'*7}")
    for tier in sorted(tier_results):
        tr = tier_results[tier]
        pp, pr_ = tr.populations.precision, tr.populations.recall
        cp, cr_ = tr.chromosomes.precision, tr.chromosomes.recall
        rp, rr = tr.regions.precision, tr.regions.recall
        fm = tr.full_match / tr.total * 100 if tr.total > 0 else 0
        cc = tr.clarification_correct / tr.total * 100 if tr.total > 0 else 0
        ia = tr.invalid_detected / tr.total * 100 if tr.total > 0 else 0
        print(f"{tr.tier:<6} {tr.total:>4} {pp:.0%}/{pr_:.0%}{'':<2} {cp:.0%}/{cr_:.0%}{'':<2} {rp:.0%}/{rr:.0%}{'':<2} {fm:>6.1f}% {cc:>5.1f}% {ia:>5.1f}%")

    total_q = sum(tr.total for tr in tier_results.values())
    total_match = sum(tr.full_match for tr in tier_results.values())
    total_duration = sum(r.get("duration_seconds", 0) for r in all_results)
    total_errors = sum(1 for r in all_results if r.get("error"))
    if total_q:
        print(f"\nOverall: {total_match}/{total_q} full matches ({total_match/total_q*100:.1f}%)")
        print(f"Total time: {total_duration:.0f}s ({total_duration/total_q:.1f}s avg/query)")
        if total_errors:
            print(f"Errors: {total_errors}/{total_q}")

    print(f"\nResults: {results_file}")
    print(f"Scores:  {scores_file}")


# ============================================================================
# Main evaluation (single mode)
# ============================================================================

async def run_evaluation_async(
    dataset_path: Path,
    output_dir: Path,
    skill_mode: str,
    model: str,
):
    """Run one evaluation for a single skill mode."""
    queries = load_dataset(dataset_path)

    config = LLMConfig()
    config.model = model
    skill_files = SKILL_MODES[skill_mode]

    print(f"Running {skill_mode} on {len(queries)} queries...")
    print(f"  Model: {model}")
    print(f"  Skills: {skill_files or '(none)'}")
    print(f"  Max concurrent: {MAX_CONCURRENT}")
    print()

    # Monkey-patch the local binding in llm_interpreter (not skill_loader),
    # because llm_interpreter uses `from .skill_loader import load_skill_context`
    # which creates a local reference that patching skill_loader doesn't affect.
    original_fn = _llm_mod.load_skill_context
    _llm_mod.load_skill_context = lambda: _load_skills_for_mode(skill_files)

    try:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        pbar = tqdm(total=len(queries), desc=skill_mode, unit="q")
        tasks = [
            run_single_query(semaphore, q, config, pbar)
            for q in queries
        ]
        all_results = await asyncio.gather(*tasks)
        pbar.close()

        aggregate_and_write(all_results, output_dir, skill_mode, model)
    finally:
        _llm_mod.load_skill_context = original_fn


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="E1: Intent Extraction with Skill Mode Ablation"
    )
    parser.add_argument(
        "--dataset", type=Path,
        default=EXPERIMENTS_DIR.parent / "datasets" / "intent-extraction" / "queries.yaml",
        help="Path to queries.yaml",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=EXPERIMENTS_DIR.parent / "results" / "v1_single_run",
        help="Output directory for results",
    )
    parser.add_argument(
        "--skill-mode", type=str, choices=list(SKILL_MODES.keys()),
        default=None,
        help="Skill mode: S0=none, S1=vocabulary, S2=strategy, S3=all",
    )
    parser.add_argument(
        "--all-modes", action="store_true",
        help="Run all four skill modes sequentially",
    )
    parser.add_argument(
        "--model", type=str, required=True,
        help="LLM model (e.g. openai/gpt-4.1-mini, openai/gpt-5.4)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print estimated cost and time without executing",
    )
    args = parser.parse_args()

    if args.all_modes:
        modes = list(SKILL_MODES.keys())
    elif args.skill_mode:
        modes = [args.skill_mode]
    else:
        parser.error("Specify --skill-mode or --all-modes")

    if args.dry_run:
        n_queries = len(modes) * 150
        print(f"Dry run estimate:")
        print(f"  Modes: {modes}")
        print(f"  Total queries: {n_queries}")
        print(f"  Est. time: {n_queries * 5 / 5 / 60:.0f} min (5s/query, 5 concurrent)")
        return

    model_slug = args.model.split("/")[-1] if "/" in args.model else args.model
    output_dir = args.output_dir / model_slug

    for mode in modes:
        print(f"\n{'#'*70}")
        print(f"# Mode: {mode}")
        print(f"# Skills: {SKILL_MODES[mode] or '(none)'}")
        print(f"# Model: {args.model}")
        print(f"{'#'*70}\n")
        asyncio.run(run_evaluation_async(
            dataset_path=args.dataset,
            output_dir=output_dir,
            skill_mode=mode,
            model=args.model,
        ))

    print(f"\nAll modes complete. Results in: {output_dir}")


if __name__ == "__main__":
    main()
