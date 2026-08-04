# The capacity model

Maintainer documentation for `core/capacity.py`. This is not a knowledge
document: nothing here is loaded into a prompt, and no stage decides capacity
by reading prose. `recommend_capacity` computes it deterministically from
region estimates and the coefficients below. This page exists for whoever
next changes that function, recalibrates the coefficients, or wants to know
why the formula has the shape it has — read the source alongside it; this
does not restate what the docstrings in `core/capacity.py` and
`core/performance_model.py` already say precisely.

Source: `CAPACITY-IMPLEMENTATION-PLAN.md` section 2, `RFC-006-REVIEW.md`
section 8.

## The DAG shape

A workflow is `K` independent per-region branches, each:

```
individuals (J_r chunked tasks) -> individuals_merge (1 task)
    -> sifting (1 task, omitted -- see below)
    -> { mutation_overlap (P tasks), frequency (P tasks) }
```

`P` is the population count, `J_r` the chunk count for region `r`. Extraction
completes every region's VCF before execution starts, so all `K` branches
start together at `t = 0` — this was measured, not assumed: Q3's three
branches were observed starting together and overlapping throughout.

`sifting` has no coefficients: it costs about 1 second per region and runs
concurrently with `individuals`, so it changes neither the work sum nor the
longest path materially.

## Work sums, span takes the maximum

Because the `K` branches are independent and start together:

- **Total work `W`** is additive across regions — every task on every branch
  consumes real compute time regardless of what else is running.
- **Wall-clock span `S`** is set by whichever single branch takes longest —
  a region that finishes early does not shorten the run, and one that
  finishes late does not lengthen any other region's own branch.

So `W = Σ_r work_r` while `S = max_r span_r`. This is why capacity grows with
the number of regions (more branches, more work, same span) while a single
dominant region caps how much capacity can help at all (that region's span
is the floor no matter how many slots are available). Q1 (one region 25×
larger than the others) and Q3 (many small regions) are the two ends of this
axis, and the coefficients below reproduce both.

## `J*` is resolved first

`span_r` depends on how region `r` is chunked (`J_r`), so `C*` cannot be
computed before `J` is known — and `J` must not be *derived* from `C`, or the
definition is circular.

It doesn't need to be. `J_r` sits on a trade-off contained entirely within
the region's own span, independent of capacity: finer chunking shrinks the
individuals stage's per-chunk cost (`b_ind · D_r / J_r`, falling in `J`) but
grows the merge stage's overhead (`c_merge · J_r`, rising in `J`, because
each additional chunk is another archive the merge has to fold in).
Differentiating their sum and setting the derivative to zero gives a capacity
independent optimum:

```
J*_r = sqrt(b_ind · D_r / c_merge)
```

This has no `C` in it, which is what makes the whole computation acyclic:
`J*` per region, from the span trade-off alone; then `W_i` and `S_i` per
stage, evaluated at `J*`; then `C*` from those. Nothing loops back.

For chr6 (`D = 2.745e8`) this gives `J* ≈ 26`, against a measured optimum of
20 and the pre-capacity policy's fixed 38 — the model lands near the measured
best without being told about it, the strongest independent check the
coefficients have.

This also retires `min_work`, the old floor-based guidance for how much work
one task should be worth. Its job was to stop tasks becoming too small to
amortise their fixed cost, which `c_merge · J` now expresses directly with a
measured coefficient instead of a guess.

## Why capacity is sized per stage, not by `W/S`

The first version of this model used `C* = W/S` over the whole workflow.
Measurement falsified it: Q1 run at 4 slots — what the whole-workflow `W/S`
recommends — took **1032s**, against **785s at 7 slots**. `W/S` was
under-provisioning.

The cause is `individuals_merge`: it is a single serial task, so its own
`W_i/S_i` is exactly 1.0. Averaged into one workflow-wide ratio, that serial
stage drags the whole figure down and hides that another stage — the
`individuals` stage alone, whose tasks run in parallel — can profitably use
far more capacity (a ratio of 12.3, on the same run where the blended
workflow-wide ratio was 6.3).

The fix accumulates work and span **per stage** rather than once for the
whole workflow:

```
makespan(C) = Σ_stages max(W_i/C, S_i)        floor = Σ_stages S_i
C*          = smallest C with makespan(C) <= floor × (1 + knee_tolerance)
```

Each stage's own term is work-bound while `C` is below its own `W_i/S_i` and
span-bound above it, so stages cross over at different capacities — the
shape a single whole-workflow `max(W/C, S)` cannot express, because it is
pinned at `S` for every `C` above the *smallest* stage's crossover point
(here, the merge's, which is 1). That is the whole bug: the workflow-wide
form answers "when is the slowest stage saturated" when the question is "when
is *every* stage saturated."

The obvious alternative — capacity at which every stage is individually
span-bound, `max_i(W_i/S_i)` — degenerates instead of fixing this: a stage of
`N` equal-cost tasks has `W_i/S_i = N` exactly, so that maximum is just
"however many tasks the widest stage happens to have," not a knee in the
cost curve. For Q1 it reads 28, buying 3% more makespan than 6 slots for
nearly five times the slot-seconds.

`knee_tolerance` (in `PerformanceModel`, default 0.10) is what stops the
search there instead: the smallest capacity whose predicted makespan is
within that fraction of the floor `Σ_stages S_i`, the time the workflow
cannot beat at any capacity. At 0.10 the Q1 recommendation is 9 slots against
a measured optimum of 7; 0.05 would give 12, 0.20 would give 5.
**`knee_tolerance` is a policy choice about where diminishing returns stop
being worth paying for — the same category the old `min_work` guidance was
in — not a number fitted to any measurement.** It belongs in
`PerformanceModel` precisely so a maintainer can see and change it without
it being buried inside a comparison.

Validated against measured Q1 runs: the per-stage model predicts
887/760/700s against 1032/785/802s measured at 4/7/15 slots — within 3% at
the optimum, under-predicting at both extremes because it models neither
contention nor scheduling stalls.

## The coefficient table

Every stage cost is a fixed per-task term plus a term proportional to
`D_r = V_r × I` (variants times individuals in the region):

| coefficient | value | meaning |
|---|---|---|
| `a_ind` | 8 s | fixed cost per individuals task (container start, input scan, output compression) |
| `b_ind` | 2.0e-6 s | individuals stage, per variant × individual actually written to a chunk |
| `b_merge` | 1.3e-6 s | individuals_merge, per variant × individual concatenated |
| `c_merge` | 0.8 s | individuals_merge, fixed cost per archive merged in (scales with `J_r`) |
| `a_mo` | 15 s | fixed cost per mutation_overlap task (loading and comparing against the reference) |
| `b_mo` | 3.5e-8 s | mutation_overlap, per variant × individual scanned |
| `a_fr` | 105 s | fixed cost per frequency task (dominated by reading every sample column) |
| `b_fr` | 6.0e-7 s | frequency, per variant × individual |

These live in `core/performance_model.py` as the `"rfc-006-review"` profile,
`DEFAULT_PERFORMANCE_MODEL`. `tests/test_policy_consistency.py` parses this
table and asserts it against that profile's fields directly, so the two
cannot silently drift apart — if you change one, change the other, and the
test tells you if you forget.

### Provenance

Fitted from the runs recorded in `RFC-006-REVIEW.md` section 8: two
workloads (Q1, one dominant region; Q3, many small regions), on one host,
with one shared filesystem, at low concurrency. Checked, not merely fitted:
Q1 predicts `W = 2516s` against `2239s` measured and `S = 635s` against
`~640s`; Q3 predicts `W = 1910s` against `1886s` and `S = 134s` against
`~147s`. `CAPACITY-IMPLEMENTATION-PLAN.md` section 6 records what a proper
calibration run (four Q1 configurations with per-task logs preserved, rather
than the three that currently survive) would add: per-task variance, a
second independent Q1 point, and a documented host/image/date per
`RFC-006-REVIEW.md` section 4.2's format.

### What invalidates this table

- **A change to any worker script's cost profile** — `individuals.py`,
  `individuals_merge.py`, `mutation_overlap.py`, `frequency.py` — changes
  the fixed or per-`D` term for that stage directly. The 2026-07-29
  individuals optimization is the worked example: it changed `a_ind`/`b_ind`
  and nothing here was re-measured to catch it, which is exactly the gap
  `test_policy_consistency.py` and a duration-extended calibration test are
  meant to close going forward.
- **A different storage architecture.** These coefficients assume one shared
  filesystem on one host. A network-backed volume changes the merge and
  analysis stages' per-`D` terms in opposite directions from what was
  measured here — merge reads and writes get more expensive, but the
  analysis stages' fixed cost (dominated by process/container start, not
  I/O) is less affected.
- **A different host's CPU count or contention profile.** The evidence is
  one 16-core machine. At 15 concurrent tasks on it, per-task time inflated
  29–243% while the serial merge was unaffected — a single coefficient set
  cannot describe both a lightly and a heavily loaded machine. These
  coefficients were fitted at low concurrency, which biases the resulting
  recommendation toward requesting less capacity: the cheaper of the two
  possible errors, per the two-regime argument below.
- **A different `knee_tolerance`.** This one is not invalidated by
  measurement at all, because it isn't a measurement — see above.

## The two regimes, and why they matter

Cost is allocated capacity times wall time — `C · makespan(C)`, the quantity
a cloud bill is proportional to (not task-seconds, which inflate under
contention without more work being done). With `T(C) = max(W/C, S)` for a
single stage:

```
C <= W/S   (work-bound)   T = W/C    cost = C · W/C = W      constant
C >  W/S   (span-bound)   T = S      cost = C · S            linear in C
```

Below a stage's own knee, adding slots buys time for free: total cost stays
at `W` regardless of how few slots are used, because fewer slots simply take
proportionally longer. Above it, makespan cannot improve — it is already at
the stage's floor `S` — so every extra slot is paid for with nothing in
return.

This is a *per-stage* statement, and that matters because `individuals_merge`
is span-bound at every capacity above 1 (it is one serial task; `W_i/S_i =
1.0`). So Q1's cost climbs across its entire measured range rather than
staying flat below some single knee: C=4 cost 4,128 slot-seconds and C=7 cost
5,495. A whole-workflow reading of "cost is flat below the knee" is simply
wrong whenever any stage is span-bound this early — which a serial merge
guarantees.

What survives is the direction, not the flatness: under-provisioning trades
time for money and degrades gracefully to `W/C`; over-provisioning buys
neither past a stage's own knee. Since the estimate driving all of this runs
on pre-extraction variant counts and will be wrong by a measured +6% to +18%,
that asymmetry is the reason to round `C*` down rather than up.
