# 1000genome-workflow

HyperFlow port of the Pegasus 1000genome workflow for identifying mutational overlaps using data from the 1000 Genomes Project.

## Overview

This workflow analyzes genetic variation data from the 1000 Genomes Project to identify mutational overlaps across populations. It processes VCF files for multiple chromosomes through a series of analysis steps including individual processing, sifting, mutation overlap detection, and frequency calculation.

## Repository Structure

```
1000genome-workflow/
├── worker-base-image/      # Base Docker image with analysis scripts
├── worker-image/           # HyperFlow worker image (Kubernetes)
├── workflow-generator/     # Workflow DAG generation tools
├── mcp-server/             # MCP server for AI-assisted workflow generation
├── data-container/         # Input data + workflow.json (~1.7GB image)
├── scripts/                # Utility scripts
└── fargate/                # AWS Fargate-specific components (legacy)
```

## Docker Images

| Image | Description |
|-------|-------------|
| `hyperflowwms/1000genome-worker-base` | Base image with Python analysis scripts |
| `hyperflowwms/1000genome-worker` | HyperFlow worker with job-executor |
| `hyperflowwms/1000genome-generator` | Workflow DAG generator |
| `hyperflowwms/1000genome-mcp` | MCP server for AI integration |
| `hyperflowwms/1000genome-data` | Input data (VCF + annotations, ~1.7GB) |

## Input Data

The workflow requires input data (~1.7GB) including VCF files, annotation files, and population sample lists.

**Note:** Annotation files (~1.2GB) are not stored in the git repository due to size. Use one of the following methods:

### Option 1: Use the data image (recommended)

```bash
# Prepare input data in a local directory
docker run --rm -v $(pwd)/input-data:/mnt/data hyperflowwms/1000genome-data:1.0 sh /prepare_data.sh
```

### Option 2: Download annotation files manually

```bash
cd data-container
./download_annotations.sh    # Downloads ~1.2GB from 1000 Genomes FTP
make image                   # Build data image locally
```

See [data-container/README.md](data-container/README.md) for details.

## Building

```bash
# Build all images
make build-all

# Build individual images
make build-worker-base
make build-worker
make build-generator
make build-mcp
make build-data

# Push all images to Docker Hub
make push-all
```

## Generating Workflows

Using Docker:
```bash
make generate
```

Or manually:
```bash
cd workflow-generator
docker build -t hyperflowwms/1000genome-generator .
docker run --rm -v $(pwd)/../data:/output hyperflowwms/1000genome-generator \
    sh -c "cd /1000genome-workflow && ./generate_workflow.sh && cp workflow.json /output/"
```

## MCP Server

The MCP server enables AI-assisted workflow generation. See [mcp-server/README.md](mcp-server/README.md) for details.

Add to Claude Desktop configuration:
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

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

This project is a HyperFlow port of the [Pegasus 1000genome-workflow](https://github.com/pegasus-isi/1000genome-workflow), originally developed by the University of Southern California. The workflow generator and Pegasus DAX libraries are used under the Apache License 2.0.

## References

- [1000 Genomes Project](https://www.internationalgenome.org/)
- [HyperFlow Workflow Management System](https://github.com/hyperflow-wms/hyperflow)
- [Pegasus Workflow Management System](https://pegasus.isi.edu/)
