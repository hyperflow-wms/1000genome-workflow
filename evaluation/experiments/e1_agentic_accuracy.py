#!/usr/bin/env python3
"""
E1 Agentic: Intent Extraction Accuracy via claude -p subprocess.

Same evaluation as e1_intent_accuracy.py but uses `claude -p` with
--json-schema for structured output instead of calling litellm+instructor
directly. Runs from a temp directory outside the repo so Claude cannot
discover skills files.

Usage:
    python e1_agentic_accuracy.py [--dataset PATH] [--output-dir PATH] [--skip-skills] [--model MODEL]
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Add paths for imports
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(REPO_ROOT / "workflow-composer" / "src"))

from e1_intent_accuracy import FieldScore, TierResult, score_single, load_dataset
from workflow_composer.core.models import ResearchIntent

# Maximum concurrent claude -p subprocesses
MAX_CONCURRENT = 3
SUBPROCESS_TIMEOUT = 120  # seconds
MAX_BUDGET_PER_QUERY = 0.50  # USD

# Skills files (same order as skill_loader.py)
SKILL_DIR = REPO_ROOT / "workflow-composer" / "src" / "workflow_composer" / "skills"
SKILL_FILES = [
    "SKILL.md",
    "populations.md",
    "genomic-regions.md",
    "research-contexts.md",
    "data-sources.md",
]


# ============================================================================
# System prompt builder
# ============================================================================

def load_skills_content() -> str:
    """Load all skill documents as a single context string.
    Replicates skill_loader.load_skill_context() without importing it.
    """
    parts = []
    for filename in SKILL_FILES:
        filepath = SKILL_DIR / filename
        if filepath.exists():
            content = filepath.read_text()
            parts.append(f"# {filename}\n\n{content}")
    return "\n\n---\n\n".join(parts)


def build_system_prompt(skip_skills: bool = False) -> str:
    """Build the system prompt, optionally with skills content inlined.
    Replicates the prompt template from llm_interpreter.py lines 72-100.
    """
    skill_context = "" if skip_skills else load_skills_content()

    return f"""You are a genomics research workflow planning assistant.

Your task is to interpret research questions and extract structured parameters
for workflow generation.

{skill_context}

Based on the user's question, extract:
1. analysis_type: What kind of analysis is being requested?
2. populations: Which population(s) are involved?
3. chromosomes: Which chromosome(s) if explicitly specified by number (null if not).
   Do NOT set chromosomes when a gene or region name is mentioned — use regions instead.
4. regions: If the user mentions a gene name (e.g., BRCA1, TP53, CFTR) or a named
   genomic region (e.g., HLA), look up its chromosome and coordinates in the
   genomic-regions.md table above and return the full GenomicRegion with name,
   chromosome, start, and end. This is REQUIRED whenever a gene or region name
   appears in the question.
5. focus: What type of variants to focus on?

IMPORTANT: When a gene name like BRCA1 or HLA is mentioned, you MUST populate
the regions field with the corresponding coordinates from the genomic-regions
table. Never leave regions as null when a known gene or region is referenced.

6. clarification_needed: If the question is too vague, missing critical parameters
   (e.g., no population specified, ambiguous scope), or contains invalid/unrecognizable
   terms that cannot be mapped to valid 1000 Genomes codes, set clarification_needed=True
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

    # Parse the JSON envelope
    # Envelope: {type, subtype, result, structured_output, total_cost_usd, num_turns, ...}
    output = json.loads(stdout_text)

    # Extract structured_output (primary) or fall back to result field
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
    index: int,
    total: int,
) -> dict:
    """Run a single query through claude -p, score it, return result dict."""
    qid = q["id"]
    tier = q["tier"]
    question = q["question"]
    gt = q["ground_truth"]

    async with semaphore:
        print(f"[{index}/{total}] {qid} ({tier}): {question[:60]}...")

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
            extra = ""
            if intent.clarification_needed:
                extra = f"  CLARIFICATION: {intent.clarification_reason}"
            print(f"  -> {status} ({duration:.1f}s, ${cr.cost_usd:.3f})  pops={scores['pred_pops']}  chroms={scores['pred_chroms']}  regions={scores['pred_regions']}{extra}")

            return result_entry

        except Exception as e:
            duration = time.time() - start_time
            print(f"  -> ERROR ({duration:.1f}s): {e}")
            return {
                "id": qid, "tier": tier, "question": question,
                "ground_truth": gt, "predicted": None,
                "scores": None, "error": str(e),
                "duration_seconds": round(duration, 2),
            }


# ============================================================================
# Main evaluation
# ============================================================================

async def run_evaluation_async(
    dataset_path: Path,
    output_dir: Path,
    skip_skills: bool = False,
    model: str = "opus",
):
    """Run full agentic evaluation via claude -p."""

    queries = load_dataset(dataset_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    system_prompt = build_system_prompt(skip_skills)
    json_schema_str = get_json_schema()

    # Create temp directory outside the repo for subprocess cwd
    tmp_dir = tempfile.mkdtemp(prefix="eval-agentic-")

    # Write empty MCP config to suppress all MCP servers
    empty_mcp_path = os.path.join(tmp_dir, "empty-mcp.json")
    with open(empty_mcp_path, "w") as f:
        json.dump({"mcpServers": {}}, f)

    if skip_skills:
        label = "agentic_without_skills"
    else:
        label = "agentic_with_skills"

    print(f"Running agentic evaluation ({label}) on {len(queries)} queries...")
    print(f"Model: {model}")
    print(f"Max concurrent: {MAX_CONCURRENT}")
    print(f"Working dir: {tmp_dir}")
    print(f"System prompt length: {len(system_prompt)} chars")
    print()

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    tasks = [
        run_single_query(
            semaphore, q, system_prompt, json_schema_str,
            model, tmp_dir, i + 1, len(queries)
        )
        for i, q in enumerate(queries)
    ]
    all_results = await asyncio.gather(*tasks)

    # Cleanup temp dir
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)

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
    results_file = output_dir / f"e1_agentic_results_{label}.json"
    with open(results_file, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nDetailed results: {results_file}")

    # ---- Write scores CSV ----
    scores_file = output_dir / f"e1_agentic_scores_{label}.csv"
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
    total_duration = sum(r.get("duration_seconds", 0) for r in all_results)
    total_cost = sum(r.get("cost_usd", 0) for r in all_results)
    total_errors = sum(1 for r in all_results if r.get("error"))
    if total_q:
        print(f"\nOverall: {total_match}/{total_q} full matches ({total_match/total_q*100:.1f}%)")
        print(f"Total time: {total_duration:.0f}s ({total_duration/total_q:.1f}s avg/query)")
        print(f"Total cost: ${total_cost:.2f} (${total_cost/total_q:.3f} avg/query)")
        if total_errors:
            print(f"Errors: {total_errors}/{total_q}")

    return all_results


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="E1 Agentic: Intent Extraction via Claude CLI"
    )
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
        help="Run without skills content in system prompt",
    )
    parser.add_argument(
        "--model", type=str, default="opus",
        help="Claude model alias (e.g. opus, sonnet) or full name",
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
