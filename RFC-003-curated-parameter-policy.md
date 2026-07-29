# RFC-003: Curated Parameter Policy for the Workflow Composer

| Field | Value |
|-------|-------|
| **Status** | Draft |
| **Created** | 2026-07-29 |
| **Depends on** | RFC-002 §5 (Adaptive Parallelism) |
| **Affects** | `workflow-composer/.../skills/`, `core/planner.py`, `core/generator.py`, `tests/integration/run-research-tests.sh` |

---

## 1. Problem Statement

The composer must choose execution parameters — today `ind_jobs`, later others —
from a natural-language research question. Some of that choice is domain
judgment, some is arithmetic with a safety limit. The two need different homes.

### 1.1 Current state: two rules that disagree

There are two independent parallelism rules in the tree, and they do not agree.

| | `core/planner.py:calculate_ind_jobs` | RFC-002 §5 `compute_adaptive_parallelism` |
|---|---|---|
| Used by | composer (`plan_workflow`) | test harness (`--vcpus`) |
| Keyed on | region span in base pairs | variant count + vCPUs |
| Output | preset 10 / 50 / 250 | `clamp(V/25k, 1.5·vcpu, 5·vcpu)` |
| Machine aware | no | yes |
| Memory aware | no | no |

Neither is reachable from the other. A run can therefore record one value in
`plan.json` and execute a different one: the `eur-afr-hla` plan recorded
`ind_jobs=50` while the harness ran `10`. Nothing surfaced the discrepancy.

### 1.2 Why both are miscalibrated

Both rules were tuned against a cost model that no longer holds. `individuals.py`
now streams its input, which changed the two quantities the rules assume:

| Property | Before | Now |
|---|---|---|
| Peak memory per task | ~4.7 GB, independent of `ind_jobs` | ~18–34 MB, scales with chunk |
| Task duration | hours | ~10 s (fill loop ~3 s) |
| Binding constraint | RAM | CPU cores |

Consequences. `planner.py` returns a fixed count regardless of machine size.
RFC-002 §5 targets 25,000 variants per task for "1–2 min per task", which is now
~10 s, and permits `5 × vcpus` tasks — 80 on a 16-core host — with no memory
term at all. Under the previous implementation that setting would have needed
~376 GB. It was never exercised only because the composer did not call it.

The lesson generalises: a numeric rule silently outlives the measurements that
justified it. This RFC is as much about keeping rules honest as about the
formula.

---

## 2. Design Principle: split by failure mode

The question is not "skill or code". It is **what a wrong answer costs, and
whether anyone notices.**

| | Region / population mapping | Parallelism |
|---|---|---|
| Wrong answer produces | wrong biology in the output | OOM, or a multi-hour run |
| Detectable by | inspecting the result | only after the damage |
| Recovery | re-run, minutes | crashed host |
| **Home** | **skill (prose)** | **code (enforced)** |

Prose is right for decisions whose errors can be caught downstream. Code is right
for decisions whose errors are destructive before anyone can look.

Evidence for both halves already exists. The prose skills work: the composer maps
"European and African populations in the HLA region" to `EUR`/`AFR` and
`chr6:28477797-33448354` unaided. And putting a rule in code is not sufficient
for correctness — `calculate_ind_jobs` was in code and was wrong for months.
Code buys consistency and testability, not correctness.

### 2.1 Detectability is earned, not intrinsic

The criterion above is only as good as the checks that back it. This project has
direct evidence: extraction produced genotype VCFs without rs IDs, every
downstream chart came out empty, and the integration harness reported PASSED
because it asserted only that output files exist. The defect survived months of
runs. "Visible in the result" was false in practice.

So the split obliges a second commitment. Anything assigned to prose must have a
downstream check that actually fails when the answer is wrong — here, asserting
that `mutation_index_array` is not uniformly empty, not merely that a tarball was
produced. Without that check, prose guidance is not a safe home for anything; the
distinction in §2 collapses and every decision migrates to code.

---

## 3. Architecture: policy in the skill, mechanism in code

Split the rule from the numbers it is parameterised by.

### 3.1 The skill owns policy

`SKILL.md` states the intent and the tunables in terms a domain curator can own,
together with the reason each number exists:

> **Choosing individuals parallelism.** Aim to keep every available core busy.
> Give each task at least ~10,000 variants for a cohort of ~1,000 individuals —
> a task carries a few seconds of fixed cost (container start, input scan, output
> compression) that smaller chunks cannot amortise. The floor scales with cohort
> size, because cost follows variants × individuals rather than variants alone.
> Keep each task under ~512 MB of memory. Call `recommend_parallelism` with the
> variant count and the target environment; do not compute the value by hand.

Nobody outside the team can meaningfully validate a clamp expression, so the
formula stays hidden. But "curated by non-experts" hides an ambiguity worth
making explicit: **the policy has two audiences, and they are different people.**

| Policy | Example | Owner |
|---|---|---|
| Domain | "autoimmune disease → HLA"; which populations a question implies | genomics curator |
| Resource | memory budget per task, vCPUs, minimum work per task | whoever knows the target machine |

A genomics curator cannot sensibly choose 512 MB; that requires knowing the
host's RAM and how many tasks run concurrently. Keeping both kinds of policy in
one undifferentiated `SKILL.md` invites each audience to edit numbers it has no
basis for. Separate them — domain policy in the skill files, resource policy in a
per-environment config the skill *refers to* — or at minimum label each block
with its owner.

Each policy number carries its justification. When the cost model shifts again, a
rule that states *why* visibly stops matching reality; a bare `50` never does.

### 3.2 Code owns mechanism

A single tool, callable by the composer and by the harness, replacing both
current rules:

```python
def recommend_parallelism(
    variants: int,            # V — actual row_count, not bp span
    individuals: int,         # I — after population filtering
    vcpus: int,
    host_mem_mb: int,
    chromosomes: int = 1,     # concurrency is global, ind_jobs is per chromosome
) -> Parallelism:             # (ind_jobs, max_parallelism, est_peak_mb, reason)
```

It returns both dials and the estimate behind them, so the caller cannot set one
without the other.

### 3.3 Trust but clamp

Whatever value arrives — agent-proposed, preset, or CLI flag — the generator
treats it as a hint and clamps it to the computed safe range. An agent that
ignores the guidance costs throughput; it cannot cost a host.

This is what neutralises the central risk of prose guidance. Strict adherence
stops mattering once violations are structurally impossible.

---

## 4. The parallelism rule

### 4.1 Cost model (measured, HLA region, I = 1153)

```
peak_RSS ≈ 12 MB + 1.2 MB × (V_chunk/1000) × (I/1000)
```

Predicts 35 MB for a 16.6k-variant chunk; measured 34 MB. Conservative on sparse
chunks (predicts 35, measured 18), which is the right bias for a safety limit.
The fraction of rows passing the AF/allele filter ranges 2–10% across chunks.

Task wall time decomposes into a constant ~3–4 s fill loop plus input scan and
output compression, the latter two varying with chunk offset and output size
(measured 7.7 s to 36.3 s per chunk).

### 4.2 Two dials, not one

`ind_jobs` sets how many tasks **exist**. `MAX_PARALLELISM`
(`HF_VAR_REDIS_CMD_MAX_PARALLELISM`) sets how many run **at once**. Memory is
consumed by concurrent tasks only, so the two must be solved together:

```
concurrency = min(ind_jobs, MAX_PARALLELISM)
peak_total  = concurrency × peak_RSS(rows_per_task, I)   ≤   host_mem_budget
```

Treating them separately is what makes a rule look safe and behave otherwise.
RFC-002 §5 sizes `ind_jobs` against vCPUs with no concurrency or memory term;
on a 16-core host it admits 80 tasks, which under the previous implementation
would have required ~376 GB.

### 4.3 Formula

```
work_per_task = clamp(V·I / C, min_work, max_work)     # work = rows × individuals
rows_per_task = work_per_task / I
ind_jobs      = ceil(V / rows_per_task)
est_peak_mb   = 12 + 1.2 × work_per_task / 1e6         # §4.1, inverted below
concurrency   = min(ind_jobs, C, floor((host_mem_mb - reserve) / est_peak_mb))

C        = vcpus - engine_reserve                      # engine + redis + merge
max_work = (mem_budget_mb - 12) × 1e6 / 1.2            # ≈ 4.2e8 at 512 MB
min_work = 1e7                                         # ~3 s of fill; see §8
```

Work is `rows × individuals` rather than rows alone: a task over 10,000 variants
costs twice as much with 2504 individuals as with 1153. A row-only minimum
mis-sizes tasks whenever the population subset differs from the reference.

Note the units on `max_work`: §4.1 charges 1.2 MB per 10⁶ row·individuals, so
inverting it multiplies by 10⁶ rather than dividing by 1.2 × 10⁻³. Getting this
wrong caps a task at a few hundred rows and inflates `ind_jobs` by three orders
of magnitude — the unit test in §6 should pin the inversion against §4.1
directly.

`concurrency` is capped by cores as well as memory. Memory alone would permit
tens of concurrent tasks on this workload, which oversubscribes a 16-core host
for no gain.

The rule switches automatically from core-bound on small regions to memory-bound
on large ones — the property fixed presets cannot express.

### 4.4 Worked examples

Assuming `mem_budget_mb = 512`, a 31 GB host, and `engine_reserve = 1`.

| Workload | V | I | vcpus | Binding | `ind_jobs` | concurrency | est. peak/task |
|---|---|---|---|---|---|---|---|
| HLA region | 166k | 1153 | 16 | cores | **15** | 15 | 27 MB |
| Whole chr1 | 6.2M | 2504 | 16 | memory | **38** | 15 | 512 MB |
| HLA region | 166k | 1153 | 64 | `min_work` | **20** | 20 | 24 MB |

The third row is the interesting one: 64 cores but only 20 tasks, because
`min_work` refuses to slice the region thinner than a task's fixed cost. Cores
sit idle by design — the alternative is 60 tasks that each spend more time
starting than working. It also shows `min_work` is the binding constraint on
small regions with large hosts, so §8's note that its value is a guess rather
than a measurement matters more than it first appears.

### 4.5 Multi-chromosome runs

`ind_jobs` is per chromosome, but `MAX_PARALLELISM` is global. Concurrency must
be divided across the chromosomes in flight, not applied to each independently —
otherwise a five-chromosome plan silently runs five times the intended tasks.

---

## 5. Observability

Record the effective value and its reason wherever a workflow is planned or
generated:

```
ind_jobs=15 max_parallelism=15 (core-bound; V=166,052 I=1153 C=15 est_peak=27MB/task)
```

Both dials appear together. Reporting one without the other reproduces the
failure in §4.2, where a safe-looking task count hides an unsafe concurrency.

`plan.json` should carry the same fields. The `50` vs `10` divergence persisted
because no artefact ever stated the value actually used. This matters more than
where the rule lives.

---

## 6. Validation

Three layers: one per home, plus one guarding the measurements both rest on.

**Mechanism — unit tests.** Invariants on the tool and on chunking: exactly
`ind_jobs` tasks, contiguous ranges, full coverage, estimated peak under budget,
`concurrency ≤ C`, and that no input can produce a value exceeding the memory
clamp. Include a round-trip check that `max_work` inverts §4.1 exactly — that
inversion is where a units slip is both easy and expensive.

**Policy — evaluation harness.** `evaluation/experiments` already runs intent
accuracy and skill-ablation studies. Add parallelism guidance to the ablation
set and measure whether the agent supplies correct inputs and calls the tool
rather than inventing a number. This converts "the agent might not follow prose"
from a hypothetical into a tracked metric.

**Calibration — a test that fails when the cost model moves.** The constants in
§4.1 are fitted to measurements taken on one implementation. Documenting the
reasoning helps a reader, but the previous rule rotted for months precisely
because no one read it. Add a test that runs `individuals.py` on a small fixture,
measures peak RSS and fill time, and asserts the §4.1 model predicts them within
a tolerance. When someone changes the script's cost profile again, that test
fails and names the constant to re-fit. This is the only mechanism here that
detects rot without a human noticing.

---

## 7. Implementation sketch

1. Add `recommend_parallelism` with the formula in §4.3, returning both dials,
   the memory estimate, and the reason.
2. Point `calculate_ind_jobs` at it; keep `small`/`medium`/`large` as memory-
   budget bundles rather than absolute task counts. They must not bundle `vcpus`
   or `C`: those describe the machine, which §3.1 assigns to the environment
   config rather than to a named preset.
3. Have `run-research-tests.sh --vcpus` call the same tool; delete the duplicate
   in RFC-002 §5.
4. Clamp in `generate_workflow`, independent of caller.
5. Emit the reason string into logs and `plan.json`.
6. Write the guidance section in `SKILL.md`, numbers and justifications together.

---

## 8. Non-goals and open questions

**Non-goals.** Tuning the analysis stages (`mutation_overlap`, `frequency`);
changing the extraction path; per-task autoscaling at runtime.

**Open questions.**

- **The pass fraction is the weak point of the memory bound.** The 1.2 MB
  constant folds in a filter pass rate of 0.1, the *highest* observed across HLA
  chunks (range 2–10%). Memory scales linearly with it, so a region whose common
  variants push the rate to 0.3 would use three times the predicted amount, and
  the bound fails in the unsafe direction. The measured range comes from one
  region and one population subset. Options: fit against several regions
  including a common-variant focus; carry an explicit safety factor; or measure
  the first chunk and size the rest from the observed value.
- **Two memory budgets, set independently.** `mem_budget_mb` caps a single task
  and `host_mem_mb` caps all concurrent tasks, but nothing keeps them consistent:
  512 MB per task with 15 concurrent needs 7.7 GB, which the host budget may or
  may not allow, and a curator editing one has no signal about the other. The
  clean formulation is a single host budget with the per-task share derived from
  concurrency — but concurrency depends on per-task size, so it is circular.
  Either iterate to a fixed point, or keep both and add a consistency check that
  refuses contradictory settings. Left unresolved here.
- Where do `vcpus`, `host_mem_mb`, and `MAX_PARALLELISM` come from for a remote
  target? Detected, declared in the plan, or supplied per compute environment?
- Clamping bounds the *parameter*, not the *scope*. An agent that selects seven
  populations genome-wide gets a safely-sized `ind_jobs` for a job that should
  not run at all; that remains the job of the volume auto-stop in RFC-002 §6.
  Worth confirming the two interact sensibly.
- `min_work` is set so a task's useful work roughly matches its fixed cost. That
  ratio is a guess, not a measurement — worth deriving from the observed
  overhead once the calibration test in §6 exists.
- Input scanning is `O(ind_jobs²)` in aggregate, since each task skips to its
  offset. Harmless at `ind_jobs ≈ 15`; a byte-offset index would be needed if
  whole-genome runs push the value into the hundreds.
