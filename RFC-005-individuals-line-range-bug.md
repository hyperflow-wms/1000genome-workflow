# RFC-005: individuals.py slices file lines with variant indices

Status: fixed and verified
Severity: silent scientific data loss on every HyperFlow run

## 1. Summary

`individuals.py` receives a variant range and applies it to raw file lines.
VCF header lines occupy the front of that window, so the tail of the region is
never processed. Nothing fails: the stage exits 0 and writes the expected
archive, so every "outputs present" check passes.

The loss is `min(H, V)` variants, where `H` is the VCF header line count and
`V` the variant count. It does not depend on `ind_jobs`. When `V <= H` the
stage processes nothing at all and emits one empty file per individual.

Measured against the 20130502 release, `H = 253` on every chromosome used in
the evaluation (chr6, 7, 11, 13, 17, 19).

## 2. Reproduction

No Docker or network required:

```bash
cd /tmp && mkdir t && cd t
cp <repo>/engines/nextflow/testdata/ALL.chr17.brca1.vcf .
cp <repo>/engines/nextflow/testdata/columns.gbr91.txt columns.txt

# 113 variants, the size of the APOE region
python3 <repo>/worker-base-image/scripts/individuals.py ALL.chr17.brca1.vcf 17 1 114 113
```

Output:

```
== Streamed 0 lines, filled 91 individuals in 0.00 sec
== Done. Zipping 91 files into chr17n-1-114.tar.gz.
```

Zero variants processed, 91 empty files, exit status 0.

## 3. Root cause

`worker-base-image/scripts/individuals.py`:

```python
ending = stop if total == -1 else min(stop, total)
...
for line in itertools.islice(f, counter, ending):
    if line.startswith('#'):
        continue
```

`islice` indexes raw file lines, but the `#` filter runs inside the loop, so
header lines consume slots in the window. The caller supplies variant indices:
`data.csv` records `2369` for BRCA1, which is `grep -vc '^#'`, and the
generator emits `individuals.py ALL.chr17.brca1.vcf 17 1 2370 2369`.

With `H = 253`: the window is raw indices `[1, 2369)`, data lines occupy
`[253, 2622)`, so only 2116 of 2369 variants are seen.

This predates the streaming rewrite. The comment records the previous
behaviour as `min(stop, len(file))` over `readlines()`, which sliced raw lines
in the same way.

The Nextflow port is unaffected: `CHUNK_VCF` strips headers with awk before
calling the script, so line indices and variant indices coincide there. That
is why the two engines disagree.

## 4. Impact

`lost = min(H, V)`, with `H = 253`:

| Region | Chr | Variants | Processed | Lost | Loss |
|---|---|---|---|---|---|
| HLA | 6 | 166,052 | 165,799 | 253 | 0.15% |
| CFTR | 7 | 4,391 | 4,138 | 253 | 5.8% |
| BRCA2 | 13 | 2,502 | 2,249 | 253 | 10.1% |
| BRCA1 | 17 | 2,369 | 2,116 | 253 | 10.7% |
| HBB | 11 | 136 | 0 | 136 | **100%** |
| APOE | 19 | 113 | 0 | 113 | **100%** |

Confirmed end to end on BRCA1/GBR: HyperFlow's merged output stops at position
43117462, exactly the 2116th variant, while Nextflow reaches 43124649 over the
same input. Across all 91 individuals HyperFlow's rows are a strict subset of
Nextflow's, with no row unique to HyperFlow.

## 5. Effect on reported results

Affected — the analysis stages consumed truncated or empty input:

- `tab:e2e-results`: all three queries. Q3 (CFTR + HBB + APOE) is worst — two
  of its three regions contributed no variants at all, so its runtime measures
  a workflow that largely did nothing.
- Any downstream `mutation_overlap` and `frequency` output, including plots.

Not affected — these do not depend on the individuals stage:

- `tab:deferred-gen` in full. Actual row counts come from the extracted VCFs,
  and parallelism calibration and transfer savings are computed before this
  stage runs.
- Intent extraction accuracy, the Skills ablation, and `tab:ablation-*`.
- Provisioning and LLM timings.

The port's troubleshooting note attributing empty results solely to missing
rs-ID annotation should be revised: this is a second, independent cause.

## 6. Fix

Slice variants, not lines, and make the two engines agree on the convention.
Filter headers before `islice`:

```python
data = (line for line in f if not line.startswith('#'))
for line in itertools.islice(data, counter, ending):
```

The callers currently disagree on the base, so both must be settled together:

| Caller | Passes | Meaning |
|---|---|---|
| HyperFlow generator | `1, 2370, 2369` | 1-based over variants |
| Nextflow `main.nf` | `0, chunk_size, chunk_size` | 0-based over a pre-stripped chunk |

A negative `islice` start raises, so `counter - 1` cannot simply be applied —
it would break the Nextflow call. Settle on 0-based half-open `[counter,
ending)` over variants, which the Nextflow path already satisfies, and change
the generator to emit `0, V, V`. Then `ending = min(stop, total)` stays as it
is and both engines index identically.

## 7. Verification

- Unit: the reproduction in §2 must yield 113 variants, not 0.
- Chunk coverage: for any `ind_jobs`, the union of chunk ranges must cover
  every variant exactly once, with no task holding zero rows.
- Cross-engine: rerun BRCA1/GBR on both engines with the same 91-sample
  cohort; the merged `chr17n.tar.gz` must match exactly, compared as extracted
  trees. It is the only fully deterministic stage, so it is the one that can be
  compared byte for byte.
- Regression: `python3 -m pytest workflow-composer/tests/ -q`. Note that
  `test_chunk_equivalence.py` and the preserved baseline at
  `engines/hyperflow/harness/workflow-eur-afr-hla-baseline/` were produced by
  the current behaviour, so they encode the truncation and must be regenerated
  rather than treated as ground truth.


## 8. Verification results

Applied and confirmed on BRCA1/GBR, 91 individuals, both engines run from this
repository against the same input.

`individuals.py` now filters headers before slicing, and the HyperFlow
generator emits 0-based ranges, matching the convention the Nextflow backend
already used. Worker images were rebuilt as 1.4 rather than overwriting 1.3.

| Comparison | `chr17n.tar.gz` | analysis bundles |
|---|---|---|
| HyperFlow vs Nextflow | **identical, 91 files** | same shape |
| Nextflow vs Nextflow, same input | identical, 91 files | same shape |

Before the fix HyperFlow stopped at position 43117462, exactly the 2116th
variant; both engines now reach the same final variant, and no row is unique
to either side.

The same-engine control is what licenses the claim. `mutation_overlap` and
`frequency` draw with `random.sample()` and no seed, and every file they emit
descends from those draws, so their contents differ between two runs of one
engine just as they do between engines. Comparing them would report a
difference that says nothing about the engine, so the comparison checks their
structure instead.

Two further differences turned out not to be scientific:

- Nextflow's `groupTuple` emitted chunks in completion order, so its merged
  output row order varied between runs of identical input. Chunks are now
  sorted by start offset, which is also the order HyperFlow merges in. Without
  this the two engines held the same variants in a different order.
- HyperFlow's `frequency` archive carries 10 extra files, exactly
  `mutation_overlap`'s outputs. Both tasks share one working directory there,
  so the archiving step sweeps up its neighbour's files; Nextflow isolates
  each task. A packaging artifact of the execution model.

### Remaining work

`engines/hyperflow/harness/workflow-eur-afr-hla-baseline/` still holds ten
chunk archives from the old 1-based convention, including
`chr6n-166051-182656.tar.gz` whose range runs past the 166052-variant
threshold. Only the first chunk is exercised by a test and it has been
regenerated; the rest are stale and should not be treated as reference data.
