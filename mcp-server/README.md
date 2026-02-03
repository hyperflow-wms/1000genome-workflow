# 1000genome MCP Server

MCP (Model Context Protocol) server for AI-assisted 1000genome workflow generation.

## Overview

This MCP server exposes tools and resources (skills) for generating and analyzing HyperFlow workflows for the 1000 Genomes Project mutation overlap analysis.

## Resources (Skills)

AI agents should read these resources before using tools to understand parameter constraints and extraction rules.

| URI | Description |
|-----|-------------|
| `1000genome://skill` | **Read first!** Parameter extraction rules and constraints |
| `1000genome://populations` | Population codes (AFR, EUR, etc.) with sample counts |
| `1000genome://research` | Scientific context, runtime/memory estimates |

### Usage Pattern

```
1. read_resource("1000genome://skill")  # Learn constraints
2. estimate_tasks({...})                 # Verify parameters
3. generate_workflow({...})              # Generate workflow
```

## Tools

### generate_workflow

Generate a 1000genome HyperFlow workflow DAG.

**Parameters:**
- `name` (string, optional): Workflow name (default: "1000genome")
- `version` (string, optional): Workflow version (default: "1.0.0")
- `individuals_per_job` (integer, optional): Rows per parallel task (default: 250)

**Critical constraint:** `individuals_per_job` must divide 250,000 evenly.

Valid values: 1, 2, 4, 5, 10, 20, 25, 50, 100, 125, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000, 250000...

**Returns:** HyperFlow JSON workflow definition

### estimate_tasks

Estimate task count for given parameters without generating workflow.

**Parameters:**
- `individuals_per_job` (integer, optional): Rows per parallel task (default: 250)
- `chromosomes` (integer, optional): Number of chromosomes (default: 10)

**Returns:** Task breakdown and total count

### get_workflow_stats

Get statistics about a workflow.

**Parameters:**
- `workflow_json` (string): Workflow JSON content or path to workflow file

**Returns:** Statistics including task count, file count, task breakdown by type

### list_chromosomes

List available chromosome data files from data.csv.

**Returns:** List of chromosomes with VCF files and annotation files

### validate_workflow

Validate a HyperFlow workflow definition.

**Parameters:**
- `workflow_json` (string): Workflow JSON content to validate

**Returns:** Validation result with errors and warnings

## Usage with Claude Desktop

Add to your Claude Desktop configuration (`~/.config/claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "1000genome": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "hyperflowwms/1000genome-mcp:1.1"]
    }
  }
}
```

## Building

```bash
make image   # Build Docker image
make push    # Push to Docker Hub
```

## Running Locally

```bash
docker run -i --rm hyperflowwms/1000genome-mcp:1.1
```

## Version History

- **1.1**: Added resources (skills), parameter validation, estimate_tasks tool
- **1.0**: Initial release with basic tools
