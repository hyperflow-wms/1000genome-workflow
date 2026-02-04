# Integration Tests

End-to-end integration tests for the 1000genome-workflow pipeline.

## Quick Start

```bash
# Run the micro smoke test (fastest, uses pre-existing data)
./run-research-tests.sh micro

# Run HLA region test with real data extraction
./run-research-tests.sh --mock-llm eas-hla-autoimmune

# List all available test cases
./run-research-tests.sh --list
```

## Research Test Framework

The main integration test is `run-research-tests.sh`, which exercises the full 5-phase pipeline:

```
INTERPRET → PLAN → EXTRACT → GENERATE → EXECUTE
```

### Test Cases

Defined in `cases.yaml`:

| ID | Description | Volume | Default behavior |
|----|-------------|--------|------------------|
| `micro` | Smoke test with pre-existing data | ~1 MB | Full E2E |
| `eas-hla-autoimmune` | HLA region analysis | ~25 MB | Full E2E |
| `eur-afr-hla` | EUR vs AFR in HLA | ~25 MB | Full E2E |
| `brca-breast-cancer` | BRCA1/BRCA2 genes | ~5 MB | Full E2E |
| `eur-afr-chr22` | Full chromosome 22 | ~100 MB | Stop before execute |
| `genome-wide-null` | All chromosomes | ~15 GB | Stop after plan |

### Usage

```bash
# Run specific test
./run-research-tests.sh <test-id>

# Run with mock LLM (skip real interpretation)
./run-research-tests.sh --mock-llm <test-id>

# Force full execution regardless of volume
./run-research-tests.sh -y <test-id>

# Stop at specific phase
./run-research-tests.sh --stop-before-extract <test-id>
./run-research-tests.sh --stop-before-execute <test-id>
```

### Volume Thresholds

Tests auto-stop based on estimated data transfer:
- **< 50 MB**: Run end-to-end
- **50-500 MB**: Stop before execute
- **> 500 MB**: Stop after plan

Use `-y` to override and force execution.

## Prerequisites

- Docker and Docker Compose
- workflow-composer installed: `pip install -e workflow-composer`
- Workflow images built: `make build-all` (from repo root)

## Generated Files

Each test run creates a workflow directory (e.g., `workflow-eas-hla-autoimmune/`) containing:

### Pipeline Artifacts (interesting to inspect)

| File | Phase | Description |
|------|-------|-------------|
| `intent.json` | INTERPRET | Structured research intent from NL parsing |
| `plan.json` | PLAN | Advisory plan with data commands and estimates |
| `workflow-estimated.json` | PLAN | Preliminary workflow based on estimated counts |
| `data.csv` | EXTRACT | Manifest: `vcf_file,row_count,annotation_file` |
| `workflow.json` | GENERATE | Final production workflow |

### Extracted Data (from EXTRACT phase)

| File | Description |
|------|-------------|
| `ALL.chr{N}.{region}.vcf` | Extracted VCF with variant data |
| `ALL.chr{N}.{region}.annotation.vcf` | SIFT annotations for sifting |
| `columns.txt` | Sample IDs (one per line) |
| `AFR`, `EUR`, `EAS`, ... | Population membership files |

### Workflow Outputs (from EXECUTE phase)

| File | Description |
|------|-------------|
| `chr{N}n.tar.gz` | Merged individuals output |
| `sifted.SIFT.chr{N}.txt` | SIFT-filtered variants |
| `chr{N}-{POP}.tar.gz` | Mutation overlap per population |
| `chr{N}-{POP}-freq.tar.gz` | Frequency analysis per population |

---

## Legacy Tests

These older tests are still available but `run-research-tests.sh` is preferred:

| Script | Description |
|--------|-------------|
| `test-workflow-composer.sh` | Tests workflow-composer with micro data |
| `test-hla-region.sh` | Downloads HLA data via tabix |
| `setup-micro-workflow.sh` + `run-workflow.sh` | Manual micro workflow setup |

---

## Documentation

For detailed pipeline documentation, see:
- [workflow-composer/README.md](../../workflow-composer/README.md#detailed-documentation)
- [Main README](../../README.md#end-to-end-pipeline)
