# 1000genome MCP Server

MCP (Model Context Protocol) server for AI-assisted 1000genome workflow generation.

This Docker image packages the [workflow-composer](../workflow-composer/) as an MCP server for integration with Claude Desktop and other MCP clients.

## Quick Start

Add to Claude Desktop configuration (`~/.config/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "1000genome": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "hyperflowwms/1000genome-mcp:2.0"]
    }
  }
}
```

## Tools

### plan_workflow

Generate a workflow plan from research parameters.

**Parameters:**
- `analysis_type`: Type of analysis
  - `single_population`: Analyze one population
  - `population_comparison`: Compare two populations
  - `multi_population`: Analyze multiple populations
  - `region_analysis`: Focus on specific genomic region(s)
- `populations`: List of population codes (EUR, AFR, EAS, AMR, SAS, GBR, ALL)
- `chromosomes`: List of chromosomes (optional)
- `regions`: Genomic regions like HLA, BRCA1, BRCA2, APOE (optional)
- `parallelism`: Preset - small (10), medium (50), large (250)

**Returns:** Workflow plan with task counts, data preparation steps, and estimates

### list_known_regions

List available genomic regions with coordinates.

**Returns:** Table of regions (HLA, BRCA1, BRCA2, APOE, CYP2D6, HBB, CFTR, TP53)

### list_populations

List population codes with sample counts and descriptions.

## Resources (Skills)

The server provides skill documents as MCP resources:

| Resource | Description |
|----------|-------------|
| SKILL.md | Workflow planning guidelines |
| populations.md | Population codes and descriptions |
| genomic-regions.md | Known genomic regions |
| data-sources.md | Data source URLs (AWS, GCP, FTP) |
| research-contexts.md | Scientific context mapping |

## Building

Build from repository root:

```bash
cd mcp-server
make image
```

Or directly:

```bash
docker build -f mcp-server/Dockerfile -t hyperflowwms/1000genome-mcp:2.0 .
```

## Running Locally

```bash
docker run -i --rm hyperflowwms/1000genome-mcp:2.0
```

## Version History

- **2.0**: Migrated to workflow-composer (native Python generator with parallelism presets)
- **1.x**: Legacy daxgen.py-based generator (ind_jobs must divide 250,000)

## See Also

- [workflow-composer](../workflow-composer/) - The underlying workflow generator
- [Main README](../README.md) - Project overview
