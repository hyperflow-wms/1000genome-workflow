# Review of RFC-006, and a proposal for resource-policy knowledge

Status: review
Reviews: RFC-006 (Draft)
Evidence: measurements in this document were produced on a 16-core, 31 GiB host
running the HyperFlow docker harness, worker image 1.4-je1.4.2, 2026-08-04.

---

## 1. Judgement

RFC-006's machinery is correct and should be adopted as written: the C1–C4
placement rule, the prose/configuration/executable split, the rule that a number
in prose documents configuration rather than sourcing it, the resolution
contract's internal consistency requirement, and the adoption order that wires
before enforcing.

Its scope is wrong in one specific way. The RFC models resource policy as task
sizing under a memory bound. That was the right model when the individuals stage
dominated execution. It no longer does, and the RFC contains no quantity that
would register the change.

The recommendation is not to rewrite RFC-006 but to add one dimension to it:
capacity as a derived quantity, with a time model calibrated the same way the
memory model already is.

---

## 2. What the RFC gets right

The §1 diagnosis holds up under independent measurement. Policy prose reaches no
prompt. The harness records `PASSED` when outputs are missing. The artefact
records the recommendation's estimate rather than the effective run's.

Three design decisions are worth keeping without modification:

**C3 and C4 as separate obligations.** Splitting delivery from enforcement is
what makes the rule testable rather than a judgement call, and it correctly
predicts that a dynamic engine fails C4 while C1 and C2 are unchanged.

**Configuration owns operative values.** The alternative — prose as source —
produces the duplication the RFC documents between `mcp_server.py:212` and
`individuals-parallelism.md`.

**Calibration must declare what invalidates it.** §4.2 requires each numeric
recommendation to name the conditions under which it stops holding. This is the
part of the RFC that this review's central finding vindicates, and it is also
the part that failed to fire.

---

## 3. The bottleneck moved, and nothing recorded it

`individuals.py` was optimized on 2026-07-29. The fill loop went from
re-parsing every line once per individual to a single pass, and peak memory fell
from ~4.7 GB to ~19 MB per task. On the HLA region a chunk that took roughly
3.5 hours now takes seconds.

Before that change, the individuals stage was the workload. Sizing it correctly
was the resource decision, and a policy built entirely around `ind_jobs` under a
memory ceiling was the right policy. After the change, measured on the HLA +
BRCA1 workload across three populations:

| stage | tasks | total | share of task-seconds | share of critical path |
|---|---|---|---|---|
| frequency | 6 | 1101.6s | 49.2% | 268s |
| individuals | 39 | 629.3s | 28.1% | 157s |
| individuals_merge | 2 | 358.6s | 16.0% | 356s |
| mutation_overlap | 6 | 147.7s | 6.6% | — |
| sifting | 2 | 1.6s | 0.1% | — |

The critical path is `individuals 157s → chr6 merge 356s → chr6 frequency 268s`,
totalling 785s. The serial merge alone is 45% of it. The stage the policy governs
is 20%.

§4.2 lists "any change to the individuals worker's buffering or archiving" as a
calibration invalidator. That is exactly what changed. Nothing fired, because the
only calibrated model is the memory model, and memory was not what moved
materially — the optimization made per-task memory *smaller*, which the one-sided
memory bound reads as safe.

This is the review's central point. RFC-006 has the right mechanism pointed at
the wrong quantity, and the evidence is a change the RFC itself predicted would
matter passing through the system unremarked.

---

## 4. Consequences

### 4.1 `ind_jobs` is close to inert

Sweeping the `ind_jobs` hint on the same extracted HLA + BRCA1 data, execution
only:

| hint | effective | slots | makespan, 2 runs | mean | slot-seconds | task-seconds | utilisation |
|---|---|---|---|---|---|---|---|
| 8 | 8 | 14 | 793s, 830s | 812s | 11,364 | 2,883 | 25% |
| 20 | 20 | 15 | 752s, 764s | **758s** | 11,370 | 3,131 | 27% |
| 38 / 76 | 38 | 15 | 822s, 802s | 812s | 12,180 | 3,498 | 29% |
| 38 | 38 | **7** | 785s | 785s | **5,495** | **2,239** | **41%** |

`slot-seconds` is slots × mean makespan: what the allocation costs, the quantity a
cloud bill is proportional to. `task-seconds` is the compute actually consumed,
from one run per row. Their ratio is utilisation.

The `hint 76` run is a replicate of `hint 38`: `clamp_ind_jobs` capped it at the
recommendation. Every setting therefore has two makespan measurements, and the
replicate spread is 1.6% to 4.7%.

The `ind_jobs` curve has a clear interior minimum at 20 — the two runs at that
setting (752s, 764s) do not overlap the ranges at either neighbour (793–830s and
802–822s), so the roughly 7% advantage survives replication. Note also that
`task-seconds` rises monotonically with `ind_jobs`, 2,883 → 3,131 → 3,498, while
slot-seconds stay flat: splitting finer buys no time and costs compute.

The larger trade-off in the table is not about `ind_jobs` at all. Comparing the
same effective `ind_jobs` of 38 at two allocations:

- **7 slots**: 785s, 5,495 slot-seconds, 2,239 task-seconds, 41% utilisation
- **15 slots**: 812s mean, 12,180 slot-seconds, 3,498 task-seconds, 29%

Doubling the allocation bought nothing in time — it was 3% *slower* — while
costing 2.2× the allocation and 56% more compute actually consumed. Even against
the best 15-slot setting, `ind_jobs=20` at 758s, the seven-slot run gives up 3.4%
of wall time for 2.07× less allocation.

That last effect is contention on this host, and the run contains its own
control. Per-task means across the two runs, against how many peers each stage
runs beside:

| stage | mean @7 slots | mean @15 slots | inflation | concurrent peers |
|---|---|---|---|---|
| mutation_overlap | 24.6s | 84.5s | 243% | 12 |
| individuals | 16.1s | 30.6s | 90% | up to 15 |
| frequency | 183.6s | 237.5s | 29% | 12 |
| **individuals_merge** | 179.3s | 183.4s | **2%** | 1–2 |

`individuals_merge` gates its branch and therefore runs essentially alone. It is
the only stage that did not inflate. Every stage that runs beside a dozen peers
did. The inflation is therefore interference between concurrent tasks, not a
difference in the work.

**This does not generalise as measured.** Fifteen tasks on a 16-core host, plus
the engine and redis, saturate the CPU, and every task reads and writes one
bind-mounted directory on one disk — `mutation_overlap` and `frequency` each
extract the same 197 MB `chr6n.tar.gz`, twelve of them at once. On a multi-node
cluster the CPU component largely disappears, since the same fifteen slots spread
across nodes with their own cores. The storage component could go either way: a
cluster with local scratch would see less interference than this host, while the
NFS-backed volume the paper's runs used could see more, because the same twelve
extractions cross a network to one server.

So the honest claim is narrower than "over-allocation is negative-sum". On a
single host it is: the compute consumed rose 56% for no time saved. Whether the
same holds on a distributed cluster is unmeasured and depends on its storage
architecture. What does carry over is the shape — the allocation cost is real and
the time benefit was nil — because that rests on slot-seconds and makespan, not
on the inflation.

Against the fastest setting measured, J=20 at 15 slots and 752s, the cheapest
setting gives up 4% of wall time for 2.05× less allocation. Neither figure is
visible to any quantity in §6.

The fourth row is not a fourth setting. `clamp_ind_jobs` capped the hint of 76 at
the recommendation of 38, so it is an unplanned replicate of the third row, and
the pair bounds the measurement noise: 20s of makespan (2.5%), 19s of merge
(5%), and 215s of total individuals task-time (18%).

Against that noise, the ordering is weaker than a single comparison suggests but
survives. J=20 at 752s beats J=38's two runs (822s and 802s, mean 812s) by about
7%, which exceeds the 2.5% replicate spread, on one measurement of J=20 against
two of J=38. Total individuals work still rises sharply from J=20 to J=38 while
mean per-task time does not fall (34.9s → 36.1s and 30.6s), so the extra tasks
pay full per-task overhead for no per-task gain — contention at 15-wide. And
J=38 at concurrency 7 beat J=38 at concurrency 15 in both runs, so doubling the
allocation did not help and may have hurt.

The whole measured span of the knob the RFC is about is 752–822s, under 10%, and
part of that is noise.

The clamp has a second consequence worth naming. Because it caps the hint at the
recommendation, the over-parallelised branch of the curve cannot be reached
through the supported interface at all — measuring J > 38 requires changing the
environment's `vcpus` or `min_work`, not the hint. §2's corollary treats the
one-sided clamp as a safety question; it is also an observability one, since the
regime the policy most needs evidence about is the regime its own interface
forbids exploring.

### 4.2 Capacity is an input everywhere and an output nowhere

§5's phase table has MEASURE produce vCPUs and allocatable memory for RESOLVE to
consume. Nothing produces a capacity figure. `recommend_parallelism` takes
`vcpus` as an argument and sizes work to fill it: `raw_work = V*I/C`.

Measured against total task work, that produces:

| | makespan | slots | slot-seconds | task-seconds | utilization |
|---|---|---|---|---|---|
| Q1 (HLA+BRCA1, 3 pops) | 785s | 7 | 5,495 | 2,239 | **41%** |
| Q3 (HBB+CFTR+APOE, 5 pops) | 318s | 7 | 2,226 | 1,886 | **85%**, and starved |

Q3 had 30 independent analysis tasks queued against 7 slots; at 13 slots it would
finish in roughly 147s rather than 318s. Q1 at 4 slots would finish in the same
785s it took with 7, because its span dominates. Both runs satisfy every
invariant in §6.

### 4.3 No quantity in the model is workflow-global

`recommend_parallelism` is per chromosome. §6's invariants are per task, plus one
concurrency × memory product. The quantity that decides allocation is the DAG's
average parallelism — total work over critical path — and there is no slot for it.

The DAG is K independent per-region branches, each
`individuals(J) → merge(1) → {mutation_overlap(P), frequency(P)}`. Measured on
Q3, the three regions' branches start together and overlap completely. Width over
time is K·J, then K, then 2KP. Q1 and Q3 have nearly equal total work (2,239 vs
1,886 task-seconds) and spans differing 4.6× (675s vs 147s), giving optimal
capacities of about 3.3 and 12.8 slots. Nothing in the current inputs
distinguishes them.

### 4.4 The serial merge is unmodelled

`individuals_merge` is 45% of Q1's makespan and appears in no document.
`engine_reserve` accounts for it as one reserved core, a constant. It extracts
every one of the J archives and reads each per-individual file from each, so its
cost carries a J·I term — 38 × 1653 ≈ 63,000 file reads for chr6 — which is
measurable in the sweep (346 → 358 → 379s as J rises 8 → 20 → 38) though weaker
than that term alone predicts. §12's open questions cover the pass fraction,
`min_work`, planner benefit and the ablation; they do not cover the stage that
dominates the largest workload in the evaluation.

### 4.5 §4.3 removes the right line for the wrong reason

"Keep every available core busy" is deleted as redundant — "restates what the
core term already does." The reason given is the problem, because the core term
*does* say the same thing, in `raw_work = V*I/C`, and that is where it operates.
Deleting the prose leaves the belief intact where it has effect.

The maxim is also more interesting than simply wrong. Utilisation is
`W / (C · T)`, which by §8.1 is 100% everywhere below the knee and falls as
`C·S/W` above it. So "keep every core busy" is exactly equivalent to "do not
allocate beyond `C* = W/S`" — the target is right. What is wrong is which
variable it treats as free. It reads `C` as given and adjusts `J` to fill it,
when `J` cannot fill it: the sweep in §4.1 moves makespan under 10% across a
4.75× change in `J`, so no amount of task-splitting makes seven slots busy on a
workflow whose span is 640s. The only variable that reaches 100% utilisation is
`C`, and the policy treats it as an input.

Both the prose and the core term should change together, and the RFC should say
so — replacing "fill the cores you were given" with "request the cores the
workflow can fill".

### 4.6 §4.1 regressed against the design note

`POLICY-KNOWLEDGE-DESIGN.md` §8.2 has PLAN emit `resource_objective` and
`deadline_seconds` alongside the profile. RFC-006 §4.1 narrows the planner
fragment to memory presets, environment naming and `ind_jobs` omission, dropping
both.

That keeps the wrong half. A planning agent has no basis for choosing a memory
preset — §4.2's own ownership argument says resource policy belongs to whoever
knows the target machine. But "minimise time or minimise cost, and by when" is a
genuine policy choice a user can state, and it is the input that determines
capacity. The design note was closer to right on this specific point.

---

## 5. Proposed policy content

Replacing §4 of the RFC. Two audiences, disjoint content, plus configuration.

### 5.1 Planner-facing

Carried in the tool schema, for the same C3 reason §4.1 gives. What the planning
agent can genuinely express:

- **the objective and its order**: minimise wall time, minimise aggregate compute,
  or meet a deadline — and which yields when they conflict;
- **a deadline or budget, when the user states one**, tagged with its source;
- **the regime this workflow falls into**, stated as a consequence rather than a
  computation: a request dominated by one large region cannot be made much faster
  by adding capacity, and a request spanning many small regions across many
  populations can;
- **that `ind_jobs` should be omitted** unless carrying an explicit user
  constraint — unchanged from §4.1, and now better justified: it moves makespan
  under 10%.

Memory presets leave the planner's inputs entirely. They describe a host that has
not been allocated yet at the point the planner runs.

### 5.2 Maintainer-facing

Next to the code it justifies. §4.2's structure, extended:

- **the memory model** with its measurements, host, worker image and date, the
  observed range, and its margin — unchanged, and the part of the current policy
  the evidence supports without qualification;
- **a time model**, calibrated the same way: seconds per variant × individual for
  each stage, and a merge cost as a function of archive count and total bytes.
  Without this, work and span cannot be estimated and capacity cannot be derived;
- **the DAG's parallelism shape**: K independent branches, the per-branch chain,
  width over time as K·J → K → 2KP, and that the merge is serial and gates its
  branch. This is the reasoning a reader cannot recover from
  `recommend_parallelism`, and it is what makes the numbers interpretable;
- **what invalidates each model**, per §4.2, with the individuals optimization of
  2026-07-29 recorded as the worked example of an invalidation that was predicted
  and missed;
- **the kind of each number**, per §4.2, with `min_work` labelled as what the
  evidence shows: a guess that binds most real runs — it chose J=38 for the
  largest workload in the evaluation — and that governs a knob worth under 10%.

### 5.3 Configuration

Unchanged from §3, plus the time-model coefficients and the merge cost
parameters, versioned and invalidated on the same terms as the memory model.

### 5.4 Removed

Everything §4.3 lists, plus "keep every available core busy" — removed as wrong
rather than as duplication, together with the `V*I/C` core term that encodes it.

---

## 6. Consumption

The phase list in §5 needs one addition, and it changes an ordering the RFC
treats as fixed.

MEASURE currently has no upstream producer: it measures a cluster that already
exists. In the Conductor this is literal — provisioning creates a fixed cluster
from static Kind and Helm configuration, then reads its node capacity. If
capacity is a decision, it must be made before the thing it describes is built:

```
PLAN            estimates work and span from K, P and estimated variant counts,
                all available before extraction; emits a capacity request
                alongside the scientific scope
   ↓
PROVISION       consumes the request
   ↓
MEASURE         confirms what was actually obtained
   ↓
RESOLVE         sizes tasks to the capacity actually available
```

Capacity becomes an output of planning and an input to provisioning. RESOLVE's
contract is unchanged — it still consumes measured workload, measured
environment and typed constraints, and still produces a self-consistent
resolution object. What changes is that the environment it resolves against was
chosen for this workflow rather than inherited from it.

The estimate is necessarily coarse, since it runs on estimated variant counts
before extraction. That is acceptable: over-requesting capacity costs money and
under-requesting costs time, and both are recoverable, which is C1. RESOLVE
still enforces the memory invariants against measured values, so C4 is unaffected.

---

## 7. Verification

§8's five families stand. Two additions:

**Time calibration.** §8 already measures duration and nothing consumes it. Fail
when observed stage durations leave the declared envelope, on the same terms as
peak RSS. Had this existed, the individuals optimization would have turned it red
on 2026-07-29, which is the whole point of §4.2.

**Capacity properties.** Over a range of K, P and region sizes: the estimated
span is a lower bound on the achieved makespan; the recommended capacity does not
exceed the DAG's maximum width; and utilization at the recommended capacity stays
above a stated floor. Q1 at 41% would fail such a floor, which is the intended
behaviour.

---

## 8. The capacity formula, roughly

A first cut at the time model §5.2 asks for, fitted to the runs in this document
and checked against both. It is offered as a shape and an order of magnitude, not
as calibration — §7's calibration test is what would make these numbers
trustworthy.

> **Correction, added after measurement.** The `C* = W/S` rule below is wrong,
> and the per-stage costs are not. Q1 was run at 4 slots, the capacity `W/S`
> recommends, and took **1032s against 785s at 7 slots** — so `W/S`
> under-provisions. The cause is that `individuals_merge` is fully serial
> (`W_i/S_i = 1.0`), which drags the whole-workflow ratio down and hides that the
> individuals stage alone can use 12.3. Accumulating work and span per stage and
> taking the smallest capacity within a tolerance of the floor predicts
> 887/760/700s against 1032/785/802s measured at 4/7/15 slots, and recommends 9
> for Q1 rather than 4. The cost claim in §8.1 is likewise too strong: cost is
> flat below the knee only when every stage is work-bound, which a serial merge
> prevents. See `CAPACITY-IMPLEMENTATION-PLAN.md` §2.1 for the corrected model.
> The cost table and stage coefficients here stand; the rule built on them does
> not.

Write `D_r = V_r × I` for the data volume of region `r`: variants times the
individuals in `columns.txt`. Every stage cost is a fixed per-task term plus a
term proportional to `D`.

| stage | tasks | cost |
|---|---|---|
| individuals | `J_r` per region | `8s + 2.0e-6 · D_r/J_r` |
| individuals_merge | 1 per region | `1.3e-6 · D_r + 0.8s · J_r` |
| sifting | 1 per region | ~1s, negligible |
| mutation_overlap | `P` per region | `15s + 3.5e-8 · D_r` |
| frequency | `P` per region | `105s + 6.0e-7 · D_r` |

Total work is the sum over regions; span is the longest single branch, since
branches are independent:

```
W = Σ_r [ 8.8·J_r + 3.3e-6·D_r + 120·P + 6.4e-7·P·D_r ]

S = max_r [ 113 + 1.9e-6·D_r ]        (for J_r large enough to hide the chunk term)

C* = W / S,   capped at the width ceiling  max(Σ_r J_r, 2·K·P)
```

Checked against the two measured workflows:

| | W predicted | W measured | S predicted | S measured | C* | given |
|---|---|---|---|---|---|---|
| Q1 (K=2, P=3, D_max=2.7e8) | 2516s | 2239s | 635s | ~640s | **4.0** | 7 |
| Q3 (K=3, P=5, D_max=1.1e7) | 1910s | 1886s | 134s | ~147s | **14.3** | 7 |

The structure matters more than the constants. `W` is dominated by `120·K·P` —
the fixed cost of the analysis tasks, which is large and nearly independent of
region size, because `frequency` processes every sample column regardless of how
few variants a region has. `S` is set by the single largest region. So:

> **C\* ≈ K·P when regions are small, falling as the largest region grows.**

Q3 has `K·P = 15` and small regions, giving `C* ≈ 14`. Q1 has `K·P = 6` but a
region 25× larger, and its span pulls `C*` down to 4. That is the entire
difference between the two allocations, and both are computable from `K`, `P` and
estimated variant counts before any data is downloaded.

Two consequences worth stating. A workflow of many small regions wants capacity
proportional to its analysis width and is cheap to speed up. A workflow with one
dominant region cannot be made much faster at any capacity, and the return on
attacking `individuals_merge` and the `frequency` fixed cost is far higher than
the return on any allocation decision.

### 8.1 Why W/S is the answer, and what cost to measure

Cost is allocated capacity times wall time — vCPU-seconds, the quantity a cloud
bill is proportional to. It is the measure to use precisely because it does not
depend on internal task accounting: task-seconds are inflated by contention
without more work being done, so a slower task appears to "consume" more, which
makes them a poor cost proxy. What you pay for is what you reserved, for as long
as you held it.

With `T(C) = max(W/C, S)`, cost `= C · T(C)` has two regimes:

```
C ≤ W/S   (work-bound)   T = W/C    cost = C · W/C = W      constant
C > W/S   (span-bound)   T = S      cost = C · S            linear in C
```

Below the knee the total cost is `W` no matter how few slots are used — fewer
slots simply take longer. Above it the cost rises with no time benefit. So
`C* = W/S` is the unique capacity that is simultaneously the cheapest allocation
achieving the minimum makespan and the largest allocation still costing only `W`.
That is the structural reason to allocate there, independent of the coefficients.

| | `C*` | cost at `C*` | cost at C=7 | makespan at C=7 |
|---|---|---|---|---|
| Q1 | 3.5 | 2,239 | 4,480 (2×) | 640s, no gain |
| Q3 | 12.8 | 1,886 | 1,886 (same) | 269s against 147s |

The asymmetry is what policy should exploit: **under-provisioning is free in money
and costs only time, which degrades gracefully as `W/C`; over-provisioning costs
money for nothing.** Contention only sharpens this, since `W` grows with `C`, so
the real optimum sits at or below `W/S`. When the estimate is uncertain — and at
plan time, on estimated variant counts, it will be — the cheap error is
downward.

## 9. Open

- **The time model does not exist yet.** The coefficients in §5.2 are the work
  this proposal depends on, and they are measurable with the fixtures §8 already
  requires.
- **The merge is the target, not the constraint.** Span is not a fixed property
  of the problem. Parallelising or eliminating `individuals_merge` would collapse
  Q1's span and raise its optimal capacity sharply. Capacity policy and that
  stage are the same question, and this review treats the merge as given only
  because it currently is.
- **The evidence is one host, and contention is the main reason that matters.**
  Two workflows on a single 16-core machine with one shared filesystem. The
  structural claims — branch independence, the width ceiling at 2KP, span as the
  binding constraint on Q1 — are properties of the DAG and carry over. The
  coefficients do not, and neither does the §4.1 inflation: at 15 slots this host
  is CPU-saturated and every task shares one disk, so the 29–243% per-stage
  inflation is partly an artefact of the deployment rather than of the workload.
  A cluster would reduce the CPU component and may worsen the storage component
  if the volume is network-backed, as the paper's was. Until the same sweep runs
  on a multi-node cluster, the capacity numbers in §8 should be read as the shape
  of the trade-off rather than as its magnitude.
- **`min_work` still binds and is still a guess.** Unchanged from §12, and now
  with a replicated cost: it selected J=38 where J=20 is about 7% faster, with
  two runs at each setting and non-overlapping ranges. That ordering is settled
  for this workload on this host; whether the optimum sits at 20 for other
  workloads, or on a cluster, is not.
