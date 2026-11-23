#!/usr/bin/env python3
"""
E1 Codex: Intent Extraction Accuracy via `codex exec` with 4-mode skill ablation.

Mirrors e1_agentic_accuracy.py but drives OpenAI's Codex CLI instead of
`claude -p`, so GPT-family models remain reachable when the OpenAI API
account has no active billing (Codex authenticates via ChatGPT).

Two differences from the claude -p harness are load-bearing and are recorded
in the output metadata rather than papered over:

1. Codex prepends its own agentic instructions to every request and offers no
   way to replace them -- `model_instructions_file` appends. Measured floor is
   ~10.8k input tokens before any skill context. The S0 baseline is therefore
   NOT "parametric knowledge only" the way it is under claude -p; treat S0
   numbers from this runner as a lower bound on context, not a clean baseline.

2. OpenAI structured output runs in strict mode, which requires
   `additionalProperties: false` on every object and every property listed in
   `required`. ResearchIntent's schema is transformed accordingly, so the model
   must emit all fields rather than omitting them.

Usage:
    python e1_codex_accuracy.py --skill-mode S0 --model gpt-5.6-luna
    python e1_codex_accuracy.py --all-modes --model gpt-5.6-luna
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
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path

from tqdm import tqdm

# v1_single_run/ is 4 levels below repo root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
EXPERIMENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EXPERIMENTS_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO_ROOT / "workflow-composer" / "src"))

from scoring import TierResult, score_single, load_dataset, _region_key
from workflow_composer.core.models import ResearchIntent

# Reuse the exact prompt construction from the claude -p harness so the only
# variable between runners is the backend.
from e1_agentic_accuracy import build_system_prompt, SKILL_MODES

CODEX_BIN = os.path.expanduser("~/.local/bin/codex")
DEFAULT_MODEL = "gpt-5.6-luna"
SUBPROCESS_TIMEOUT = 240
MAX_RETRIES = 3


# ============================================================================
# Schema: OpenAI strict mode
# ============================================================================

def make_strict(node):
    """Recursively enforce OpenAI strict-mode schema requirements."""
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            node["additionalProperties"] = False
            if "properties" in node:
                node["required"] = list(node["properties"].keys())
        for value in node.values():
            make_strict(value)
    elif isinstance(node, list):
        for value in node:
            make_strict(value)
    return node


# ============================================================================
# Codex subprocess runner
# ============================================================================

@dataclass
class CodexResult:
    intent: ResearchIntent
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0


async def run_codex_subprocess(
    query: str,
    instructions_path: Path,
    schema_path: Path,
    model: str,
    cwd: str,
) -> CodexResult:
    """Run one query through `codex exec` and return the parsed intent."""
    out_fd, out_path = tempfile.mkstemp(suffix=".json", dir=cwd)
    os.close(out_fd)

    cmd = [
        CODEX_BIN, "exec",
        "-m", model,
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "-s", "read-only",
        "--json",
        "-c", "include_environment_context=false",
        "-c", "project_doc_max_bytes=0",
        "-c", "tools.web_search=false",
        "-c", f"model_instructions_file={instructions_path}",
        "--output-schema", str(schema_path),
        "-o", out_path,
        query,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=SUBPROCESS_TIMEOUT
        )

        stdout_text = stdout_bytes.decode("utf-8", errors="replace")

        # Usage comes from the turn.completed event on the JSONL stream.
        usage = {}
        for line in stdout_text.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "turn.completed" and "usage" in event:
                usage = event["usage"]

        raw = Path(out_path).read_text().strip() if os.path.exists(out_path) else ""
        if not raw:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"codex produced no output (rc={proc.returncode}): {stderr_text[:400]}"
            )

        intent = ResearchIntent.model_validate(json.loads(raw))

        return CodexResult(
            intent=intent,
            input_tokens=usage.get("input_tokens", 0),
            cached_input_tokens=usage.get("cached_input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            reasoning_tokens=usage.get("reasoning_output_tokens", 0),
        )
    finally:
        if os.path.exists(out_path):
            os.unlink(out_path)


# ============================================================================
# Single query runner
# ============================================================================

async def run_single_query(
    semaphore: asyncio.Semaphore,
    q: dict,
    instructions_path: Path,
    schema_path: Path,
    model: str,
    cwd: str,
    pbar: tqdm,
) -> dict:
    qid, tier = q["id"], q["tier"]
    question, gt = q["question"], q["ground_truth"]

    async with semaphore:
        start_time = time.time()
        last_error = None

        for attempt in range(MAX_RETRIES):
            try:
                cr = await run_codex_subprocess(
                    query=question,
                    instructions_path=instructions_path,
                    schema_path=schema_path,
                    model=model,
                    cwd=cwd,
                )
                duration = time.time() - start_time
                intent = cr.intent
                scores = score_single(intent, gt)

                status = "MATCH" if scores["full_match"] else "MISMATCH"
                pbar.update(1)
                pbar.set_postfix_str(
                    f"{qid} {status} {duration:.0f}s {cr.input_tokens // 1000}k tok"
                )

                return {
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
                    "input_tokens": cr.input_tokens,
                    "cached_input_tokens": cr.cached_input_tokens,
                    "output_tokens": cr.output_tokens,
                    "reasoning_tokens": cr.reasoning_tokens,
                    "attempts": attempt + 1,
                }

            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    # Back off; free-tier rate limits are the expected failure.
                    await asyncio.sleep(5 * (2 ** attempt))

        duration = time.time() - start_time
        pbar.update(1)
        pbar.set_postfix_str(f"{qid} ERROR {duration:.0f}s")
        return {
            "id": qid, "tier": tier, "question": question,
            "ground_truth": gt, "predicted": None, "scores": None,
            "error": str(last_error),
            "duration_seconds": round(duration, 2),
            "attempts": MAX_RETRIES,
        }


# ============================================================================
# Aggregation
# ============================================================================

def aggregate_and_write(all_results, output_dir, skill_mode, model, system_prompt_len):
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

    results_file = output_dir / f"e1_results_{skill_mode}.json"
    output = {
        "metadata": {
            "skill_mode": skill_mode,
            "skill_files": SKILL_MODES[skill_mode],
            "model": model,
            "backend": "codex exec",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "system_prompt_length": system_prompt_len,
            "n_queries": len(all_results),
            "n_errors": sum(1 for r in all_results if r.get("error")),
            "total_input_tokens": sum(r.get("input_tokens", 0) for r in all_results),
            "total_output_tokens": sum(r.get("output_tokens", 0) for r in all_results),
            "total_reasoning_tokens": sum(r.get("reasoning_tokens", 0) for r in all_results),
            "total_duration_seconds": sum(r.get("duration_seconds", 0) for r in all_results),
            "caveat": (
                "Codex prepends non-removable agentic instructions. Measured floor "
                "under this runner's flag set is ~7.5k input tokens before any skill "
                "context (min observed 7,905 including the 1,499-char base prompt). "
                "S0 is not a clean parametric-knowledge baseline under this backend."
            ),
        },
        "results": all_results,
    }
    with open(results_file, "w") as f:
        json.dump(output, f, indent=2)

    scores_file = output_dir / f"e1_scores_{skill_mode}.csv"
    with open(scores_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "tier", "n",
            "pop_precision", "pop_recall",
            "chrom_precision", "chrom_recall",
            "region_precision", "region_recall",
            "full_match_pct", "clarification_correct_pct", "invalid_avoided_pct",
        ])
        for tier in sorted(tier_results):
            tr = tier_results[tier]
            writer.writerow([
                tr.tier, tr.total,
                f"{tr.populations.precision:.3f}", f"{tr.populations.recall:.3f}",
                f"{tr.chromosomes.precision:.3f}", f"{tr.chromosomes.recall:.3f}",
                f"{tr.regions.precision:.3f}", f"{tr.regions.recall:.3f}",
                f"{100 * tr.full_match / tr.total:.1f}" if tr.total else "0.0",
                f"{100 * tr.clarification_correct / tr.total:.1f}" if tr.total else "0.0",
                f"{100 * tr.invalid_detected / tr.total:.1f}" if tr.total else "0.0",
            ])

    # Console summary
    print("\n" + "=" * 70)
    print(f"Summary ({skill_mode} / {model} via codex exec)")
    print("=" * 70)
    print(f"{'Tier':<6} {'N':>4} {'Pop P/R':>10} {'Chr P/R':>10} {'Reg P/R':>10} "
          f"{'Match%':>8} {'Clar%':>7} {'Inv%':>7}")
    print(f"{'-'*6} {'-'*4} {'-'*10} {'-'*10} {'-'*10} {'-'*8} {'-'*7} {'-'*7}")
    for tier in sorted(tier_results):
        tr = tier_results[tier]
        print(
            f"{tr.tier:<6} {tr.total:>4} "
            f"{tr.populations.precision:.0%}/{tr.populations.recall:.0%}".ljust(30)
            + f"{tr.chromosomes.precision:.0%}/{tr.chromosomes.recall:.0%}".ljust(12)
            + f"{tr.regions.precision:.0%}/{tr.regions.recall:.0%}".ljust(12)
            + f"{100*tr.full_match/tr.total:>7.1f}% "
            + f"{100*tr.clarification_correct/tr.total:>6.1f}% "
            + f"{100*tr.invalid_detected/tr.total:>6.1f}%"
        )

    total = len(all_results)
    matches = sum(1 for r in all_results if (r.get("scores") or {}).get("full_match"))
    errors = sum(1 for r in all_results if r.get("error"))
    tok_in = sum(r.get("input_tokens", 0) for r in all_results)
    tok_out = sum(r.get("output_tokens", 0) for r in all_results)
    print(f"\nOverall: {matches}/{total} full matches ({100*matches/total:.1f}%)")
    print(f"Tokens: {tok_in:,} in / {tok_out:,} out")
    if errors:
        print(f"Errors: {errors}/{total}")
    print(f"\nResults: {results_file}")
    print(f"Scores:  {scores_file}")


# ============================================================================
# Main
# ============================================================================

async def run_evaluation_async(dataset_path, output_dir, skill_mode, model, concurrency):
    queries = load_dataset(dataset_path)
    system_prompt = build_system_prompt(skill_mode)

    tmp_dir = tempfile.mkdtemp(prefix="eval-codex-")
    try:
        instructions_path = Path(tmp_dir) / "instructions.md"
        instructions_path.write_text(system_prompt)

        schema_path = Path(tmp_dir) / "schema.json"
        schema_path.write_text(
            json.dumps(make_strict(ResearchIntent.model_json_schema()), indent=1)
        )

        print(f"Running {skill_mode} on {len(queries)} queries via codex exec...")
        print(f"  Model: {model}")
        print(f"  Skills: {SKILL_MODES[skill_mode] or '(none)'}")
        print(f"  Max concurrent: {concurrency}")
        print(f"  Working dir: {tmp_dir}")
        print(f"  Instructions: {len(system_prompt)} chars "
              f"(plus Codex's own non-removable preamble)")
        print()

        semaphore = asyncio.Semaphore(concurrency)
        pbar = tqdm(total=len(queries), desc=skill_mode, unit="q")
        tasks = [
            run_single_query(semaphore, q, instructions_path, schema_path,
                             model, tmp_dir, pbar)
            for q in queries
        ]
        all_results = await asyncio.gather(*tasks)
        pbar.close()

        aggregate_and_write(all_results, output_dir / model, skill_mode,
                            model, len(system_prompt))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(
        description="E1 intent extraction accuracy via codex exec"
    )
    parser.add_argument("--dataset", type=Path,
                        default=REPO_ROOT / "evaluation" / "datasets" /
                                "intent-extraction" / "queries.yaml")
    parser.add_argument("--output-dir", type=Path,
                        default=REPO_ROOT / "evaluation" / "results" / "v1_codex")
    parser.add_argument("--skill-mode", type=str, choices=list(SKILL_MODES.keys()))
    parser.add_argument("--all-modes", action="store_true")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL)
    parser.add_argument("--concurrency", type=int, default=2,
                        help="Concurrent codex processes (free tier throttles hard)")
    args = parser.parse_args()

    if args.all_modes:
        modes = list(SKILL_MODES.keys())
    elif args.skill_mode:
        modes = [args.skill_mode]
    else:
        parser.error("specify --skill-mode or --all-modes")

    for mode in modes:
        print("\n" + "#" * 70)
        print(f"# Mode: {mode}  Model: {args.model}")
        print(f"# Skills: {SKILL_MODES[mode] or '(none)'}")
        print("#" * 70 + "\n")
        asyncio.run(run_evaluation_async(
            args.dataset, args.output_dir, mode, args.model, args.concurrency
        ))


if __name__ == "__main__":
    main()
