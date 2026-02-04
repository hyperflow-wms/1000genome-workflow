#!/usr/bin/env python3
"""
E2E Test Framework Library

Provides Python functions for test phases, called from run-research-tests.sh.
This keeps complex logic in Python while shell handles orchestration.

See RFC-002 for architecture details.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# Add workflow-composer to path
REPO_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "workflow-composer" / "src"))

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


class StopPoint(Enum):
    """Where to stop test execution."""
    AFTER_INTERPRET = "interpret"
    AFTER_PLAN = "plan"
    AFTER_ESTIMATE = "estimate"
    AFTER_EXTRACT = "extract"
    AFTER_GENERATE = "generate"
    NEVER = "never"


@dataclass
class TestCase:
    """Parsed test case from YAML."""
    id: str
    name: str
    description: str | None = None
    prompt: str | None = None
    expected_intent: dict | None = None
    mock_intent: dict | None = None
    skip_interpret: bool = False
    skip_extract: bool = False
    data_csv: str | None = None
    expected_outputs: list[str] = field(default_factory=list)
    parallelism: str = "small"

    @classmethod
    def from_dict(cls, data: dict, defaults: dict) -> "TestCase":
        """Create TestCase from YAML dict with defaults applied."""
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description"),
            prompt=data.get("prompt"),
            expected_intent=data.get("expected_intent"),
            mock_intent=data.get("mock_intent"),
            skip_interpret=data.get("skip_interpret", False),
            skip_extract=data.get("skip_extract", False),
            data_csv=data.get("data_csv"),
            expected_outputs=data.get("expected_outputs", []),
            parallelism=data.get("parallelism", defaults.get("parallelism", "small"))
        )


def expand_env_vars(value: Any, env: dict[str, str]) -> Any:
    """Recursively expand environment variables in strings."""
    if isinstance(value, str):
        for var, val in env.items():
            value = value.replace(f"${{{var}}}", val)
        return value
    elif isinstance(value, dict):
        return {k: expand_env_vars(v, env) for k, v in value.items()}
    elif isinstance(value, list):
        return [expand_env_vars(v, env) for v in value]
    return value


def load_test_cases(yaml_path: Path) -> dict[str, TestCase]:
    """Load test cases from YAML file."""
    if not HAS_YAML:
        raise ImportError("PyYAML not installed. Run: pip install pyyaml")

    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    defaults = data.get("defaults", {})

    env = {
        "REPO_ROOT": str(REPO_ROOT),
        "SCRIPT_DIR": str(yaml_path.parent),
    }

    cases = {}
    for tc_data in data.get("test_cases", []):
        tc_data = expand_env_vars(tc_data, env)
        tc = TestCase.from_dict(tc_data, defaults)
        cases[tc.id] = tc

    return cases


def load_thresholds(yaml_path: Path) -> tuple[float, float]:
    """Load volume thresholds from YAML."""
    if not HAS_YAML:
        raise ImportError("PyYAML not installed. Run: pip install pyyaml")

    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    thresholds = data.get("volume_thresholds", {})
    return (
        thresholds.get("stop_before_extract_mb", 500),
        thresholds.get("stop_before_execute_mb", 50)
    )


def compute_adaptive_parallelism(
    estimated_variants: int,
    vcpus: int,
    target_variants_per_task: int = 25_000,
    min_tasks_per_vcpu: float = 1.5,
    max_tasks_per_vcpu: float = 5.0,
) -> int:
    """
    Compute ind_jobs based on cluster size and data volume.

    Goals:
    1. Each task processes a reasonable chunk (~25k variants)
    2. Enough tasks to keep all vCPUs busy (accounting for I/O wait)
    3. Not so many tasks that scheduling overhead dominates

    Args:
        estimated_variants: Total estimated variant count
        vcpus: Number of available vCPUs
        target_variants_per_task: Target variants per task (~1-2 min processing)
        min_tasks_per_vcpu: Minimum task multiplier (keeps CPUs busy during I/O)
        max_tasks_per_vcpu: Maximum task multiplier (avoids scheduling overhead)

    Returns:
        Recommended ind_jobs value
    """
    if vcpus <= 0:
        raise ValueError(f"vcpus must be positive, got {vcpus}")
    if estimated_variants <= 0:
        return 1

    # Data-driven estimate
    data_driven = max(1, estimated_variants // target_variants_per_task)

    # Resource-driven bounds
    min_jobs = max(1, int(vcpus * min_tasks_per_vcpu))
    max_jobs = max(min_jobs, int(vcpus * max_tasks_per_vcpu))

    # Clamp to bounds
    ind_jobs = max(min_jobs, min(data_driven, max_jobs))

    return ind_jobs


def interpret_prompt(
    prompt: str | None,
    use_mock: bool,
    mock_intent: dict | None,
    model: str | None = None
) -> dict:
    """
    Phase 1: Interpret natural language prompt.

    Args:
        prompt: Natural language research question
        use_mock: If True, use mock_intent instead of calling LLM
        mock_intent: Pre-defined intent for mocking
        model: LLM model to use (if not mocking)

    Returns:
        ResearchIntent as dict
    """
    from workflow_composer.core.models import ResearchIntent, GenomicRegion
    from workflow_composer.core.data_resolver import KNOWN_REGIONS

    if use_mock or prompt is None:
        if not mock_intent:
            raise ValueError("Mock mode requested but no mock_intent provided")

        regions = None
        if mock_intent.get("regions"):
            regions = []
            for r in mock_intent["regions"]:
                if isinstance(r, dict):
                    regions.append(GenomicRegion(**r))
                elif isinstance(r, str):
                    regions.append(KNOWN_REGIONS[r.upper()])
                else:
                    regions.append(r)

        intent = ResearchIntent(
            analysis_type=mock_intent["analysis_type"],
            populations=mock_intent["populations"],
            chromosomes=mock_intent.get("chromosomes"),
            regions=regions,
            focus=mock_intent.get("focus", "all_variants")
        )
        return intent.model_dump()

    try:
        from workflow_composer.interpretation.llm_interpreter import (
            interpret_research_question,
            LLMConfig
        )
    except ImportError as e:
        raise ImportError(
            f"LLM dependencies not installed: {e}\n"
            "Install with: pip install workflow-composer[llm]\n"
            "Or use --mock-llm to bypass LLM interpretation."
        )

    config = LLMConfig()
    if model:
        config.model = model

    intent = interpret_research_question(prompt, config)
    return intent.model_dump()


def create_plan(intent_dict: dict, compute_env: str = "local") -> dict:
    """
    Phase 2: Create advisory workflow plan.

    Returns plan as dict for JSON serialization.
    """
    from workflow_composer.core.models import ResearchIntent, GenomicRegion, OutputFormat
    from workflow_composer.core.planner import create_advisory_plan

    regions = None
    if intent_dict.get("regions"):
        regions = [GenomicRegion(**r) for r in intent_dict["regions"]]

    intent = ResearchIntent(
        analysis_type=intent_dict["analysis_type"],
        populations=intent_dict["populations"],
        chromosomes=intent_dict.get("chromosomes"),
        regions=regions,
        focus=intent_dict.get("focus", "all_variants")
    )

    plan = create_advisory_plan(
        intent=intent,
        output_format=OutputFormat.HYPERFLOW,
        compute_environment=compute_env
    )
    return plan.model_dump()


def estimate_total_variants(intent_dict: dict) -> int:
    """Estimate total variant count for an intent."""
    from workflow_composer.core.models import GenomicRegion
    from workflow_composer.core.data_resolver import estimate_variant_count, CHROMOSOME_VARIANT_COUNT

    total = 0

    if intent_dict.get("regions"):
        for r in intent_dict["regions"]:
            region = GenomicRegion(**r)
            total += estimate_variant_count(region=region)
    elif intent_dict.get("chromosomes"):
        for chrom in intent_dict["chromosomes"]:
            total += estimate_variant_count(chromosome=chrom)
    else:
        # All autosomes
        for chrom in [str(i) for i in range(1, 23)]:
            total += CHROMOSOME_VARIANT_COUNT.get(chrom, 3_000_000)

    return total


def generate_estimated_workflow(
    intent_dict: dict,
    parallelism: str | None = None,
    ind_jobs: int | None = None,
    vcpus: int | None = None
) -> dict:
    """
    Phase 3: Generate workflow with estimated variant counts.

    Parallelism precedence:
    1. ind_jobs (explicit)
    2. vcpus (adaptive calculation)
    3. parallelism preset
    4. default "small"
    """
    from workflow_composer.core.models import ResearchIntent, GenomicRegion
    from workflow_composer.core.generator import HyperFlowGenerator, ChromosomeData, PARALLELISM_PRESETS
    from workflow_composer.core.data_resolver import estimate_variant_count

    regions = None
    if intent_dict.get("regions"):
        regions = [GenomicRegion(**r) for r in intent_dict["regions"]]

    intent = ResearchIntent(
        analysis_type=intent_dict["analysis_type"],
        populations=intent_dict["populations"],
        chromosomes=intent_dict.get("chromosomes"),
        regions=regions,
        focus=intent_dict.get("focus", "all_variants")
    )

    generator = HyperFlowGenerator()
    chromosomes = []

    if intent.regions:
        for region in intent.regions:
            estimated = estimate_variant_count(region=region)
            chromosomes.append(ChromosomeData(
                vcf_file=f"ALL.chr{region.chromosome}.{region.name.lower()}.vcf",
                row_count=estimated,
                annotation_file=f"ALL.chr{region.chromosome}.{region.name.lower()}.annotation.vcf",
                chromosome=region.chromosome
            ))
    elif intent.chromosomes:
        for chrom in intent.chromosomes:
            estimated = estimate_variant_count(chromosome=chrom)
            chromosomes.append(ChromosomeData(
                vcf_file=f"ALL.chr{chrom}.vcf",
                row_count=estimated,
                annotation_file=f"ALL.chr{chrom}.annotation.vcf",
                chromosome=chrom
            ))
    else:
        for chrom in [str(i) for i in range(1, 23)]:
            estimated = estimate_variant_count(chromosome=chrom)
            chromosomes.append(ChromosomeData(
                vcf_file=f"ALL.chr{chrom}.vcf",
                row_count=estimated,
                annotation_file=f"ALL.chr{chrom}.annotation.vcf",
                chromosome=chrom
            ))

    # Determine ind_jobs with precedence
    if ind_jobs is not None:
        final_ind_jobs = ind_jobs
    elif vcpus is not None:
        total_variants = sum(c.row_count for c in chromosomes)
        final_ind_jobs = compute_adaptive_parallelism(total_variants, vcpus)
    elif parallelism is not None:
        final_ind_jobs = PARALLELISM_PRESETS.get(parallelism, 10)
    else:
        final_ind_jobs = PARALLELISM_PRESETS.get("small", 10)

    return generator.generate(
        chromosomes=chromosomes,
        populations=intent.populations,
        ind_jobs=final_ind_jobs,
        name="1000genome-estimated"
    )


def determine_stop_point(
    estimated_transfer_mb: float,
    threshold_extract: float,
    threshold_execute: float,
    explicit_stop: str | None,
    force_yes: bool
) -> str:
    """
    Determine where to stop based on volume and flags.

    Returns: StopPoint value as string
    """
    if explicit_stop == "extract":
        return StopPoint.AFTER_ESTIMATE.value
    if explicit_stop == "execute":
        return StopPoint.AFTER_GENERATE.value
    if force_yes:
        return StopPoint.NEVER.value

    if estimated_transfer_mb > threshold_extract:
        return StopPoint.AFTER_ESTIMATE.value
    if estimated_transfer_mb > threshold_execute:
        return StopPoint.AFTER_GENERATE.value

    return StopPoint.NEVER.value


def validate_intent(actual: dict, expected: dict | None) -> tuple[bool, list[str]]:
    """
    Validate actual intent against expected intent.

    Returns (is_valid, list_of_differences)
    """
    if expected is None:
        return True, []

    differences = []

    if actual.get("analysis_type") != expected.get("analysis_type"):
        differences.append(
            f"analysis_type: expected {expected.get('analysis_type')}, "
            f"got {actual.get('analysis_type')}"
        )

    actual_pops = set(actual.get("populations", []))
    expected_pops = set(expected.get("populations", []))
    if actual_pops != expected_pops:
        differences.append(
            f"populations: expected {expected_pops}, got {actual_pops}"
        )

    if expected.get("chromosomes"):
        actual_chroms = set(actual.get("chromosomes") or [])
        expected_chroms = set(expected.get("chromosomes"))
        if actual_chroms != expected_chroms:
            differences.append(
                f"chromosomes: expected {expected_chroms}, got {actual_chroms}"
            )

    if expected.get("focus") and actual.get("focus") != expected.get("focus"):
        differences.append(
            f"focus: expected {expected.get('focus')}, got {actual.get('focus')}"
        )

    return len(differences) == 0, differences


def verify_outputs(workflow_dir: Path, expected_outputs: list[str]) -> tuple[int, list[str]]:
    """
    Verify expected output files exist and are non-empty.

    Returns (missing_count, list_of_missing_files)
    """
    missing = []
    for output in expected_outputs:
        output_path = workflow_dir / output
        if not output_path.exists():
            missing.append(f"{output} (not found)")
        elif output_path.stat().st_size == 0:
            missing.append(f"{output} (empty)")
    return len(missing), missing


def get_tabix_commands(intent_dict: dict, output_dir: str) -> list[dict]:
    """
    Generate tabix extraction commands for downloading data.

    Returns list of command specs with: chromosome, region, vcf_url, annotation_url, output_files
    """
    from workflow_composer.core.models import GenomicRegion
    from workflow_composer.core.data_resolver import KNOWN_REGIONS

    # 1000 Genomes URLs
    VCF_BASE = "https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/release/20130502"
    ANNOTATION_BASE = f"{VCF_BASE}/supporting/functional_annotation/filtered"

    commands = []

    if intent_dict.get("regions"):
        for r in intent_dict["regions"]:
            if isinstance(r, dict):
                region = GenomicRegion(**r)
            else:
                region = r

            chrom = region.chromosome
            region_str = f"{chrom}:{region.start}-{region.end}"
            region_name = region.name.lower()

            # VCF filename varies by chromosome
            if chrom == "X":
                vcf_file = "ALL.chrX.phase3_shapeit2_mvncall_integrated_v1b.20130502.genotypes.vcf.gz"
            elif chrom == "Y":
                vcf_file = "ALL.chrY.phase3_integrated_v2a.20130502.genotypes.vcf.gz"
            else:
                vcf_file = f"ALL.chr{chrom}.phase3_shapeit2_mvncall_integrated_v5b.20130502.genotypes.vcf.gz"

            annotation_file = f"ALL.chr{chrom}.phase3_shapeit2_mvncall_integrated_v5.20130502.sites.annotation.vcf.gz"

            commands.append({
                "chromosome": chrom,
                "region": region_str,
                "region_name": region_name,
                "vcf_url": f"{VCF_BASE}/{vcf_file}",
                "annotation_url": f"{ANNOTATION_BASE}/{annotation_file}",
                "output_vcf": f"ALL.chr{chrom}.{region_name}.vcf",
                "output_annotation": f"ALL.chr{chrom}.{region_name}.annotation.vcf"
            })

    elif intent_dict.get("chromosomes"):
        for chrom in intent_dict["chromosomes"]:
            if chrom == "X":
                vcf_file = "ALL.chrX.phase3_shapeit2_mvncall_integrated_v1b.20130502.genotypes.vcf.gz"
            elif chrom == "Y":
                vcf_file = "ALL.chrY.phase3_integrated_v2a.20130502.genotypes.vcf.gz"
            else:
                vcf_file = f"ALL.chr{chrom}.phase3_shapeit2_mvncall_integrated_v5b.20130502.genotypes.vcf.gz"

            annotation_file = f"ALL.chr{chrom}.phase3_shapeit2_mvncall_integrated_v5.20130502.sites.annotation.vcf.gz"

            commands.append({
                "chromosome": chrom,
                "region": None,  # Full chromosome
                "region_name": None,
                "vcf_url": f"{VCF_BASE}/{vcf_file}",
                "annotation_url": f"{ANNOTATION_BASE}/{annotation_file}",
                "output_vcf": f"ALL.chr{chrom}.vcf",
                "output_annotation": f"ALL.chr{chrom}.annotation.vcf"
            })

    return commands


# ============================================================================
# CLI Interface
# ============================================================================

def main():
    """CLI entry point for shell script integration."""
    import argparse

    parser = argparse.ArgumentParser(description="E2E Test Framework Library")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # List command
    list_parser = subparsers.add_parser("list", help="List test cases")
    list_parser.add_argument("--yaml", required=True)
    list_parser.add_argument("--verbose", "-v", action="store_true")

    # Interpret command
    interp_parser = subparsers.add_parser("interpret", help="Interpret NL prompt")
    interp_parser.add_argument("--yaml", required=True)
    interp_parser.add_argument("--test-id", required=True)
    interp_parser.add_argument("--mock", action="store_true")
    interp_parser.add_argument("--model", default=None)

    # Plan command
    plan_parser = subparsers.add_parser("plan", help="Create advisory plan")
    plan_parser.add_argument("--intent-json", required=True)
    plan_parser.add_argument("--compute-env", default="local")

    # Estimate command
    est_parser = subparsers.add_parser("estimate", help="Generate estimated workflow")
    est_parser.add_argument("--intent-json", required=True)
    est_parser.add_argument("--parallelism", default=None)
    est_parser.add_argument("--ind-jobs", type=int, default=None)
    est_parser.add_argument("--vcpus", type=int, default=None)

    # Adaptive parallelism command
    adapt_parser = subparsers.add_parser("adaptive-parallelism", help="Compute adaptive parallelism")
    adapt_parser.add_argument("--intent-json", required=True)
    adapt_parser.add_argument("--vcpus", type=int, required=True)

    # Stop-point command
    stop_parser = subparsers.add_parser("stop-point", help="Determine stop point")
    stop_parser.add_argument("--transfer-mb", type=float, required=True)
    stop_parser.add_argument("--yaml", required=True)
    stop_parser.add_argument("--explicit-stop", default=None)
    stop_parser.add_argument("--force-yes", action="store_true")

    # Validate command
    val_parser = subparsers.add_parser("validate", help="Validate intent")
    val_parser.add_argument("--actual-json", required=True)
    val_parser.add_argument("--yaml", required=True)
    val_parser.add_argument("--test-id", required=True)

    # Verify outputs command
    verify_parser = subparsers.add_parser("verify-outputs", help="Verify expected outputs")
    verify_parser.add_argument("--workflow-dir", required=True)
    verify_parser.add_argument("--yaml", required=True)
    verify_parser.add_argument("--test-id", required=True)

    # Tabix commands
    tabix_parser = subparsers.add_parser("tabix-commands", help="Generate tabix extraction commands")
    tabix_parser.add_argument("--intent-json", required=True)
    tabix_parser.add_argument("--output-dir", required=True)

    # Get test case info
    info_parser = subparsers.add_parser("test-info", help="Get test case info")
    info_parser.add_argument("--yaml", required=True)
    info_parser.add_argument("--test-id", required=True)
    info_parser.add_argument("--field", required=True)

    args = parser.parse_args()

    try:
        if args.command == "list":
            cases = load_test_cases(Path(args.yaml))
            for tc in cases.values():
                if args.verbose:
                    skip_info = []
                    if tc.skip_interpret:
                        skip_info.append("skip-interpret")
                    if tc.skip_extract:
                        skip_info.append("skip-extract")
                    skip_str = f" [{', '.join(skip_info)}]" if skip_info else ""
                    print(f"{tc.id}\t{tc.name}{skip_str}")
                else:
                    print(f"{tc.id}\t{tc.name}")

        elif args.command == "interpret":
            cases = load_test_cases(Path(args.yaml))
            tc = cases.get(args.test_id)
            if not tc:
                print(f"Error: Unknown test ID: {args.test_id}", file=sys.stderr)
                sys.exit(1)

            intent = interpret_prompt(
                tc.prompt,
                args.mock or tc.skip_interpret,
                tc.mock_intent,
                args.model
            )
            print(json.dumps(intent, indent=2))

        elif args.command == "plan":
            intent = json.loads(args.intent_json)
            plan = create_plan(intent, args.compute_env)
            print(json.dumps(plan, indent=2))

        elif args.command == "estimate":
            intent = json.loads(args.intent_json)
            workflow = generate_estimated_workflow(
                intent,
                parallelism=args.parallelism,
                ind_jobs=args.ind_jobs,
                vcpus=args.vcpus
            )
            print(json.dumps(workflow, indent=2))

        elif args.command == "adaptive-parallelism":
            intent = json.loads(args.intent_json)
            total_variants = estimate_total_variants(intent)
            ind_jobs = compute_adaptive_parallelism(total_variants, args.vcpus)
            print(json.dumps({
                "vcpus": args.vcpus,
                "estimated_variants": total_variants,
                "ind_jobs": ind_jobs
            }))

        elif args.command == "stop-point":
            threshold_dl, threshold_exec = load_thresholds(Path(args.yaml))
            stop = determine_stop_point(
                args.transfer_mb,
                threshold_dl,
                threshold_exec,
                args.explicit_stop,
                args.force_yes
            )
            print(stop)

        elif args.command == "validate":
            cases = load_test_cases(Path(args.yaml))
            tc = cases.get(args.test_id)
            if not tc:
                print(f"Error: Unknown test ID: {args.test_id}", file=sys.stderr)
                sys.exit(1)

            actual = json.loads(args.actual_json)
            is_valid, diffs = validate_intent(actual, tc.expected_intent)

            result = {"valid": is_valid, "differences": diffs}
            print(json.dumps(result))

        elif args.command == "verify-outputs":
            cases = load_test_cases(Path(args.yaml))
            tc = cases.get(args.test_id)
            if not tc:
                print(f"Error: Unknown test ID: {args.test_id}", file=sys.stderr)
                sys.exit(1)

            missing_count, missing_files = verify_outputs(
                Path(args.workflow_dir),
                tc.expected_outputs
            )
            print(json.dumps({
                "missing_count": missing_count,
                "missing_files": missing_files,
                "expected_count": len(tc.expected_outputs)
            }))

        elif args.command == "tabix-commands":
            intent = json.loads(args.intent_json)
            commands = get_tabix_commands(intent, args.output_dir)
            print(json.dumps(commands))

        elif args.command == "test-info":
            cases = load_test_cases(Path(args.yaml))
            tc = cases.get(args.test_id)
            if not tc:
                print(f"Error: Unknown test ID: {args.test_id}", file=sys.stderr)
                sys.exit(1)

            field_value = getattr(tc, args.field, None)
            if field_value is None:
                print("null")
            elif isinstance(field_value, bool):
                print("true" if field_value else "false")
            elif isinstance(field_value, (list, dict)):
                print(json.dumps(field_value))
            else:
                print(field_value)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
