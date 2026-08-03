# 1000genome on Nextflow

A port of the 1000genome workflow to Nextflow. Every process calls the same
analysis scripts as the HyperFlow port, from the same base image, so the two
engines run one copy of the science rather than two that must be kept in step.

## Running it

Through the composer, from a research question:

```bash
cd engines/nextflow
python3 composer.py "Analyze BRCA1 gene variants in the British population."
python3 composer.py --dry-run "..."     # interpretation only, a few seconds
python3 composer.py --plan-only "..."   # extract and resolve, then stop
```

Or the pipeline directly, with parameters supplied by hand:

```bash
nextflow run main.nf --populations GBR --columns testdata/columns.gbr91.txt
nextflow run main.nf -entry extract --outdir out   # acquire and measure only
```

Requirements: Docker, Nextflow (pinned to `NXF_VER=25.10.2`; 26.x does not
parse nf-core style configs), the `workflow-composer` package, and an LLM key in
the repository root `.env` unless `--intent-json` supplies the intent. Build the
worker image once with `bash setup.sh`.

## The DAG

```
per chromosome:
  EXTRACT -> ANNOTATE -+-> CHUNK_VCF -> INDIVIDUALS -> INDIVIDUALS_MERGE -+
                       +-> SIFTING ---------------------------------+     |
                                                                    v     v
                          per (chromosome x population):  MUTATION_OVERLAP
                                                          FREQUENCY
```

Scatter-gather plus a cross product, expressed natively on channels: `flatMap`
splits a chromosome into chunks, `groupTuple` gathers them, and `combine` forms
the chromosome-by-population pairs.

Two processes have no counterpart in the HyperFlow port. `ANNOTATE` rewrites
genotype IDs from the annotation VCF, matching on CHROM+POS+REF+ALT; without it
`mutation_overlap` and `frequency` match variants by rs ID against a file that
has none, and return empty results. `CHUNK_VCF` pre-splits the VCF because
`individuals.py` reads its input through `readlines()`.

`EXTRACT` acquires data with tabix from the public 1000 Genomes release, using
the same URLs as the HyperFlow path. It runs when the composer passes
`--extract_csv`, which happens whenever the question names a region; otherwise
the bundled chr17/BRCA1 test data is used. Rows are `chrom,region,name`, with no
header.

## Phases

The composer runs four phases, of which only the last is Nextflow-specific:

| Phase | What happens |
|---|---|
| INTERPRET | question to `ResearchIntent`, shared with the HyperFlow path |
| EXTRACT | `nextflow run -entry extract` acquires the data and writes `measurements.csv` |
| RESOLVE | `recommend_parallelism` sizes `ind_jobs`, `maxForks` and per-task memory |
| EXECUTE | `nextflow run` with those values bound |

Resolution has to precede execution because `maxForks` binds when a process is
instantiated: a value arriving on a channel cannot drive it. That is why
extraction has its own entry point.

Each run writes to `runs/<timestamp>/`: `intent.json`, `prompt.txt`,
`plan.json` (measurements, resolved dials and the exact command), `extract.log`,
`execute.log`, and `results/`.

## Files

| Path | Role |
|---|---|
| `main.nf` | the pipeline, with `extract` as a second entry point |
| `nextflow.config` | Docker settings, worker image, resource directives, reports |
| `composer.py` | the four phases above |
| `worker-nf.Dockerfile` | worker image: the shared base plus bash |
| `testdata/` | chr17/BRCA1 VCF, population files, `columns.txt` (2504) and `columns.gbr91.txt` (91) |

Nextflow writes `report-*.html`, `timeline-*.html`, `dag-*.html` and
`trace-*.txt` beside each run.

## Comparing against HyperFlow

`tests/equivalence/compare-results.sh` compares two result directories. The
merged `chr*n.tar.gz` must match exactly; the analysis bundles are checked for
shape only, because `mutation_overlap` and `frequency` sample without a seed and
every file they write descends from those draws. See
`tests/equivalence/reference/README.md`.

The GUI in `gui/` runs a prompt on either engine and compares the resulting
charts side by side.
