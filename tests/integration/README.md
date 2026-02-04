# Integration Tests

Integration tests for the 1000genome-workflow using Docker Compose.

## Prerequisites

- Docker and Docker Compose installed
- All workflow images built (`make build-all` from repository root)
- workflow-composer installed (`pip install -e workflow-composer`)

## Quick Start

```bash
# Run workflow-composer integration test (recommended)
./test-workflow-composer.sh --parallelism small --yes

# Or run the HLA region test with real data
./test-hla-region.sh --quick --yes
```

## Available Tests

### Workflow Composer Test (Recommended)

Tests the workflow-composer's ability to generate working HyperFlow workflows.

```bash
./test-workflow-composer.sh --parallelism small --yes
```

Parameters:
- Uses micro test data (10,000 variants, 30 individuals)
- Generates workflow via workflow-composer (not legacy daxgen.py)
- Verifies all expected outputs

### HLA Region Test (Real Data)

Tests with real 1000 Genomes data downloaded via tabix.

```bash
# Quick mode (~100kb region, faster)
./test-hla-region.sh --quick --yes

# Full HLA region (~5Mb, slower)
./test-hla-region.sh --yes
```

Parameters:
- Downloads real chromosome 6 HLA region data via tabix
- Trims to 30 individuals for faster testing (minimum required by mutation_overlap.py)
- Tests complete workflow with production data
- **26 total tasks** (10 individuals + 1 merge + 1 sifting + 7×2 analyses)

### Legacy Micro Workflow

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

### Legacy Tiny Workflow (Full data, single job)

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

---

## End-to-End Pipeline Documentation

For detailed documentation of the 5-phase pipeline (INTERPRET → PLAN → EXTRACT → GENERATE → EXECUTE), see:

- **[workflow-composer/README.md](../../workflow-composer/README.md#end-to-end-pipeline)** - Detailed phase descriptions, diagrams, and agent implementation guide
- **[Main README](../../README.md#end-to-end-pipeline)** - Pipeline overview
