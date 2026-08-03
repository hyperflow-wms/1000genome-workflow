#!/usr/bin/env python3
"""1000genome composer, Nextflow backend.

Research question -> ResearchIntent -> measured data -> resolved parallelism
-> `nextflow run`.

The phases mirror the HyperFlow harness, and everything up to RESOLVE is the
shared, engine-neutral half of the composer: intent interpretation, the
knowledge layer behind it, and `recommend_parallelism`. Only the final step is
Nextflow-specific, and it lives in the `nextflow` backend rather than here.

  INTERPRET  natural language -> ResearchIntent          (shared)
  EXTRACT    acquire the data and measure it             (nextflow -entry extract)
  RESOLVE    measurements + environment -> Parallelism   (shared)
  EXECUTE    launch with the resolved dials              (nextflow run)

RESOLVE has to precede EXECUTE rather than run inside the pipeline because
`maxForks` binds when a process is instantiated: a value produced by an
upstream channel cannot drive it. Splitting extraction into its own entry
point is what makes the measurement available in time.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ---- Shared, engine-neutral half of the composer ----
from workflow_composer.interpretation.llm_interpreter import (
    interpret_research_question,
    LLMConfig,
)
from workflow_composer.core.models import ResearchIntent
from workflow_composer.core.environment import ComputeEnvironment
from workflow_composer.core.parallelism import recommend_parallelism
from workflow_composer.backends import get_backend
from workflow_composer.backends.nextflow.params import intent_to_params, write_extract_csv

THIS_DIR = Path(__file__).parent.resolve()
MAIN_NF = THIS_DIR / "main.nf"
DEFAULT_DATA_CSV = THIS_DIR / "testdata" / "data.csv"
NXF_VER = os.environ.get("NXF_VER", "25.10.2")


def _find_nextflow() -> str:
    return os.environ.get("NEXTFLOW_BIN") or shutil.which("nextflow") or "nextflow"


def _run(cmd: list[str], cwd: Path, log_path: Path) -> int:
    env = os.environ.copy()
    env["NXF_VER"] = NXF_VER
    with open(log_path, "w") as log:
        return subprocess.run(
            cmd, stdout=log, stderr=subprocess.STDOUT, cwd=str(cwd), env=env
        ).returncode


def read_measurements(path: Path) -> dict[str, int]:
    """Read `chromosome,variants` rows written by main.nf's extract entry."""
    measurements: dict[str, int] = {}
    with open(path) as f:
        for row in csv.reader(f):
            if len(row) >= 2 and row[0].strip():
                measurements[row[0].strip()] = int(row[1])
    return measurements


def count_individuals(columns_txt: str) -> int:
    """Individuals in a columns.txt header: fields after the 9 fixed VCF ones."""
    return max(0, len(columns_txt.rstrip("\n").split("\t")) - 9)


def main() -> int:
    p = argparse.ArgumentParser(description="1000genome composer (Nextflow backend)")
    p.add_argument("prompt", help="Research question in natural language")
    # No pinned default: LLMConfig's is a floating alias, overridable through
    # WORKFLOW_COMPOSER_MODEL. Pinning a dated Gemini model here makes every
    # interpretation fail with a 404 once Google retires it.
    p.add_argument("--model", default=None,
                   help="LLM to interpret with (default: LLMConfig / WORKFLOW_COMPOSER_MODEL)")
    p.add_argument("--env", default="local", choices=["local", "aws", "gcp"],
                   help="Compute environment profile sizing the parallelism")
    p.add_argument("--intent-json", type=Path,
                   help="Skip the LLM and load a ResearchIntent from this file")
    p.add_argument("--dry-run", action="store_true",
                   help="Stop after RESOLVE; print the command without running it")
    # -resume reuses the extraction this run already performed. It also makes a
    # repeated run replay the first one's cached tasks, so anything checking
    # that unseeded stages really do vary between runs must turn it off.
    p.add_argument("--no-resume", action="store_true",
                   help="Execute every task afresh instead of reusing cached ones")
    args = p.parse_args()

    run_dir = THIS_DIR / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    # The GUI reads this to label a run with the question that produced it.
    (run_dir / "prompt.txt").write_text(args.prompt)
    nextflow = _find_nextflow()
    backend = get_backend("nextflow")
    print(f"[composer] run dir: {run_dir}")

    # ---- INTERPRET -----------------------------------------------------
    print("\n[composer] Phase 1: INTERPRET")
    if args.intent_json:
        intent = ResearchIntent.model_validate_json(args.intent_json.read_text())
        print(f"  loaded intent from {args.intent_json}")
    else:
        config = LLMConfig()
        if args.model:
            config.model = args.model
        print(f"  model: {config.model}")
        intent = interpret_research_question(args.prompt, config)
    (run_dir / "intent.json").write_text(intent.model_dump_json(indent=2))
    params = intent_to_params(intent)
    print(f"  populations: {', '.join(params.populations) or '(none)'}")
    if params.dropped_populations:
        print(f"  dropped (no population file): {', '.join(params.dropped_populations)}")
    print(f"  regions: {', '.join(r.name for r in params.regions) or '(none, using test data)'}")

    # ---- EXTRACT -------------------------------------------------------
    # Runs the pipeline's own extract entry, so acquisition stays inside the
    # DAG rather than being reimplemented here, and reports the variant count
    # RESOLVE needs.
    print("\n[composer] Phase 2: EXTRACT")
    extract_dir = run_dir / "extracted"
    extract_cmd = [nextflow, "run", str(MAIN_NF), "-entry", "extract",
                   "--outdir", str(extract_dir)]
    if params.regions:
        (run_dir / "extract.csv").write_text(write_extract_csv(params))
        extract_cmd.extend(["--extract_csv", str(run_dir / "extract.csv")])
    rc = _run(extract_cmd, run_dir, run_dir / "extract.log")
    if rc != 0:
        print(f"  FAILED (exit {rc}) -- see {run_dir / 'extract.log'}")
        return rc

    measurements_path = extract_dir / "measurements.csv"
    if not measurements_path.exists():
        print(f"  FAILED: {measurements_path} not produced")
        return 1
    measurements = read_measurements(measurements_path)
    for chrom, variants in sorted(measurements.items()):
        print(f"  chr{chrom}: {variants:,} variants")

    # ---- RESOLVE -------------------------------------------------------
    print("\n[composer] Phase 3: RESOLVE")
    # main.nf's EXTRACT publishes under "${outdir}/extracted", so the VCFs sit
    # one level below the measurements file. data.csv must live beside them:
    # generate_columns_txt resolves VCF paths relative to it.
    vcf_dir = extract_dir / "extracted"
    data_csv = vcf_dir / "data.csv" if params.regions else DEFAULT_DATA_CSV
    if params.regions:
        # main.nf publishes the extracted VCFs; name them the way
        # generate_columns_txt expects so it can read a real #CHROM header.
        rows = [
            f"ALL.chr{r.chromosome}.{r.name.lower()}.vcf,"
            f"{measurements.get(r.chromosome, 0)},"
            f"ALL.chr{r.chromosome}.{r.name.lower()}.annotation.vcf"
            for r in params.regions
        ]
        data_csv.write_text("\n".join(rows) + "\n")

    # A throwaway resolution just to render columns.txt, which is what the
    # individual count comes from; the real resolution needs that count.
    spec_preview = backend.materialize(
        intent, measurements,
        recommend_parallelism(variants=1, individuals=1, vcpus=2, host_mem_mb=4096),
        data_csv=data_csv if data_csv.exists() else None,
    )
    individuals = count_individuals(spec_preview.files["columns.txt"])

    # ind_jobs is per chromosome, so size against the largest single
    # chromosome rather than the sum, which belongs to no chromosome.
    variants = max(measurements.values()) if measurements else 0
    if variants <= 0 or individuals <= 0:
        print(f"  FAILED: variants={variants} individuals={individuals}")
        return 1

    reserve = backend.reserve()
    env = ComputeEnvironment.resolve(args.env)
    resolution = recommend_parallelism(
        variants=variants,
        individuals=individuals,
        vcpus=env.vcpus,
        host_mem_mb=env.host_mem_mb,
        chromosomes=max(1, len(measurements)),
        mem_budget_mb=env.mem_budget_mb,
        engine_reserve=reserve.cores,
        host_reserve_mb=reserve.host_mb,
    )
    print(f"  {resolution.reason}")
    print(f"  engine reserve: {reserve.cores} core(s), {reserve.host_mb} MB "
          f"({reserve.rationale})")

    # ---- EXECUTE -------------------------------------------------------
    spec = backend.materialize(
        intent, measurements, resolution,
        data_csv=data_csv if data_csv.exists() else None,
    )
    for name, content in spec.files.items():
        (run_dir / name).write_text(content)

    command = list(spec.command)
    command[0] = nextflow
    command[2] = str(MAIN_NF)
    command.extend(["--outdir", str(run_dir / "results")])
    if not args.no_resume:
        command.append("-resume")

    (run_dir / "plan.json").write_text(json.dumps({
        "intent": intent.model_dump(),
        "measurements": measurements,
        "individuals": individuals,
        "resolution": {
            "ind_jobs": resolution.ind_jobs,
            "max_parallelism": resolution.max_parallelism,
            "est_peak_mb": resolution.est_peak_mb,
            "binding": resolution.binding,
            "reason": resolution.reason,
        },
        "engine_reserve": {
            "cores": reserve.cores, "host_mb": reserve.host_mb,
            "rationale": reserve.rationale,
        },
        "command": command,
    }, indent=2))

    print(f"\n[composer] Phase 4: EXECUTE\n  {' '.join(command)}")
    if args.dry_run:
        print("\n[composer] --dry-run: stopping before execution.")
        return 0

    rc = _run(command, run_dir, run_dir / "execute.log")
    if rc == 0:
        print("\n[composer] SUCCESS -- results:")
        for f in sorted((run_dir / "results").glob("*.tar.gz")):
            print(f"  {f.name}")
    else:
        print(f"\n[composer] FAILED (exit {rc}) -- see {run_dir / 'execute.log'}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
