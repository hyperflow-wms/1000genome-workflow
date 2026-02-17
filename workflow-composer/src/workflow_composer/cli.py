"""
CLI interface for standalone usage.

Includes:
- generate: Direct daxgen.py replacement (produces HyperFlow JSON)
- compose: Natural language → workflow plan
- plan: Structured intent → workflow plan
- regions: List known regions
- populations: List population codes
"""
from __future__ import annotations

import json
from pathlib import Path
import click

from .core.models import OutputFormat, ResearchIntent
from .core.generator import (
    generate_workflow, generate_columns_txt, copy_population_files,
    PARALLELISM_PRESETS, DEFAULT_PARALLELISM, BUNDLED_POPULATIONS_DIR,
)
from .core.planner import plan_workflow


@click.group()
def cli():
    """Workflow Composer - Generate genomics workflow plans."""
    pass


# =============================================================================
# CRITICAL: Direct generator access (daxgen.py replacement)
# =============================================================================

@cli.command()
@click.option("--data-csv", required=True, type=click.Path(exists=True),
              help="Path to data.csv")
@click.option("--populations-dir", default=None, type=click.Path(exists=True),
              help="Path to populations directory (default: bundled)")
@click.option("--parallelism", "-p", default=None,
              type=click.Choice(list(PARALLELISM_PRESETS.keys())),
              help=f"Parallelism preset: small={PARALLELISM_PRESETS['small']}, "
                   f"medium={PARALLELISM_PRESETS['medium']}, large={PARALLELISM_PRESETS['large']}")
@click.option("--ind-jobs", default=None, type=int,
              help="Explicit individuals jobs per chromosome (overrides --parallelism)")
@click.option("--name", default="1000genome", help="Workflow name")
@click.option("--version", default="1.0.0", help="Workflow version")
@click.option("--populations", default=None,
              help="Comma-separated population filter (e.g., GBR or EUR,AFR). Default: all in dir.")
@click.option("--max-samples-per-pop", default=None, type=int,
              help="Cap individuals per population in columns.txt")
@click.option("--output", "-o", default=None, help="Output file (default: stdout)")
def generate(data_csv: str, populations_dir: str, parallelism: str, ind_jobs: int,
             name: str, version: str, populations: str, max_samples_per_pop: int, output: str):
    """
    Generate HyperFlow workflow directly (daxgen.py replacement).

    This is the native workflow generator that replaces:
        python daxgen.py + hflow-convert-dax

    Example:
        workflow-composer generate \\
            --data-csv workflow-generator/data.csv \\
            --populations-dir workflow-generator/data/populations \\
            --parallelism medium
    """
    # Resolve populations directory: explicit path > bundled
    if populations_dir is None:
        populations_dir = str(BUNDLED_POPULATIONS_DIR)

    # Resolve ind_jobs: explicit value > preset > default
    if ind_jobs is None:
        if parallelism:
            ind_jobs = PARALLELISM_PRESETS[parallelism]
        else:
            ind_jobs = PARALLELISM_PRESETS[DEFAULT_PARALLELISM]

    # Parse population filter
    pop_filter = None
    if populations:
        pop_filter = [p.strip() for p in populations.split(",")]

    try:
        workflow = generate_workflow(
            data_csv=Path(data_csv),
            populations_dir=Path(populations_dir),
            ind_jobs=ind_jobs,
            name=name,
            version=version,
            population_filter=pop_filter
        )

        result = json.dumps(workflow, indent=2)

        if output:
            output_path = Path(output)
            with open(output_path, "w") as f:
                f.write(result)
            click.echo(f"Workflow written to {output}", err=True)
            click.echo(f"  Tasks: {len(workflow['processes'])}", err=True)
            click.echo(f"  Files: {len(workflow['signals'])}", err=True)

            # Generate columns.txt alongside workflow.json
            columns_txt = generate_columns_txt(
                data_csv=Path(data_csv),
                populations_dir=Path(populations_dir),
                population_filter=pop_filter,
                max_samples_per_pop=max_samples_per_pop,
            )
            columns_path = output_path.parent / "columns.txt"
            with open(columns_path, "w") as f:
                f.write(columns_txt)
            ind_count = len(columns_txt.strip().split("\t")) - 9
            click.echo(f"  columns.txt: {ind_count} individuals", err=True)

            # Copy population files alongside workflow.json
            copied_pops = copy_population_files(
                output_dir=output_path.parent,
                populations_dir=Path(populations_dir),
                population_filter=pop_filter,
            )
            click.echo(f"  populations: {', '.join(copied_pops)}", err=True)
        else:
            click.echo(result)

    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


# =============================================================================
# Natural language interface
# =============================================================================

@cli.command()
@click.argument("question")
@click.option("--model", default=None, help="LLM model (e.g., anthropic/claude-sonnet-4-20250514)")
@click.option("--format", "output_format", default="hyperflow",
              type=click.Choice(["hyperflow", "wfcommons"]))
@click.option("--env", "compute_env", default="local",
              type=click.Choice(["local", "aws", "gcp"]))
@click.option("--parallelism", "-p", default=None,
              type=click.Choice(list(PARALLELISM_PRESETS.keys())),
              help="Parallelism preset (auto-selected if not specified)")
@click.option("--output", "-o", default=None, help="Output file (default: stdout)")
@click.option("--json-only", is_flag=True, help="Output only JSON, no description")
def compose(question: str, model: str, output_format: str, compute_env: str,
            parallelism: str, output: str, json_only: bool):
    """
    Generate a workflow plan from a natural language question.

    Example:
        workflow-composer compose "Compare EUR vs AFR in HLA region"
    """
    try:
        from .interpretation.llm_interpreter import interpret_research_question, LLMConfig
    except ImportError:
        click.echo("Error: LLM dependencies not installed. Install with: pip install workflow-composer[llm]", err=True)
        raise SystemExit(1)

    # Configure LLM
    config = LLMConfig()
    if model:
        config.model = model

    # Interpret question
    click.echo("Interpreting research question...", err=True)
    intent = interpret_research_question(question, config)

    click.echo(f"Detected: {intent.analysis_type} with {intent.populations}", err=True)

    # Generate plan
    click.echo("Generating workflow plan...", err=True)
    plan = plan_workflow(
        intent=intent,
        output_format=OutputFormat(output_format),
        compute_environment=compute_env,
        parallelism=parallelism
    )

    # Output
    if json_only:
        result = plan.model_dump_json(indent=2)
    else:
        result = f"""
# Workflow Plan

## Description
{plan.description}

## Rationale
{plan.rationale}

## Statistics
- Tasks: {plan.task_count}
- Files: {plan.file_count}

## Data Preparation
Source: {plan.data_preparation.source_type}
Transfer: {plan.data_preparation.estimated_transfer_mb:.1f} MB

## Estimates
- Runtime: ~{plan.estimated_runtime_minutes} minutes
- Storage: ~{plan.estimated_storage_gb:.1f} GB

## Plan JSON
```json
{plan.model_dump_json(indent=2)}
```
"""

    if output:
        with open(output, "w") as f:
            f.write(result)
        click.echo(f"Plan written to {output}", err=True)
    else:
        click.echo(result)


@cli.command()
@click.argument("intent_json")
@click.option("--format", "output_format", default="hyperflow",
              type=click.Choice(["hyperflow", "wfcommons"]))
@click.option("--env", "compute_env", default="local",
              type=click.Choice(["local", "aws", "gcp"]))
@click.option("--parallelism", "-p", default=None,
              type=click.Choice(list(PARALLELISM_PRESETS.keys())),
              help="Parallelism preset (auto-selected if not specified)")
def plan(intent_json: str, output_format: str, compute_env: str, parallelism: str):
    """
    Generate workflow from a ResearchIntent JSON.

    Example:
        workflow-composer plan '{"analysis_type": "population_comparison", "populations": ["EUR", "AFR"]}'
    """
    intent = ResearchIntent.model_validate_json(intent_json)

    result = plan_workflow(
        intent=intent,
        output_format=OutputFormat(output_format),
        compute_environment=compute_env,
        parallelism=parallelism
    )

    click.echo(result.model_dump_json(indent=2))


@cli.command()
def regions():
    """List known genomic regions."""
    from .core.data_resolver import KNOWN_REGIONS

    click.echo("Known Genomic Regions:\n")
    for name, region in KNOWN_REGIONS.items():
        click.echo(f"  {name:10} chr{region.chromosome}:{region.start:,}-{region.end:,} ({region.context})")


@cli.command()
def populations():
    """List population codes."""
    from .interpretation.skill_loader import SKILL_DIR

    pop_file = SKILL_DIR / "populations.md"
    if pop_file.exists():
        click.echo(pop_file.read_text())
    else:
        click.echo("Population data not available. See skills/populations.md")


if __name__ == "__main__":
    cli()
