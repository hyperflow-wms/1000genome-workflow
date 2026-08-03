# Nextflow backend

How to invoke the Nextflow port of the pipeline, and what artifact it
consumes. Domain and resource policy — which populations and regions a
question implies, how much memory a task gets — are engine-neutral and live
under `knowledge/domain/` and `knowledge/policy/`; this file covers only what
is specific to launching this engine.

## What `materialize` produces

`backends.nextflow.NextflowBackend.materialize` turns a `ResearchIntent` and
a resolved `Parallelism` (from `core/parallelism.py:recommend_parallelism`)
into a `LaunchSpec`:

- `files["extract.csv"]` — one row per region to extract, each carrying the
  population set: `region,chromosome,start,end,populations`, with
  `populations` a `;`-joined list. One row per named region
  (`intent.regions`), or one whole-chromosome row per entry in
  `intent.chromosomes` when no region is named.
- `files["columns.txt"]` — the sample-ID header row the worker scripts read.
- `command` — the `nextflow run` invocation below.

## Invocation

```
nextflow run engines/nextflow/main.nf \
  --populations EUR,AFR \
  --extract_csv extract.csv \
  --columns_txt columns.txt \
  --ind_jobs 12 \
  --ind_max_forks 8 \
  --task_mem 340MB
```

`engines/nextflow/main.nf` is where RFC-004 section 3 places the merged
pipeline; that merge is done outside this package, so nothing here generates
`main.nf` — this backend only parameterises it.

## How the parallelism dials bind

Per RFC-004 section 2.1, HyperFlow and Nextflow read the same
`Parallelism` differently:

| `Parallelism` field | Nextflow flag | When it binds |
|---|---|---|
| `ind_jobs` | `--ind_jobs` | consumed at runtime by the scatter |
| `max_parallelism` | `--ind_max_forks` | `maxForks`, fixed at launch |
| `est_peak_mb` | `--task_mem` | the `memory` directive |

All three values come from the single `recommend_parallelism` call that also
sizes the HyperFlow generator's `ind_jobs`; this backend never recomputes
them.

## Engine reserve

`NextflowBackend.reserve()` reports the cores and host memory Nextflow's own
launch holds back before task memory is budgeted: the JVM head process
running `main.nf`, plus the Docker daemon that launches each task's
container. This replaces HyperFlow's engine/Redis/merge-step reserve — the
two backends reserve for different things because they coordinate work
differently, which is the one piece of resource policy `EngineReserve` exists
to let each backend state for itself.

## Population validation

`intent_to_params` checks every population named in the intent against the
bundled population files (`data/populations/`: AFR, ALL, AMR, EAS, EUR, GBR,
SAS). A name not among them is dropped from `NextflowParams.populations` and
recorded in `NextflowParams.dropped_populations` — never dropped without
that record, and always logged as a warning.
