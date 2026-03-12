#!/usr/bin/env python3
"""
E1 Agentic: Intent Extraction Accuracy via claude -p with 4-mode skill ablation.

Skill modes:
    S0: no skills (LLM parametric knowledge only)
    S1: vocabulary skills (populations.md, genomic-regions.md)
    S2: strategy skills (research-contexts.md, data-sources.md)
    S3: all five skill documents

Usage:
    # Single mode
    python e1_agentic_accuracy.py --skill-mode S3

    # All modes
    python e1_agentic_accuracy.py --all-modes

    # Dry run
    python e1_agentic_accuracy.py --all-modes --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml
from tqdm import tqdm

# Add paths for imports
# v1_single_run/ is 4 levels below repo root: evaluation/experiments/v1_single_run/<file>
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
EXPERIMENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EXPERIMENTS_DIR))
sys.path.insert(0, str(REPO_ROOT / "workflow-composer" / "src"))

from scoring import FieldScore, TierResult, score_single, load_dataset, _region_key
from workflow_composer.core.models import ResearchIntent

# Maximum concurrent claude -p subprocesses
MAX_CONCURRENT = 3
SUBPROCESS_TIMEOUT = 120  # seconds
MAX_BUDGET_PER_QUERY = 0.50  # USD

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


# ============================================================================
# System prompt builder
# ============================================================================

def load_skills_content(skill_files: list[str]) -> str:
    """Load specified skill documents as a single context string."""
    parts = []
    for filename in skill_files:
        filepath = SKILL_DIR / filename
        if filepath.exists():
            content = filepath.read_text()
            parts.append(f"# {filename}\n\n{content}")
    return "\n\n---\n\n".join(parts)


def build_system_prompt(skill_mode: str) -> str:
    """Build the system prompt with skill-mode-aware instructions.

    Adapts instruction text based on which skills are loaded to avoid
    prompt leakage (referencing gene names, tables, or domain terms
    that aren't in the skill context).
    """
    skill_files = SKILL_MODES[skill_mode]
    skill_context = load_skills_content(skill_files)
    has_regions_table = "genomic-regions.md" in skill_files
    has_populations = "populations.md" in skill_files

    if has_regions_table:
        regions_instruction = (
            "4. regions: If the user mentions a gene name (e.g., BRCA1, TP53, CFTR) or a named\n"
            "   genomic region (e.g., HLA), look up its chromosome and coordinates in the\n"
            "   genomic-regions.md table above and return the full GenomicRegion with name,\n"
            "   chromosome, start, and end. This is REQUIRED whenever a gene or region name\n"
            "   appears in the question."
        )
        regions_emphasis = (
            "IMPORTANT: When a gene name like BRCA1 or HLA is mentioned, you MUST populate\n"
            "the regions field with the corresponding coordinates from the genomic-regions\n"
            "table. Never leave regions as null when a known gene or region is referenced."
        )
    else:
        regions_instruction = (
            "4. regions: If the user mentions a gene name or a named genomic region,\n"
            "   return the full GenomicRegion with name, chromosome, start, and end\n"
            "   if you can determine the coordinates. If you cannot determine exact\n"
            "   GRCh37/hg19 coordinates, set regions to null and note this in\n"
            "   clarification_reason."
        )
        regions_emphasis = (
            "IMPORTANT: Only populate the regions field if you are confident in the\n"
            "exact GRCh37/hg19 coordinates. If unsure, leave regions as null rather\n"
            "than guessing."
        )

    if has_populations:
        invalid_ref = "valid 1000 Genomes codes"
    else:
        invalid_ref = "valid population codes"

    return f"""You are a genomics research workflow planning assistant.

Your task is to interpret research questions and extract structured parameters
for workflow generation.

{skill_context}

Based on the user's question, extract:
1. analysis_type: What kind of analysis is being requested?
2. populations: Which population(s) are involved?
3. chromosomes: Which chromosome(s) if explicitly specified by number (null if not).
   Do NOT set chromosomes when a gene or region name is mentioned — use regions instead.
{regions_instruction}
5. focus: What type of variants to focus on?

{regions_emphasis}

6. clarification_needed: If the question is too vague, missing critical parameters
   (e.g., no population specified, ambiguous scope), or contains invalid/unrecognizable
   terms that cannot be mapped to {invalid_ref}, set clarification_needed=True
   and explain what is missing or invalid in clarification_reason.
   Still extract whatever parameters you CAN identify — but flag the gap.

Respond ONLY with the structured JSON output matching the provided schema. Do not explain or discuss.
"""


def get_json_schema() -> str:
    """Generate JSON Schema string from the ResearchIntent Pydantic model."""
    schema = ResearchIntent.model_json_schema()
    return json.dumps(schema)


# ============================================================================
# Claude subprocess runner
# ============================================================================

@dataclass
class ClaudeResult:
    """Result from a claude -p subprocess call."""
    intent: ResearchIntent
    cost_usd: float = 0.0
    num_turns: int = 0
    duration_api_ms: int = 0


async def run_claude_subprocess(
    query: str,
    system_prompt: str,
    json_schema_str: str,
    model: str,
    cwd: str,
) -> ClaudeResult:
    """Run a single query through claude -p and return parsed result."""
    cmd = [
        "claude", "-p", query,
        "--system-prompt", system_prompt,
        "--json-schema", json_schema_str,
        "--output-format", "json",
        "--tools", "",
        "--no-session-persistence",
        "--max-budget-usd", str(MAX_BUDGET_PER_QUERY),
        "--model", model,
        "--disable-slash-commands",
        "--setting-sources", "",
        "--mcp-config", os.path.join(cwd, "empty-mcp.json"),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout_bytes, stderr_bytes = await asyncio.wait_for(
        proc.communicate(), timeout=SUBPROCESS_TIMEOUT
    )

    if proc.returncode != 0:
        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"claude -p exited with code {proc.returncode}: {stderr_text[:500]}"
        )

    stdout_text = stdout_bytes.decode("utf-8").strip()
    if not stdout_text:
        raise RuntimeError("claude -p returned empty output")

    output = json.loads(stdout_text)

    if isinstance(output, dict) and output.get("structured_output"):
        data = output["structured_output"]
    elif isinstance(output, dict) and output.get("result"):
        result_val = output["result"]
        data = json.loads(result_val) if isinstance(result_val, str) else result_val
    else:
        data = output

    intent = ResearchIntent.model_validate(data)

    return ClaudeResult(
        intent=intent,
        cost_usd=output.get("total_cost_usd", 0.0) if isinstance(output, dict) else 0.0,
        num_turns=output.get("num_turns", 0) if isinstance(output, dict) else 0,
        duration_api_ms=output.get("duration_api_ms", 0) if isinstance(output, dict) else 0,
    )


# ============================================================================
# Single query runner
# ============================================================================

async def run_single_query(
    semaphore: asyncio.Semaphore,
    q: dict,
    system_prompt: str,
    json_schema_str: str,
    model: str,
    cwd: str,
    pbar: tqdm,
) -> dict:
    """Run a single query through claude -p, score it, return result dict."""
    qid = q["id"]
    tier = q["tier"]
    question = q["question"]
    gt = q["ground_truth"]

    async with semaphore:
        start_time = time.time()
        try:
            cr = await run_claude_subprocess(
                query=question,
                system_prompt=system_prompt,
                json_schema_str=json_schema_str,
                model=model,
                cwd=cwd,
            )
            duration = time.time() - start_time
            intent = cr.intent

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
                "cost_usd": cr.cost_usd,
                "num_turns": cr.num_turns,
            }

            status = "MATCH" if scores["full_match"] else "MISMATCH"
            pbar.update(1)
            pbar.set_postfix_str(f"{qid} {status} {duration:.0f}s ${cr.cost_usd:.3f}")

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
    system_prompt_len: int,
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
    output = {
        "metadata": {
            "skill_mode": skill_mode,
            "skill_files": SKILL_MODES[skill_mode],
            "model": model,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "system_prompt_length": system_prompt_len,
            "n_queries": len(all_results),
            "n_errors": sum(1 for r in all_results if r.get("error")),
            "total_cost_usd": sum(r.get("cost_usd", 0) for r in all_results),
            "total_duration_seconds": sum(r.get("duration_seconds", 0) for r in all_results),
        },
        "results": all_results,
    }
    with open(results_file, "w") as f:
        json.dump(output, f, indent=2)

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
    total_cost = sum(r.get("cost_usd", 0) for r in all_results)
    total_errors = sum(1 for r in all_results if r.get("error"))
    if total_q:
        print(f"\nOverall: {total_match}/{total_q} full matches ({total_match/total_q*100:.1f}%)")
        print(f"Total time: {total_duration:.0f}s ({total_duration/total_q:.1f}s avg/query)")
        print(f"Total cost: ${total_cost:.2f} (${total_cost/total_q:.3f} avg/query)")
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
    system_prompt = build_system_prompt(skill_mode)
    json_schema_str = get_json_schema()

    # Create temp directory outside the repo for subprocess cwd
    tmp_dir = tempfile.mkdtemp(prefix="eval-agentic-")
    empty_mcp_path = os.path.join(tmp_dir, "empty-mcp.json")
    with open(empty_mcp_path, "w") as f:
        json.dump({"mcpServers": {}}, f)

    print(f"Running {skill_mode} on {len(queries)} queries...")
    print(f"  Model: {model}")
    print(f"  Skills: {SKILL_MODES[skill_mode] or '(none)'}")
    print(f"  Max concurrent: {MAX_CONCURRENT}")
    print(f"  Working dir: {tmp_dir}")
    print(f"  System prompt: {len(system_prompt)} chars")
    print()

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    pbar = tqdm(total=len(queries), desc=skill_mode, unit="q")
    tasks = [
        run_single_query(
            semaphore, q, system_prompt, json_schema_str,
            model, tmp_dir, pbar,
        )
        for q in queries
    ]
    all_results = await asyncio.gather(*tasks)
    pbar.close()

    shutil.rmtree(tmp_dir, ignore_errors=True)

    aggregate_and_write(all_results, output_dir, skill_mode, model, len(system_prompt))


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="E1 Agentic: Intent Extraction with Skill Mode Ablation"
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
        "--model", type=str, default="opus",
        help="Claude model alias (e.g. opus, sonnet) or full name",
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
        est_cost = n_queries * 0.05
        est_hours = n_queries * 20 / 3 / 3600
        print(f"Dry run estimate:")
        print(f"  Modes: {modes}")
        print(f"  Total queries: {n_queries}")
        print(f"  Est. cost: ${est_cost:.0f}")
        print(f"  Est. time: {est_hours:.1f} hours")
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
