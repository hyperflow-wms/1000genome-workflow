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
from .core.data_resolver import resolve_region, KNOWN_REGIONS

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
- Signals: {plan.signal_count}

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
