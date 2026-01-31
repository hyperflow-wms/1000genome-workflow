#!/usr/bin/env python3
"""
MCP Server for 1000genome workflow generation.

Provides tools for generating and analyzing HyperFlow workflows
for the 1000 Genomes Project mutation overlap analysis.
"""

import json
import subprocess
import sys
import os
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Initialize MCP server
server = Server("1000genome-workflow")

# Path to workflow generator scripts
GENERATOR_PATH = "/1000genome-workflow"


@server.list_tools()
async def list_tools():
    """List available MCP tools."""
    return [
        Tool(
            name="generate_workflow",
            description="Generate a 1000genome HyperFlow workflow DAG",
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
                        "description": "Number of individuals to process per job (default: 250)",
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
            description="List available chromosome data files",
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
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def generate_workflow(arguments: dict):
    """Generate a 1000genome workflow."""
    name = arguments.get("name", "1000genome")
    version = arguments.get("version", "1.0.0")
    ind_jobs = arguments.get("individuals_per_job", 250)

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

        return [TextContent(
            type="text",
            text=json.dumps(workflow, indent=2)
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
