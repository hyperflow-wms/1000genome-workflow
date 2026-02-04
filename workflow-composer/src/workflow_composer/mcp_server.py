"""
MCP server interface for chat-based interaction.
"""
from __future__ import annotations

import json
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent, Resource
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

from .core.models import ResearchIntent, GenomicRegion, OutputFormat
from .core.planner import create_advisory_plan
from .core.data_resolver import (
    resolve_region,
    KNOWN_REGIONS,
    CHROMOSOME_VARIANT_COUNT,
    estimate_variant_count,
    compute_optimal_ind_jobs
)
from .core.generator import HyperFlowGenerator, ChromosomeData, PARALLELISM_PRESETS

if HAS_MCP:
    server = Server("workflow-composer")

    @server.list_resources()
    async def list_resources():
        """List available skill resources."""
        from .interpretation.skill_loader import SKILL_DIR, SKILL_FILES

        resources = []
        for filename in SKILL_FILES:
            filepath = SKILL_DIR / filename
            if filepath.exists():
                resources.append(Resource(
                    uri=f"file://{filepath}",
                    name=filename.replace(".md", "").replace("-", " ").title(),
                    description=f"Skill document: {filename}",
                    mimeType="text/markdown"
                ))
        return resources

    @server.read_resource()
    async def read_resource(uri: str):
        """Read a skill resource by URI."""
        from .interpretation.skill_loader import SKILL_DIR, SKILL_FILES

        uri_str = str(uri)
        for filename in SKILL_FILES:
            filepath = SKILL_DIR / filename
            if uri_str.endswith(filename) or uri_str.endswith(str(filepath)):
                if filepath.exists():
                    return filepath.read_text()
                return f"Error: Skill file not found: {filename}"

        return f"Unknown resource: {uri}"

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """List available tools."""
        return [
            Tool(
                name="plan_workflow",
                description="""Generate an advisory workflow plan for population genetics research.

Returns a planning document including:
- Human-readable description
- Data preparation steps (tabix commands, file downloads)
- Estimated task counts and runtime
- Execution hints and recommendations

This is an ADVISORY plan - actual workflow.json generation requires
data files and should be done using the CLI: g1kwf generate""",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "analysis_type": {
                            "type": "string",
                            "enum": ["single_population", "population_comparison", "multi_population", "region_analysis"],
                            "description": "Type of analysis to perform"
                        },
                        "populations": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Population codes (e.g., EUR, AFR, EAS)"
                        },
                        "chromosomes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Chromosome numbers (optional)"
                        },
                        "regions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Named regions like HLA, BRCA1 (optional)"
                        },
                        "focus": {
                            "type": "string",
                            "enum": ["all_variants", "deleterious", "common", "rare"],
                            "default": "all_variants"
                        },
                        "output_format": {
                            "type": "string",
                            "enum": ["hyperflow", "wfcommons"],
                            "default": "hyperflow"
                        },
                        "compute_environment": {
                            "type": "string",
                            "enum": ["aws", "gcp", "local"],
                            "default": "aws"
                        }
                    },
                    "required": ["analysis_type", "populations"]
                }
            ),
            Tool(
                name="generate_workflow",
                description="""Generate actual HyperFlow workflow JSON from chromosome data.

Use this tool when you have concrete data (either exact row counts from scanned
files, or estimated counts). The generator handles non-exact counts gracefully.

For production use with deferred generation:
1. Use plan_workflow to get data preparation steps
2. Execute data extraction on target infrastructure
3. Scan files to get exact row counts
4. Call generate_workflow with exact counts

For testing or when exact counts are unavailable:
- Use estimated row counts (the generator handles remainders correctly)
- Overestimation is safe; underestimation may miss data

Returns the complete workflow.json ready for HyperFlow execution.""",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "chromosome_data": {
                            "type": "array",
                            "description": "Data for each chromosome to include in workflow",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "vcf_file": {
                                        "type": "string",
                                        "description": "VCF filename (e.g., 'ALL.chr6.hla.vcf')"
                                    },
                                    "row_count": {
                                        "type": "integer",
                                        "description": "Number of variant rows (exact or estimated)"
                                    },
                                    "annotation_file": {
                                        "type": "string",
                                        "description": "Annotation VCF filename for sifting"
                                    }
                                },
                                "required": ["vcf_file", "row_count", "annotation_file"]
                            }
                        },
                        "populations": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Population codes (e.g., ['EUR', 'AFR']). Default: all 7 populations",
                            "default": ["AFR", "ALL", "AMR", "EAS", "EUR", "GBR", "SAS"]
                        },
                        "parallelism": {
                            "type": "string",
                            "enum": ["small", "medium", "large"],
                            "description": "Parallelism preset: small=10, medium=50, large=250 ind_jobs",
                            "default": "medium"
                        },
                        "name": {
                            "type": "string",
                            "description": "Workflow name",
                            "default": "1000genome"
                        }
                    },
                    "required": ["chromosome_data"]
                }
            ),
            Tool(
                name="estimate_variants",
                description="""Estimate variant count for a chromosome or genomic region.

Use this for planning when exact counts are unavailable. Returns an
intentionally overestimated count (20% safety margin) to ensure the
workflow doesn't miss data.

For known regions (HLA, BRCA1, etc.), uses density-based estimation.
For full chromosomes, uses pre-computed 1000 Genomes Phase 3 counts.""",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "chromosome": {
                            "type": "string",
                            "description": "Chromosome number (e.g., '6', '22', 'X')"
                        },
                        "region": {
                            "type": "string",
                            "description": "Named region (e.g., 'HLA', 'BRCA1') - optional"
                        },
                        "start": {
                            "type": "integer",
                            "description": "Custom region start position (requires chromosome)"
                        },
                        "end": {
                            "type": "integer",
                            "description": "Custom region end position (requires chromosome)"
                        }
                    }
                }
            ),
            Tool(
                name="list_known_regions",
                description="List known genomic regions with coordinates",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
            ),
            Tool(
                name="list_populations",
                description="List available population codes",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
            )
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        """Handle tool calls."""

        if name == "plan_workflow":
            # Resolve region names to GenomicRegion objects
            regions = None
            if arguments.get("regions"):
                regions = [resolve_region(r) for r in arguments["regions"]]

            intent = ResearchIntent(
                analysis_type=arguments["analysis_type"],
                populations=arguments["populations"],
                chromosomes=arguments.get("chromosomes"),
                regions=regions,
                focus=arguments.get("focus", "all_variants")
            )

            plan = create_advisory_plan(
                intent=intent,
                output_format=OutputFormat(arguments.get("output_format", "hyperflow")),
                compute_environment=arguments.get("compute_environment", "aws")
            )

            # Format response for human review
            response = f"""## Workflow Plan

### Description
{plan.description}

### Rationale
{plan.rationale}

### Statistics
- Tasks: {plan.task_count}
- Files: {plan.signal_count}

### Data Preparation
- Source: {plan.data_preparation.source_type.upper()} ({plan.data_preparation.base_url})
- Remote extraction: {'Yes' if plan.data_preparation.use_remote_extraction else 'No'}
- Estimated transfer: {plan.data_preparation.estimated_transfer_mb:.1f} MB

**Steps:**
"""
            for i, step in enumerate(plan.data_preparation.steps, 1):
                response += f"\n{i}. {step.action.value}: {step.output_file}"

            response += f"""

### Estimates
- Runtime: ~{plan.estimated_runtime_minutes} minutes
- Storage: ~{plan.estimated_storage_gb:.1f} GB

### Execution Hints
- Parallel population analysis: {plan.execution_hints.parallel_population_analysis}
- Recommended parallelism: {plan.execution_hints.recommended_parallelism}

---

*This is an advisory plan. To generate the actual workflow.json:*
1. Extract data using the steps above
2. Create data.csv with VCF file paths and row counts
3. Run: `g1kwf generate --data-csv data.csv --populations-dir <dir> --output workflow.json`

<details>
<summary>Full Plan Details (JSON)</summary>

```json
{plan.model_dump_json(indent=2)}
```
</details>
"""

            return [TextContent(type="text", text=response)]

        elif name == "list_known_regions":
            response = "## Known Genomic Regions\n\n"
            response += "| Name | Chr | Start | End | Context |\n"
            response += "|------|-----|-------|-----|--------|\n"
            for region_name, region in KNOWN_REGIONS.items():
                response += f"| {region_name} | {region.chromosome} | {region.start:,} | {region.end:,} | {region.context} |\n"

            return [TextContent(type="text", text=response)]

        elif name == "list_populations":
            from .interpretation.skill_loader import SKILL_DIR
            pop_file = SKILL_DIR / "populations.md"
            if pop_file.exists():
                return [TextContent(type="text", text=pop_file.read_text())]
            return [TextContent(type="text", text="Population data not available")]

        elif name == "generate_workflow":
            # Generate actual workflow JSON from chromosome data
            chromosome_data = arguments.get("chromosome_data", [])
            if not chromosome_data:
                return [TextContent(type="text", text="Error: chromosome_data is required")]

            populations = arguments.get("populations", ["AFR", "ALL", "AMR", "EAS", "EUR", "GBR", "SAS"])
            parallelism = arguments.get("parallelism", "medium")
            workflow_name = arguments.get("name", "1000genome")

            ind_jobs = PARALLELISM_PRESETS.get(parallelism, 50)

            # Convert input to ChromosomeData objects
            chromosomes = []
            for chrom_info in chromosome_data:
                vcf_file = chrom_info["vcf_file"]
                row_count = chrom_info["row_count"]

                # Validate row_count
                if row_count <= 0:
                    return [TextContent(type="text",
                        text=f"Error: row_count must be positive, got {row_count} for {vcf_file}")]

                # Extract chromosome number from VCF filename
                # Expected format: *.chr{N}.*.vcf where N is 1-22, X, or Y
                if 'chr' not in vcf_file:
                    return [TextContent(type="text",
                        text=f"Error: VCF filename must contain 'chr' pattern, got: {vcf_file}")]

                c_num = vcf_file[vcf_file.find('chr') + 3:]
                c_num = c_num[0:c_num.find('.')] if '.' in c_num else c_num

                # Validate extracted chromosome
                valid_chroms = [str(i) for i in range(1, 23)] + ['X', 'Y']
                if c_num not in valid_chroms:
                    return [TextContent(type="text",
                        text=f"Error: Invalid chromosome '{c_num}' extracted from {vcf_file}. "
                             f"Expected one of: {', '.join(valid_chroms)}")]

                chromosomes.append(ChromosomeData(
                    vcf_file=vcf_file,
                    row_count=row_count,
                    annotation_file=chrom_info["annotation_file"],
                    chromosome=c_num
                ))

            # Generate workflow
            generator = HyperFlowGenerator()
            workflow = generator.generate(
                chromosomes=chromosomes,
                populations=populations,
                ind_jobs=ind_jobs,
                name=workflow_name,
                version="1.0.0"
            )

            # Format response
            task_count = len(workflow["processes"])
            signal_count = len(workflow["signals"])

            response = f"""## Generated Workflow

### Statistics
- Tasks: {task_count}
- Files: {signal_count}
- Chromosomes: {len(chromosomes)}
- Populations: {len(populations)}
- Parallelism: {parallelism} (ind_jobs={ind_jobs})

### Chromosome Data
| VCF File | Rows | Annotation |
|----------|------|------------|
"""
            for chrom in chromosomes:
                response += f"| {chrom.vcf_file} | {chrom.row_count:,} | {chrom.annotation_file} |\n"

            response += f"""
### Workflow JSON

```json
{json.dumps(workflow, indent=2)}
```

---

*To execute this workflow:*
1. Save the JSON above as `workflow.json`
2. Ensure all input files are in the workflow directory
3. Run with HyperFlow: `hflow run workflow.json`
"""

            return [TextContent(type="text", text=response)]

        elif name == "estimate_variants":
            # Estimate variant count for chromosome or region
            chromosome = arguments.get("chromosome")
            region_name = arguments.get("region")
            start = arguments.get("start")
            end = arguments.get("end")

            if region_name:
                # Named region
                try:
                    region = resolve_region(region_name)
                    estimated = estimate_variant_count(region=region)
                    exact_count = CHROMOSOME_VARIANT_COUNT.get(region.chromosome, "unknown")

                    response = f"""## Variant Estimate: {region.name}

| Property | Value |
|----------|-------|
| Chromosome | {region.chromosome} |
| Start | {region.start:,} |
| End | {region.end:,} |
| Size | {(region.end - region.start):,} bp |
| Full chromosome variants | {exact_count:,} |
| **Estimated region variants** | **{estimated:,}** |

*Note: Estimate includes 20% safety margin to ensure no data is missed.*
"""
                    return [TextContent(type="text", text=response)]
                except ValueError as e:
                    return [TextContent(type="text", text=f"Error: {e}")]

            elif chromosome and start and end:
                # Custom region
                region = GenomicRegion(
                    name=f"chr{chromosome}:{start}-{end}",
                    chromosome=chromosome,
                    start=start,
                    end=end,
                    context="custom region"
                )
                estimated = estimate_variant_count(region=region)
                exact_count = CHROMOSOME_VARIANT_COUNT.get(chromosome, "unknown")

                response = f"""## Variant Estimate: Custom Region

| Property | Value |
|----------|-------|
| Chromosome | {chromosome} |
| Start | {start:,} |
| End | {end:,} |
| Size | {(end - start):,} bp |
| Full chromosome variants | {exact_count:,} |
| **Estimated region variants** | **{estimated:,}** |

*Note: Estimate includes 20% safety margin to ensure no data is missed.*
"""
                return [TextContent(type="text", text=response)]

            elif chromosome:
                # Full chromosome
                exact_count = CHROMOSOME_VARIANT_COUNT.get(chromosome)
                if exact_count:
                    optimal_ind_jobs = compute_optimal_ind_jobs(exact_count, target=250)
                    response = f"""## Variant Count: Chromosome {chromosome}

| Property | Value |
|----------|-------|
| Chromosome | {chromosome} |
| **Variant count** | **{exact_count:,}** |
| Recommended ind_jobs | {optimal_ind_jobs} |

*This is the exact count from 1000 Genomes Phase 3 metadata.*
"""
                    return [TextContent(type="text", text=response)]
                else:
                    return [TextContent(type="text", text=f"Error: Unknown chromosome '{chromosome}'")]

            else:
                return [TextContent(type="text", text="Error: Provide either 'region', 'chromosome', or 'chromosome' with 'start' and 'end'")]

        raise ValueError(f"Unknown tool: {name}")


async def main():
    """Run the MCP server."""
    if not HAS_MCP:
        raise ImportError(
            "MCP dependencies not installed. "
            "Install with: pip install workflow-composer[mcp]"
        )

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
