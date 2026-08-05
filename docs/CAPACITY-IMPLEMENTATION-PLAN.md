# Implementation plan: capacity planning from scientific intent

Scope: `workflow-composer/`, `knowledge/policy/`, `workflow-conductor/`,
`engines/hyperflow/harness/`.
Based on: `CAPACITY-PLANNING-NOTE.md`, `RFC-006-REVIEW.md` §8.
Depends on: RFC-006 (three representations, C1–C4).

---

## 1. Goal

PLAN returns a recommended capacity to allocate, derived deterministically from
scientific intent. Task sizing follows that allocation instead of manufacturing
tasks to fill whatever capacity it was handed.

The change is one of direction. Today `recommend_parallelism` takes `vcpus` as an
input and computes `raw_work = V*I/C`, so the task count is a function of the
machine. After this change the workflow's own structure produces a capacity
figure, and the task count is a function of that.

### Success criteria

1. `plan_workflow` returns a capacity recommendation with its work, span, regime
   and reasoning, computed before extraction.
2. The recommendation is measurably better than the current allocation on both
   shapes of workload: Q1 (one dominant region) and Q3 (many small regions).
3. Capacity binds somewhere real — the harness concurrency dial, and the
   Kubernetes `ResourceQuota`.
4. Resource policy carries no prompt-delivered prose: coefficients live in
   configuration, rationale lives next to the code, and neither duplicates the
   other.

---

## 2. Design

### 2.1 The model

For each region `r`, with `D_r = V_r × I` (variants × individuals) and `J_r`
individuals tasks:

```
work_r = a_ind·J_r + b_ind·D_r
       + b_merge·D_r + c_merge·J_r
       + P·(a_mo + b_mo·D_r)
       + P·(a_fr + b_fr·D_r)

span_r = (a_ind + b_ind·D_r/J_r) + (b_merge·D_r + c_merge·J_r)
       + max(a_mo + b_mo·D_r, a_fr + b_fr·D_r)

W = Σ_r work_r          S = max_r span_r
```

`sifting` is omitted from both: it costs about 1s and runs concurrently with
`individuals`, so it enters neither the sum nor the longest path materially.

**`C* = W/S` was wrong, and measurement caught it.** A stage that is serial —
`individuals_merge`, whose `W/S` is 1.0 — drags the whole-workflow ratio down
and hides that another stage could use far more. Measured on Q1 the global ratio
is 6.3 while the individuals stage alone reaches 12.3, and the run at the
capacity the global ratio implied took 1032s against 785s at 7 slots. Work and
span are therefore accumulated per stage:

```
makespan(C) = Σ_stages max(W_i/C, S_i)        floor = Σ_stages S_i
C*          = smallest C with makespan(C) <= floor × (1 + knee_tolerance)
```

The obvious alternative — the capacity at which every stage is span-bound,
`max_i(W_i/S_i)` — degenerates: a stage of N equal tasks has `W_i/S_i = N`, so
that maximum is just the widest stage. For Q1 it reads 28, buying 3% of makespan
over 6 slots for nearly five times the slot-seconds.

`knee_tolerance` is a policy number, not a fitted coefficient — the same
category as the old `min_work`, and stated in `PerformanceModel` rather than
buried in a comparison. At 0.10 the Q1 recommendation is 9 slots against a
measured optimum of 7; 0.05 would give 12 and 0.20 would give 5.

Validated against measured Q1 runs: predicts 887/760/700s against 1032/785/802s
at 4/7/15 slots, so within 3% at the optimum and under-predicting at both
extremes, where it models neither contention nor scheduling stalls.

### 2.1.1 Resolving the order — `J` first, then `C`

`span_r` depends on `J_r`, so `C*` cannot be computed before `J` is known. `J`
must therefore not be derived from `C`, or the definition is circular.

It need not be. `J` sits on a trade-off contained entirely within the span:
finer chunks shrink `b_ind·D_r/J_r` and grow `c_merge·J_r`. Differentiating and
solving gives a capacity-independent optimum:

```
J*_r = sqrt(b_ind · D_r / c_merge)          clamped to [1, memory ceiling]
```

For chr6 this is `sqrt(2.0e-6 × 2.745e8 / 0.8) ≈ 26`, against a measured optimum
of 20 and the current policy's 38. The model lands near the measured best without
being told about it, which is the strongest independent check the coefficients
have so far.

The computation is therefore acyclic:

```
J*_r        from the span trade-off, per region
W_i, S_i    per stage, evaluated at J*
C*          smallest C with Σ_i max(W_i/C, S_i) <= (Σ_i S_i)(1 + tolerance)
```

This also retires `min_work`. Its role was to stop tasks becoming too small to
amortise their fixed cost, which is exactly what the `c_merge·J` term now
expresses with a measured coefficient rather than a guess.

Summing work while taking the maximum of spans carries the multi-region case, and
it does so per stage as well as overall. Regions are independent branches, so
their work adds while their time does not: adding a region raises every `W_i` and
leaves the `S_i` alone, and `C*` therefore grows with the number of regions. This
is measured, not assumed — extraction completes every region's VCF before
execution starts, and Q3's three branches were observed starting together at t=0
and overlapping throughout.

Asymmetric regions need no special handling. Q1's chr17 contributes about 400s to
`W` while chr6 sets `S`, and most of that 400s is the `P × (a_mo + a_fr)` fixed
analysis cost every region pays regardless of size.

No separate width ceiling is needed, because the tolerance search already stops
at the widest stage. Any schedule achieving the floor uses at most `max_width`
slots at any instant, and the search never proposes more than that.

Starting coefficients, fitted in `RFC-006-REVIEW.md` §8 and to be replaced by
the calibration in §6:

| coefficient | value | meaning |
|---|---|---|
| `a_ind` | 8 s | fixed cost per individuals task |
| `b_ind` | 2.0e-6 s | per variant × individual |
| `b_merge` | 1.3e-6 s | merge, per variant × individual |
| `c_merge` | 0.8 s | merge, per archive |
| `a_mo`, `b_mo` | 15 s, 3.5e-8 s | mutation_overlap |
| `a_fr`, `b_fr` | 105 s, 6.0e-7 s | frequency |

### 2.2 Why the two regimes matter

Cost is `C · makespan(C)`, equivalently `Σ_i max(W_i, C·S_i)`. Where every stage
is work-bound this is `W`, constant — spend longer on fewer slots for the same
money. Where a stage is span-bound it contributes `C·S_i`, rising with `C`.

The earlier claim that cost is flat below `C*` assumed the first case
everywhere. It does not hold for Q1: the merge is span-bound from `C = 1`, so
cost climbs across the whole range. Measured, C=4 cost 4,128 slot-seconds and
C=7 cost 5,495 — cheaper below the knee, but not free.

What survives is the direction. Under-provisioning trades time for money and
degrades gracefully; over-provisioning buys neither, and past the knee buys
nothing at all. The estimate runs on pre-extraction variant counts, so it will be
wrong; that asymmetry still says round down.

### 2.3 Task sizing stops following the machine

`recommend_parallelism`'s `raw_work = V*I/C` term is removed: it is the term that
makes the task count a function of the machine. `J_r` becomes `J*_r` from
§2.1.1, clamped to the per-task memory ceiling, which is unchanged and still
enforced.

Both dials are then properties of the workflow rather than of the host. `C` says
how much to allocate; `J` says how to divide the work; neither is derived from
the other.

Strictly, `J` does interact with the objective, because `W` rises monotonically
with `J` while `S` is U-shaped. Minimising cost alone would push `J` to 1. The
plan does not do that, for a measured reason: across chr6 the whole range of `J`
moves `W` by about 12% while the allocation choice moves cost by 2×, and `J = 1`
costs 75% in wall time to buy it. So `J*` is used under either objective and the
objective selects `C` alone. This is a decision, not an omission — §8 records it.

This is the substantive behavioural change and needs its own decision record: the
current one-sided clamp is retained or replaced per RFC-006 §2's corollary.

### 2.4 When `C*` exceeds what is available

The common case on a fixed cluster. The plan's recommendation is a request, not
an assumption: RESOLVE sizes tasks to the capacity actually obtained, and
`T = max(W/C, S)` says the run degrades gracefully rather than failing — at
`C < C*` the workflow is work-bound, so it takes `W/C` and still costs `W`.

What must not happen is silent divergence. The recommendation, the capacity
actually granted, and the resulting predicted makespan are all recorded (D4), and
a granted capacity below the recommendation is reported with its predicted time
cost so a user can decide whether to wait or to provision more.

---

## 3. Workstreams

### A. Deterministic model — `workflow-composer`

| # | Change | Files |
|---|---|---|
| A1 | New `Capacity` dataclass and `recommend_capacity()`; pure function of estimates plus coefficients. Includes `J*` per §2.1.1 | `core/capacity.py` (new) |
| A2 | Per-region variant estimates exposed for the model; today `_estimate_max_variants_per_chromosome` returns only the maximum | `core/planner.py:124` |
| A3 | Coefficients in versioned configuration, loaded like `ComputeEnvironment` | `core/performance_model.py` or extend `core/environment.py` |
| A4 | Remove `raw_work = V*I/C`; `J` becomes `J*` from the span trade-off | `core/parallelism.py:170` |
| A5 | Property tests: `C*` never exceeds the DAG's maximum width, which §2.1 proves must hold and so is a check on the implementation rather than a clamp; `J*` minimises predicted span; adding a region raises `C*` and leaves `S` unchanged; the two-regime cost formula matches `C·max(W/C,S)` | `tests/test_capacity.py` (new) |

### B. Plan carries the recommendation

| # | Change | Files |
|---|---|---|
| B1 | `CapacityRecommendation` model — `slots`, `slots_exact`, `work_seconds`, `span_seconds`, `span_region`, per-region `ind_jobs` (`J*`), `model_version`, `reason`. Deliberately no `vcpus`: the recommendation is in slots, and the conversion belongs at the binding point (§4.2) | `core/models.py:88` |
| B2 | `WorkflowPlan.capacity` field; populated by `plan_workflow` | `core/models.py:113`, `core/planner.py:502` |
| B3 | `objective` parameter — `minimize_time` or `minimize_cost` — plus optional `deadline_seconds` | `core/planner.py:502` |
| B4 | CLI surfaces capacity in `plan` and `compose` output | `cli.py:291` |
| B5 | MCP `plan_workflow` returns capacity; schema gains `objective`/`deadline`, loses the memory preset | `mcp_server.py:97` |

### C. Policy knowledge

Resource policy ends up with **no prompt-delivered prose**. Applying RFC-006's
C3 — name the `file:line` that puts the document into the prompt of the stage
that makes the decision — there is no such stage: capacity is computed
deterministically from `K`, `P`, estimated variant counts and calibrated
coefficients. C1–C4 do not bind because nothing is prose-owned. The objective is
a user choice expressed through the interface, not knowledge an agent needs
taught.

What remains is documentation next to code, and configuration. Domain knowledge
is untouched and remains essential: the capacity model is entirely downstream of
populations, region coordinates and disease-to-gene mapping.

| # | Change | Files |
|---|---|---|
| C1 | Maintainer document — DAG shape, the two regimes, the cost structure, coefficients with evidence and invalidation conditions. For humans reading the code, not for a prompt | `core/capacity.py` docstring + `docs/capacity-model.md` (new) |
| C2 | Delete. `min_work` guidance and "keep every available core busy" are obsolete once `J` is `J*` and capacity is derived | `knowledge/policy/individuals-parallelism.md` |
| C3 | Reduce to ownership and override semantics, or delete if configuration carries both | `knowledge/policy/resource-policy.md` |
| C4 | Tool schema gains `objective` and `deadline_seconds`, loses the memory preset. This is tool instruction, not knowledge | `mcp_server.py:97` |
| C5 | Test that the maintainer document and the configuration agree on every coefficient | `tests/test_policy_consistency.py` (new) |

`knowledge/policy/` may persist as an ownership and provenance marker, but after
C2 and C3 nothing in it reaches an agent. That is a stronger conclusion than
RFC-006 §4.1 draws, which retains a twelve-line planner fragment about memory
presets — presets that leave the planner's inputs entirely under this design.

### D. Consumption

| # | Change | Files |
|---|---|---|
| D1 | Harness: derive `MAX_PARALLELISM` from the plan's capacity rather than the hardcoded `--vcpus 8` fallback | `harness/run-research-tests.sh:631-640` |
| D2 | Conductor: `resource_quota_cpu` from the plan instead of a static setting — this is where capacity binds in Kubernetes | `phases/provisioning.py:104-111`, `config.py` |
| D3 | Conductor: forward `state.infrastructure.available_vcpus` into `generate_workflow`, closing the gap RFC-006 §1.1 documents | `phases/generation.py:171-180` |
| D4 | Record requested, recommended and effective capacity in plan and run artefacts | `core/planner.py`, `reporting.py` |

D2 is the smallest change with the largest effect: the chart already enforces
`resourceQuota.hard.requests.cpu` and worker pods already carry CPU requests, so
the quota throttles concurrency exactly as an allocation would.

---

## 4. End-to-end validation

### 4.1 Docker harness first

Cheapest and already proven. Q1 and Q3 are extracted, so each point is an
`--execute-only` run of 3–14 minutes. For each workload, run at the recommended
capacity, half it, and double it, recording makespan, slot-seconds, task-seconds
and utilisation — the columns in `RFC-006-REVIEW.md` §4.1.

Prediction to falsify: at `C*` the makespan is within noise of the best observed,
and slot-seconds are minimal among settings achieving that makespan. Q1 should
prefer roughly 4 slots and Q3 roughly 13.

Two replicates per point. The measured spread is 1.6–4.7%, so a single run cannot
separate settings closer than about 5%.

### 4.2 Kind cluster

Feasible, with one caveat to state plainly: a Kind node reports the *host's* CPU
count, so a three-node cluster on this 16-core machine claims 48 vCPUs that do
not exist. Node count therefore cannot be used to vary capacity honestly.

The `ResourceQuota` can. It is the real binding point in Kubernetes, it is what
D2 wires, and it is enforced by the scheduler against the CPU requests worker
pods already carry. So: one fixed Kind cluster, capacity varied by quota.

Both workloads' recommended capacities — about 4 and 13 slots — fit inside 16
real cores, so the experiment is honest on this host. A capacity above the host's
core count could not be tested here and should not be claimed.

| step | action |
|---|---|
| 1 | Create the Kind cluster; install `hf-ops` and `hf-run` from `~/hyperflow-k8s-deployment` |
| 2 | Set `ResourceQuota.hard.requests.cpu` to `C × cpu_per_task`. With the chart's 0.5 CPU request per worker, 13 slots is a quota of 6.5 — the plan's capacity and the quota's number are not the same figure, and D2 must convert rather than assign |
| 3 | Run Q1 and Q3 at `C*`, `C*/2`, `2·C*` |
| 4 | Compare makespan and quota-seconds against the harness results for the same capacities |

Prerequisites already verified: `kind`, `helm` and `kubectl` are installed, the
charts are present, the conductor's dependencies install cleanly, and the pinned
composer image exists locally. Two known obstacles — `~/.kube` is not writable
under the sandbox (use `kind get kubeconfig`), and the conductor needs
`HF_CONDUCTOR_HYPERFLOW_K8S_DEPLOYMENT_PATH` set explicitly.

Running the full conductor additionally needs an LLM API key. If none is
available, D2 can still be validated by driving Helm and the quota directly,
which tests the binding point without the planning agent.

---

## 5. Sequence

Wiring before enforcement, per RFC-006 §10.

| Milestone | Contents | Gate | Status |
|---|---|---|---|
| M1 | A1–A3, B1–B2: capacity computed and returned, nothing consumes it | Property tests pass; Q1 and Q3 predictions match `RFC-006-REVIEW.md` §8 | **done** — `b3eadcf`, `4cbbe93`. Suite 414→485 passed, 0 regressions; Q1 4 slots, Q3 14; `core/parallelism.py` untouched |
| M2 | §6 calibration: re-fit the coefficients into the configuration M1 built | Predicted `W` and `S` within 20% on all three queries | next |
| M3 | D1 + §4.1 harness validation | `C*` is within noise of the best makespan at minimal slot-seconds | — |
| M4 | A4: `J` becomes `J*` | No regression in M3; memory invariants hold | — |
| M5 | C1–C5: policy documents removed, coefficients-agree test added | No `knowledge/policy/` document reaches any prompt; documentation and configuration agree | — |
| M6 | D2–D4 + §4.2 Kind validation | Quota-bound run reproduces the harness result | — |

M1–M3 are independently useful: they produce a number and evidence that it is
right, without changing any existing behaviour. M4 is the first change that can
regress a working run.

---

## 6. Calibration

The coefficients in §2.1 are fitted from a handful of runs on one host and are
the weakest part of the proposal.

The data currently on disk is thinner than it first appears. Each
`--execute-only` run overwrites `logs-hf`, so per-task detail survives for only
three configurations — Q1 at `J=20`, Q2, and Q3 — while the other Q1 settings
survive only as per-stage aggregates in the sweep results. Fitting fixed and
per-`D` terms from aggregates is possible but gives no per-task variance and only
one Q1 point at task granularity. Re-running the four Q1 settings with logs
preserved costs about an hour and is worth it if these coefficients are going to
carry the design. Do that first, then:

- fit each stage's fixed and per-`D` terms per region and per configuration;
- record host, worker image, date and observed range, per RFC-006 §4.2;
- add the calibration test RFC-006 §8 already requires, extended to duration —
  had it existed, the individuals optimization of 2026-07-29 would have failed it;
- state the invalidation conditions, including any change to the individuals
  worker, to `frequency`'s per-task fixed cost, or to the storage architecture.

Contention is the known confound. On this host, 15 concurrent tasks inflate
per-task time by 29–243% while the serial merge is unaffected, so a single set of
coefficients cannot describe both a lightly and a heavily loaded machine. Two
options, and the choice should be recorded: fit at low concurrency and accept
under-prediction when loaded, or add a concurrency term. The first is simpler and
biases toward requesting less capacity, which §2.2 says is the cheap error.

---

## 7. Risks

**The model is fitted on one host with one shared filesystem.** The structure —
branch independence, work summing while span takes the maximum, span set by the
largest region — is a property of the DAG and carries over. The coefficients are
not, and a network-backed volume would change the merge and the analysis stages
in opposite directions from what was measured here.

**A4 can regress throughput.** Replacing the core term changes `J` for every
existing workload, and `J*` disagrees with the current recommendation on every
region measured: chr6 26 against 38, chr7 5 against 2, chr17 3 against 1. M3's
measurements are the guard, and M4 is deliberately sequenced after them.

**Capacity is estimated before extraction**, and M1 measured what that costs.
Running Q1 through `plan_workflow` gives `W = 3172s, S = 931s` against
`W = 2424s, S = 676s` from the measured row counts — the estimator's safety
margin inflates `W` by 31% and `S` by 38%, more than the +6.4% and +17.6%
variant-count errors the harness reports, because both terms are products.
Both still round to 4 slots, so the recommendation survived here, but a
workload nearer a slot boundary would not. §2.2's asymmetry is the mitigation,
and the estimate should round down. M2 should report `C*` from estimated and
from measured counts side by side rather than only the former.

**The Conductor exercises a pinned composer image**, so D2–D3 do not test the
code in this tree unless the image is rebuilt or the server command overridden.

---

## 8. Open decisions

- **Objective default.** `minimize_time` gives `C*`; `minimize_cost` gives any
  `C ≤ C*`, all costing `W`, and needs a deadline to be well posed — without one
  it degenerates to `C = 1`. Which is the default when a user states neither?
- **The objective selects `C`, not `J`** (§2.3). Recorded as a decision because
  the measured trade-off supports it — `J` moves `W` by about 12% while `C` moves
  cost by 2× — but it is a simplification, and a deadline-constrained
  cost objective would in principle optimise both together.
- **One-sided clamp.** RFC-006 §2's corollary requires either two-sided
  enforcement or recomputing the estimate from the effective value. A4 is the
  natural place to settle it.
- **vCPUs versus slots — settled in M1.** The recommendation is in *slots*, and
  `CapacityRecommendation` deliberately carries no `vcpus` field. The chart
  requests 0.5 CPU per worker, so a quota in CPUs differs from a capacity in
  slots by that factor; putting the conversion at the binding point (D2) keeps
  one number meaning one thing. Restated here because the earlier text left it
  open.

- **Closed form now, DAG walk later.** §2.1's formula hardcodes the 1000genome
  stage structure, which is workflow-specific knowledge expressed as code. The
  general alternative is to compute `W` as the sum of predicted task durations
  and `S` as the longest path over the estimated workflow, which PLAN already
  produces — `workflow-estimated.json` carries the full dataflow graph, 273
  processes and 285 signals for Q3. That would leave per-task-type coefficients
  as the only workflow-specific input, making the model work for any DAG the
  composer can generate. The closed form is being implemented first because it is
  simpler to reason about and does not require generating the estimated workflow
  before capacity can be computed. Revisit once the coefficients are calibrated
  and M3 has validated the numbers.
