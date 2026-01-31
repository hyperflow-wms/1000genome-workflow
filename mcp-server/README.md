# 1000genome MCP Server

MCP (Model Context Protocol) server for AI-assisted 1000genome workflow generation.

## Overview

This MCP server exposes tools for generating and analyzing HyperFlow workflows for the 1000 Genomes Project mutation overlap analysis.

## Tools

### generate_workflow

Generate a 1000genome HyperFlow workflow DAG.

**Parameters:**
- `name` (string, optional): Workflow name (default: "1000genome")
- `version` (string, optional): Workflow version (default: "1.0.0")
- `individuals_per_job` (integer, optional): Number of individuals per job (default: 250)

**Returns:** HyperFlow JSON workflow definition

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
      "args": ["run", "-i", "--rm", "hyperflowwms/1000genome-mcp:1.0"]
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
docker run -i --rm hyperflowwms/1000genome-mcp:1.0
```
