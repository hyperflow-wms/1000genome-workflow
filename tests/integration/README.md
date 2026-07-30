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

| ID | Description | Transfer | Default behavior |
|----|-------------|----------|------------------|
| `micro` | Smoke test with pre-existing data; skips INTERPRET and EXTRACT | ~1 MB | Full E2E |
| `brca1-gbr` | BRCA1 in the British population; fastest real end-to-end test | ~0.4 MB | Full E2E |
| `brca-breast-cancer` | BRCA1/BRCA2 genes, five populations | ~5 MB | Full E2E |
| `eas-hla-autoimmune` | HLA region analysis | ~25 MB | Full E2E |
| `eur-afr-hla` | EUR vs AFR in HLA | ~25 MB | Full E2E |
| `eur-afr-chr22` | Full chromosome 22 | ~100 MB | Stop before execute |
| `genome-wide-null` | All chromosomes | ~15 GB | Stop after plan |

Transfer is compressed download size, which is what the auto-stop thresholds
weigh. Disk footprint is much larger — the HLA cases expand to ~1.6 GB — so a
case that runs end-to-end by default is cheap to fetch, not cheap to run.

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

# Re-run EXECUTE against data already extracted, without downloading again
./run-research-tests.sh --execute-only -y <test-id>
```

| Option | Effect |
|--------|--------|
| `--list` | List available test cases and exit |
| `--mock-llm` | Use the case's `mock_intent` instead of calling an LLM |
| `--model MODEL` | LLM model for INTERPRET (default: see below) |
| `--stop-before-extract` | Stop after PLAN |
| `--stop-before-execute` | Stop after GENERATE |
| `--execute-only` | Regenerate `workflow.json` from the data already in the workflow directory and run EXECUTE. Needs a previous run's `data.csv` and `intent.json`; no download, no LLM call |
| `-y`, `--yes` | Non-interactive; proceed through all phases |
| `-p`, `--parallelism` | Per-task memory budget: `small`=256MB, `medium`=512MB, `large`=1024MB |
| `--vcpus N` | Size parallelism for a host with N vCPUs |
| `--ind-jobs N` | Set individuals tasks per chromosome explicitly, overriding the recommendation |
| `--max-samples-per-pop N` | Cap individuals per population in `columns.txt` |
| `-v`, `--verbose` | Verbose output |

`--ind-jobs` takes precedence over `--vcpus`, which takes precedence over
`--parallelism`. Whatever arrives is clamped to a memory-safe range per
chromosome, so an over-large value costs throughput rather than the host.

### Volume Thresholds

A case never declares its transfer volume. PLAN estimates it, records it as
`data_preparation.estimated_transfer_mb` in `plan.json`, and the harness compares
that against the thresholds in `cases.yaml`:

- **< 50 MB**: Run end-to-end
- **50-500 MB**: Stop before execute
- **> 500 MB**: Stop after plan

Use `-y` to override and force execution.

## Prerequisites

- Docker and Docker Compose
- workflow-composer installed: `pip install -e "workflow-composer[all]"`
- Workflow images built: `make build-all` (from repo root)
- **For INTERPRET phase**: a Gemini API key

### LLM Configuration

The INTERPRET phase uses an LLM to parse natural language research questions.
The harness sources `.env` from the repo root before anything else, so both the
key and the model belong there:

```
GEMINI_API_KEY=your-key-here
WORKFLOW_COMPOSER_MODEL=gemini/gemini-flash-latest
```

Equivalently, export them in the shell. The model defaults to
`gemini/gemini-flash-latest` and can be overridden per run with `--model`. It is
a floating alias on purpose: Google retires dated Gemini releases, and a pinned
one eventually fails every interpretation with a 404.

Use `--mock-llm` to skip the LLM entirely and take the `mock_intent` from
`cases.yaml` — useful in CI and when no key is available.

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
| `ALL.chr{N}.{region}.vcf` | Extracted VCF, annotated with rs IDs from the sites file |
| `ALL.chr{N}.{region}.annotation.vcf` | SIFT annotations for sifting |
| `columns.txt` | A single tab-separated line: the VCF's 9 fixed columns followed by one field per sample |
| `AFR`, `EUR`, `EAS`, ... | Population membership files |

The extracted VCF carries rs IDs because the analysis matches variants to
individuals by rs number. The 1000 Genomes genotype files leave the ID column
empty, so EXTRACT copies the IDs across from the sites annotation file; without
that step every downstream chart comes out blank.

### Workflow Outputs (from EXECUTE phase)

| File | Description |
|------|-------------|
| `chr{N}n-{start}-{stop}.tar.gz` | Per-individual files for one slice of the input, one archive per individuals task |
| `chr{N}n.tar.gz` | The above merged into a single archive |
| `sifted.SIFT.chr{N}.txt` | SIFT-filtered variants |
| `chr{N}-{POP}.tar.gz` | Mutation overlap per population |
| `chr{N}-{POP}-freq.tar.gz` | Frequency analysis per population |
| `plots_no_sift/` | Charts from the two analysis stages |
| `logs-hf/` | Per-task engine logs, one pair of files per task |

Two cautions when comparing runs. The `.tar.gz` archives embed timestamps, so
identical contents still differ byte-for-byte — compare extracted trees instead.
And `mutation_overlap.py` and `frequency.py` sample without a fixed seed, so
their archives and every chart differ between runs even with no code change;
only the individuals stage is reproducible.

---

## Deferred Generation

PLAN writes `workflow-estimated.json` from an estimated variant count, before any
data is downloaded, so a workflow can be reviewed while it is still cheap to
change. GENERATE writes `workflow.json` from the exact count once the data is on
disk.

After GENERATE the harness checks that the two differ **only** in the individuals
stage, whose task count is derived from the row count:

```
[OK]   Estimate held: individuals 11 -> 15, variants 176,606 -> 166,052 (+6.4%), other stages unchanged
```

A different population set, or a missing merge or sifting step, fails the test —
it means the workflow that was reviewed is not the workflow that will run.

The variant percentage is reported, not asserted. It reads high because the
estimator carries a safety margin, and it is worth watching: that same estimate
decides where a run auto-stops, so drift in it misroutes those decisions with
nothing else to reveal them.

---

## Documentation

For detailed pipeline documentation, see:
- [workflow-composer/README.md](../../workflow-composer/README.md#detailed-documentation)
- [Main README](../../README.md#end-to-end-pipeline)
