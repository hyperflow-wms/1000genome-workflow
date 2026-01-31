# Integration Tests

Integration tests for the 1000genome-workflow using Docker Compose.

## Prerequisites

- Docker and Docker Compose installed
- All workflow images built (`make build-all` from repository root)

## Quick Start

```bash
# Setup the micro workflow (fast, ~2-3 minutes)
./setup-micro-workflow.sh

# Run the workflow
./run-workflow.sh workflow-micro
```

## Available Workflows

### Micro Workflow (Recommended for testing)

```bash
./setup-micro-workflow.sh
./run-workflow.sh workflow-micro
```

Parameters:
- 1 chromosome (chr1)
- 10,000 rows (instead of 250,000)
- 5 parallel individuals jobs
- 30 individuals (minimum 26 required by mutation_overlap.py)
- **21 total jobs**, completes in ~2-3 minutes

### Tiny Workflow (Full data, single job)

```bash
./setup-tiny-workflow.sh
./run-workflow.sh workflow-tiny
```

Parameters:
- 1 chromosome (chr1)
- 250,000 rows (full data)
- 1 individuals job
- 2,504 individuals (all)
- **17 total jobs**, takes much longer (~hours)

## Configuration

### Max Parallelism

Control how many jobs run in parallel:

```bash
MAX_PARALLELISM=30 ./run-workflow.sh workflow-micro
```

Default is 20.

## Output Files

After successful execution, the workflow directory contains:

| File | Description |
|------|-------------|
| `chr1n-*.tar.gz` | Individual job outputs (variant data per row range) |
| `chr1n.tar.gz` | Merged individuals output |
| `sifted.SIFT.chr1.txt` | Filtered variants with SIFT scores |
| `chr1-{POP}.tar.gz` | Mutation overlap analysis per population |
| `chr1-{POP}-freq.tar.gz` | Frequency analysis per population |

## Cleanup

```bash
# Remove workflow directory
rm -rf workflow-micro

# Remove Docker resources
docker-compose down
```
