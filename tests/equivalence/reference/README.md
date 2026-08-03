# Cross-engine reference results

Result bundles from runs of the 1000genome workflow on both engines, kept so a
later change can be checked against a known-good scientific output.

Both runs used the same input: chr17/BRCA1 test data, population GBR, and the
91-sample `columns.gbr91.txt` rather than the full 2504-sample cohort.

| Directory | Engine | Contents |
|---|---|---|
| `hyperflow/` | HyperFlow | `chr17n.tar.gz` (merged individuals), `chr17-GBR-freq.tar.gz`, `sifted.SIFT.chr17.txt` |
| `nextflow-verified/` | Nextflow | `chr17-GBR-freq.tar.gz`, `chr17-GBR.tar.gz` |

## What the comparison shows

`../compare-results.sh hyperflow nextflow-verified` reports:

```
IDENTICAL  chr17-GBR-freq.tar.gz (3002/4002 files; 1000 unseeded draws excluded)
```

Every deterministic file in the frequency bundle is byte-identical across the
two engines. The 1000 excluded files are `random_indiv17_sNO-SIFT_GBR_*.txt`,
the Monte Carlo draws that `frequency.py` produces with `random.sample()` and
no seed. Those differ between two runs of the *same* engine as well, so they
carry no information about engine equivalence and are excluded rather than
compared.

Stating this precisely matters: the bundles are not identical as archives, and
a naive `diff -r` over the whole tree reports 1000 differing files and looks
like a failure.

## Known gap

`chr17n.tar.gz` exists only on the HyperFlow side, so the comparison reports it
as missing and exits non-zero. The merged individuals output is the one fully
deterministic stage, which makes it the most informative artifact to compare,
so capturing it from a Nextflow run would strengthen the evidence. Until then
the exit status reflects a gap in the stored reference data, not a discrepancy
between the engines.
