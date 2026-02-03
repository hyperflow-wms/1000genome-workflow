#!/usr/bin/env python3
"""
MCP Server for 1000genome workflow generation.

Provides tools and resources (skills) for generating and analyzing HyperFlow
workflows for the 1000 Genomes Project mutation overlap analysis.

Resources (Skills):
- 1000genome://skill - Parameter extraction rules and constraints
- 1000genome://populations - Population codes and sample counts
- 1000genome://research - Scientific context and runtime estimates
"""

import json
import subprocess
import os
from pathlib import Path
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, Resource

# Initialize MCP server
server = Server("1000genome-workflow")

# Path to workflow generator scripts
GENERATOR_PATH = "/1000genome-workflow"

# Path to skill files
SKILLS_PATH = Path(__file__).parent / "skills"

# Valid divisors of 250,000 for individuals_per_job parameter
VALID_IND_JOBS = [
    1, 2, 4, 5, 8, 10, 16, 20, 25, 40, 50, 100, 125, 200, 250,
    500, 625, 1000, 1250, 2000, 2500, 5000, 6250, 10000, 12500,
    25000, 50000, 62500, 125000, 250000
]

# Skill resources definition
SKILL_RESOURCES = [
    {
        "uri": "file:///1000genome-workflow/skills/SKILL.md",
        "name": "Workflow Generation Skill",
        "description": "Parameter extraction rules, constraints, and examples. READ THIS FIRST before using generate_workflow.",
        "path": "SKILL.md"
    },
    {
        "uri": "file:///1000genome-workflow/skills/populations.md",
        "name": "Population Reference",
        "description": "Details on 7 population groups (AFR, AMR, EAS, EUR, SAS, GBR, ALL) with sample counts and natural language mappings.",
        "path": "references/populations.md"
    },
    {
        "uri": "file:///1000genome-workflow/skills/research.md",
        "name": "Research Contexts",
        "description": "Scientific background, task descriptions, memory/runtime estimates, and common analysis patterns.",
        "path": "references/research-contexts.md"
    }
]


# ============ RESOURCES (SKILLS) ============

@server.list_resources()
async def list_resources():
    """List available skill resources."""
    return [
        Resource(
            uri=r["uri"],
            name=r["name"],
            description=r["description"],
            mimeType="text/markdown"
        )
        for r in SKILL_RESOURCES
    ]


@server.read_resource()
async def read_resource(uri: str):
    """Read a skill resource by URI."""
    uri_str = str(uri)  # Convert AnyUrl to string for comparison
    for r in SKILL_RESOURCES:
        if r["uri"] == uri_str:
            skill_path = SKILLS_PATH / r["path"]
            if skill_path.exists():
                return skill_path.read_text()
            return f"Error: Skill file not found: {r['path']}"

    available = ", ".join(r["uri"] for r in SKILL_RESOURCES)
    return f"Unknown resource: {uri}\n\nAvailable resources: {available}"


# ============ TOOLS ============

@server.list_tools()
async def list_tools():
    """List available MCP tools."""
    return [
        Tool(
            name="generate_workflow",
            description=(
                "Generate a 1000genome HyperFlow workflow DAG.\n\n"
                "⚠️ IMPORTANT: Read skill first with read_resource('1000genome://skill')\n\n"
                "Key constraint: individuals_per_job must divide 250,000 evenly.\n"
                "Valid values: 1, 2, 4, 5, 10, 20, 25, 50, 100, 125, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000, 250000..."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Workflow name",
                        "default": "1000genome"
                    },
                    "version": {
                        "type": "string",
                        "description": "Workflow version",
                        "default": "1.0.0"
                    },
                    "individuals_per_job": {
                        "type": "integer",
                        "description": "Rows per parallel task. MUST divide 250,000 evenly. Default: 250. Use 50000 for quick tests.",
                        "default": 250
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="get_workflow_stats",
            description="Get statistics about a workflow (task count, file count, etc.)",
            inputSchema={
                "type": "object",
                "properties": {
                    "workflow_json": {
                        "type": "string",
                        "description": "Workflow JSON content or path to workflow file"
                    }
                },
                "required": ["workflow_json"]
            }
        ),
        Tool(
            name="list_chromosomes",
            description="List available chromosome data files from data.csv",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="validate_workflow",
            description="Validate a HyperFlow workflow definition",
            inputSchema={
                "type": "object",
                "properties": {
                    "workflow_json": {
                        "type": "string",
                        "description": "Workflow JSON content to validate"
                    }
                },
                "required": ["workflow_json"]
            }
        ),
        Tool(
            name="estimate_tasks",
            description="Estimate task count for given parameters without generating workflow",
            inputSchema={
                "type": "object",
                "properties": {
                    "individuals_per_job": {
                        "type": "integer",
                        "description": "Rows per parallel task (must divide 250,000)",
                        "default": 250
                    },
                    "chromosomes": {
                        "type": "integer",
                        "description": "Number of chromosomes (default: 10)",
                        "default": 10
                    }
                },
                "required": []
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    """Handle tool calls."""
    if name == "generate_workflow":
        return await generate_workflow(arguments)
    elif name == "get_workflow_stats":
        return await get_workflow_stats(arguments)
    elif name == "list_chromosomes":
        return await list_chromosomes(arguments)
    elif name == "validate_workflow":
        return await validate_workflow(arguments)
    elif name == "estimate_tasks":
        return await estimate_tasks(arguments)
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def generate_workflow(arguments: dict):
    """Generate a 1000genome workflow."""
    name = arguments.get("name", "1000genome")
    version = arguments.get("version", "1.0.0")
    ind_jobs = arguments.get("individuals_per_job", 250)

    # Validate individuals_per_job constraint
    if 250000 % ind_jobs != 0:
        # Find nearest valid values
        nearest = sorted(VALID_IND_JOBS, key=lambda x: abs(x - ind_jobs))[:5]
        return [TextContent(
            type="text",
            text=(
                f"❌ Error: individuals_per_job={ind_jobs} is invalid.\n\n"
                f"The value must divide 250,000 evenly (each VCF has 250,000 rows).\n\n"
                f"Nearest valid values: {nearest}\n\n"
                f"💡 Tip: Read the skill for more details:\n"
                f"   read_resource('1000genome://skill')"
            )
        )]

    try:
        # Change to generator directory
        os.chdir(GENERATOR_PATH)

        # Run daxgen.py to generate DAX
        dax_cmd = [
            "python3", "daxgen.py",
            "--dax", "1000genome.dax",
            "--ind-jobs", str(ind_jobs)
        ]
        subprocess.run(dax_cmd, check=True, capture_output=True, text=True)

        # Convert DAX to HyperFlow JSON
        convert_cmd = ["hflow-convert-dax", "1000genome.dax"]
        result = subprocess.run(convert_cmd, check=True, capture_output=True, text=True)
        workflow_json = result.stdout

        # Parse and add metadata
        workflow = json.loads(workflow_json)
        workflow["name"] = name
        workflow["version"] = version

        # Add generation info
        task_count = len(workflow.get("processes", []))

        return [TextContent(
            type="text",
            text=(
                f"✅ Workflow generated successfully!\n\n"
                f"Parameters:\n"
                f"  - individuals_per_job: {ind_jobs}\n"
                f"  - Total tasks: {task_count}\n\n"
                f"{json.dumps(workflow, indent=2)}"
            )
        )]

    except subprocess.CalledProcessError as e:
        return [TextContent(
            type="text",
            text=f"Error generating workflow: {e.stderr}"
        )]
    except Exception as e:
        return [TextContent(
            type="text",
            text=f"Error: {str(e)}"
        )]


async def get_workflow_stats(arguments: dict):
    """Get statistics about a workflow."""
    workflow_json = arguments.get("workflow_json", "")

    try:
        # Try to parse as JSON or read from file
        if workflow_json.startswith("{"):
            workflow = json.loads(workflow_json)
        else:
            with open(workflow_json, "r") as f:
                workflow = json.load(f)

        # Count tasks by type
        processes = workflow.get("processes", [])
        task_types = {}
        for proc in processes:
            task_name = proc.get("name", "unknown")
            # Extract task type from name (e.g., "individuals_1" -> "individuals")
            task_type = task_name.rsplit("_", 1)[0] if "_" in task_name else task_name
            task_types[task_type] = task_types.get(task_type, 0) + 1

        # Count signals (files)
        signals = workflow.get("signals", [])
        input_signals = [s for s in signals if s.get("name", "").endswith(":in")]
        output_signals = [s for s in signals if s.get("name", "").endswith(":out")]

        stats = {
            "name": workflow.get("name", "unknown"),
            "version": workflow.get("version", "unknown"),
            "total_tasks": len(processes),
            "task_breakdown": task_types,
            "total_signals": len(signals),
            "input_signals": len(input_signals),
            "output_signals": len(output_signals)
        }

        return [TextContent(
            type="text",
            text=json.dumps(stats, indent=2)
        )]

    except json.JSONDecodeError as e:
        return [TextContent(
            type="text",
            text=f"Invalid JSON: {str(e)}"
        )]
    except Exception as e:
        return [TextContent(
            type="text",
            text=f"Error: {str(e)}"
        )]


async def list_chromosomes(arguments: dict):
    """List available chromosome data files."""
    try:
        data_csv_path = os.path.join(GENERATOR_PATH, "data.csv")

        if not os.path.exists(data_csv_path):
            return [TextContent(
                type="text",
                text="data.csv not found"
            )]

        chromosomes = []
        with open(data_csv_path, "r") as f:
            for line in f:
                parts = line.strip().split(",")
                if len(parts) >= 3:
                    chromosomes.append({
                        "vcf_file": parts[0],
                        "row_count": int(parts[1]),
                        "annotation_file": parts[2]
                    })

        result = {
            "chromosome_count": len(chromosomes),
            "chromosomes": chromosomes
        }

        return [TextContent(
            type="text",
            text=json.dumps(result, indent=2)
        )]

    except Exception as e:
        return [TextContent(
            type="text",
            text=f"Error: {str(e)}"
        )]


async def validate_workflow(arguments: dict):
    """Validate a HyperFlow workflow definition."""
    workflow_json = arguments.get("workflow_json", "")

    errors = []
    warnings = []

    try:
        workflow = json.loads(workflow_json)

        # Check required fields
        if "processes" not in workflow:
            errors.append("Missing 'processes' field")
        if "signals" not in workflow:
            errors.append("Missing 'signals' field")

        # Check processes
        processes = workflow.get("processes", [])
        for i, proc in enumerate(processes):
            if "name" not in proc:
                errors.append(f"Process {i} missing 'name'")
            if "function" not in proc:
                errors.append(f"Process {i} missing 'function'")

        # Check signals
        signals = workflow.get("signals", [])
        signal_names = set()
        for i, sig in enumerate(signals):
            if "name" not in sig:
                errors.append(f"Signal {i} missing 'name'")
            else:
                if sig["name"] in signal_names:
                    warnings.append(f"Duplicate signal name: {sig['name']}")
                signal_names.add(sig["name"])

        result = {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "process_count": len(processes),
            "signal_count": len(signals)
        }

        return [TextContent(
            type="text",
            text=json.dumps(result, indent=2)
        )]

    except json.JSONDecodeError as e:
        return [TextContent(
            type="text",
            text=json.dumps({
                "valid": False,
                "errors": [f"Invalid JSON: {str(e)}"],
                "warnings": []
            }, indent=2)
        )]
    except Exception as e:
        return [TextContent(
            type="text",
            text=f"Error: {str(e)}"
        )]


async def estimate_tasks(arguments: dict):
    """Estimate task count for given parameters."""
    ind_jobs = arguments.get("individuals_per_job", 250)
    chromosomes = arguments.get("chromosomes", 10)

    # Validate
    if 250000 % ind_jobs != 0:
        nearest = sorted(VALID_IND_JOBS, key=lambda x: abs(x - ind_jobs))[:5]
        return [TextContent(
            type="text",
            text=(
                f"❌ Invalid individuals_per_job={ind_jobs}\n"
                f"Must divide 250,000 evenly.\n"
                f"Nearest valid: {nearest}"
            )
        )]

    # Calculate
    tasks_per_chr = 250000 // ind_jobs
    individuals_tasks = chromosomes * tasks_per_chr
    merge_tasks = chromosomes
    sifting_tasks = chromosomes
    overlap_tasks = chromosomes
    frequency_tasks = chromosomes
    populations_tasks = 1

    total = individuals_tasks + merge_tasks + sifting_tasks + overlap_tasks + frequency_tasks + populations_tasks

    estimate = {
        "parameters": {
            "individuals_per_job": ind_jobs,
            "chromosomes": chromosomes,
            "tasks_per_chromosome": tasks_per_chr
        },
        "task_breakdown": {
            "individuals": individuals_tasks,
            "individuals_merge": merge_tasks,
            "sifting": sifting_tasks,
            "mutation_overlap": overlap_tasks,
            "frequency": frequency_tasks,
            "populations": populations_tasks
        },
        "total_tasks": total
    }

    return [TextContent(
        type="text",
        text=json.dumps(estimate, indent=2)
    )]


async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
