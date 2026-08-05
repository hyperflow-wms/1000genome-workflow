# RFC-006: Policy Knowledge — Placement, Delivery, and Enforcement

| Field | Value |
|---|---|
| **Status** | Draft |
| **Scope** | `workflow-composer/`, `workflow-conductor/`, engine backends |
| **Supersedes** | RFC-003 §2 (placement principle), RFC-003 §3.3 (one-sided clamp) |
| **Depends on** | RFC-004 (backend protocol), RFC-005 (chunk conventions) |

---

## 1. Problem

RFC-003 §2 places a decision by asking what a wrong answer costs and whether
anyone notices. That question is necessary and not sufficient. It says whether a
decision may be advisory, but not who receives the guidance, at which phase the
evidence to decide exists, or where the decision stops being reversible. Five
measurements show each gap producing a live defect.

### 1.1 Policy prose reaches no agent

`load_skill_context(include_policy=True)` and `load_skill_context(backend=…)`
have no production caller. The only caller of the loader is
`interpretation/llm_interpreter.py:81`, with defaults, which loads
`knowledge/domain/` alone.

```
$ grep -rn "load_skill_context" workflow-composer/src/
skill_loader.py:77      def load_skill_context(...)
llm_interpreter.py:81       skill_context = load_skill_context()
```

`knowledge/policy/` (114 lines) and `knowledge/backends/` (225 lines) — 339 of
562 knowledge lines — enter no prompt on any path in either repository. The MCP
server exposes them as resources, but the Conductor's `COMPOSER_INSTRUCTION`
(`phases/planning.py:27-39`) names only tools. The guidance that does reach the
planning agent reaches it through the `ind_jobs` tool-schema description
(`mcp_server.py:212`), which duplicates the document and disagrees with it.

### 1.2 The clamp guards the direction that was already safe

`clamp_ind_jobs` reduces a hint above the recommendation and respects one below
it (`backends/hyperflow/generator.py:158`). Per-task memory is inversely
proportional to `ind_jobs`, so the unclamped direction is the one that raises
memory. Measured on the HLA input (V=166,052, I=1153):

```
/usr/bin/time -v python3 worker-base-image/scripts/individuals.py \
    ALL.chr6.hla.vcf 6 0 <step> 166052
```

| effective `ind_jobs` | rows/task | measured peak RSS | recorded `est_peak_mb` | task wall time |
|---|---|---|---|---|
| 15 (recommendation) | 11,071 | 15.9 MB | 27 MB | 5.66 s |
| 4 | 41,513 | 33.9 MB | 27 MB | 46.26 s |
| 1 | 166,052 | 155.2 MB | 27 MB | 295.86 s |

In the memory-bound regime the effect is larger. For whole chromosome 1
(V=6.2M, I=2504) the recommendation is `ind_jobs=38`, sized to land at the
512 MB per-task budget; a hint of 8 is respected and gives 775,000 rows per
task, about 30× the budgeted work each.

The throughput cost is also larger than the task-count ratio implies: 15× fewer
tasks costs 52× in wall time, because per-task cost is super-linear in chunk
size. The fill loop is linear (2.68 → 9.20 → 39.24 s); archiving is not
(2.98 → 37.1 → 256.6 s).

### 1.3 The artefact records the recommendation, not the run

`generate` writes `recommended.max_parallelism` and `recommended.est_peak_mb`
into `metadata.parallelism` regardless of the effective task count
(`backends/hyperflow/generator.py:344-352`). At `ind_jobs=1` on HLA the recorded
estimate is 27 MB against a measured 155 MB, alongside a `max_parallelism` of 15
for a workflow holding one task, and the note `[throughput only, not safety]`.

On the Nextflow backend that number is not inert: it becomes `--task_mem`
(`backends/nextflow/params.py:183`), then the `memory` directive
(`engines/nextflow/nextflow.config:33`), which Docker enforces. The
configuration's own comment records the consequence of an understated value in
this tree: tasks killed with exit 137.

### 1.4 The oracles that exist do not fail

- Intent mismatch logs a warning and continues to extraction and execution
  (`engines/hyperflow/harness/run-research-tests.sh:441-445`). This is the
  direct oracle for the flagship prose-owned decision, and it discards its own
  verdict.
- `verify_outputs` checks existence and non-zero size
  (`harness/lib/test_framework.py:481-494`), and the harness increments `PASSED`
  in both branches — missing outputs are recorded as `PASSED (partial outputs)`
  (`run-research-tests.sh:790-799`).
- `compare_estimated_to_final` computes `variant_diff_pct` and asserts no bound,
  although that estimate governs auto-stop and the approval preview.
- `compare-results.sh:99-101` treats a file empty on both sides as consistent,
  so two engines failing identically report `EQUIVALENT`.
- No test measures peak RSS or task duration, so nothing detects the cost model
  drifting from the code it was fitted to.

### 1.5 Wrong knowledge is undetected; only missing knowledge is detected

Mutations applied to a copy of the tree, baseline `414 passed, 3 skipped`:

| mutation | result |
|---|---|
| delete `knowledge/` | 35 failed |
| every document replaced by one garbage line | 30 failed |
| HLA coordinates changed in `genomic-regions.md` | 414 passed |
| `EUR` → `ZZZ` throughout `domain/` | 414 passed |
| BRCA1 end coordinate changed, pinned start left intact | 414 passed |

The checks are string-presence probes over four anchors
(`tests/test_interpreter_isolation.py:140-145`) — one of eight coordinates and
one of 26 population codes. The coordinates are also duplicated in
`core/data_resolver.py:50` with nothing cross-checking the two copies.

---

## 2. Placement rule

> A decision belongs no earlier than the phase where the evidence to decide it
> exists, and no later than the last deterministic point before its
> consequences bind.
>
> It may be owned by prose only when all four hold:
>
> **C1 — Consequence.** A wrong answer costs a re-run, not a host and not a
> corrupt result that is consumed as if correct.
>
> **C2 — Detection.** Perturbing the answer in place, to a different wrong
> value, turns a check red. Deleting the document is not the test.
>
> **C3 — Delivery.** You can name the `file:line` that puts the document into
> the prompt of the stage that makes the decision. Exposing it as a readable
> resource does not satisfy this.
>
> **C4 — Enforcement.** You can name the `file:line` of the single deterministic
> function through which every resource-committing consequence passes before
> anything binds.
>
> **Corollary.** One-sided enforcement is admissible only where the guarded risk
> is monotone in the enforced direction. Otherwise enforce both sides, or
> recompute the estimate from the effective value and enforce against that.

The rule is refutable. A counterexample is a decision satisfying all four that
is nonetheless unsafe in production, or a decision for which the two bounds
select no phase at all.

Applied to the current tree: region and population mapping satisfies C1, C3 and
C4 and fails C2. Parallelism satisfies C1 and C2, fails C3, and fails the
corollary.

---

## 3. Three representations

Policy is one contract in three forms, each with one consumer and one way of
being checked.

| Form | Contains | Consumer | Kept honest by |
|---|---|---|---|
| Prose | objectives and their order, ownership, the evidence behind each number, what invalidates it, override semantics | a human maintainer, or an agent choosing among named alternatives | a test asserting it agrees with the configuration; a calibration test asserting the evidence still holds |
| Configuration | operative values: memory budget, reserves, minimum work, environment profiles | `recommend_parallelism` and the environment resolver | schema validation and resolution property tests |
| Executable invariant | feasible-range arithmetic, hard inequalities, binding-point checks | the resolver and each backend | unit and property tests |

A number appearing in prose is documentation of the configuration, never its
source. Directory membership under `knowledge/` records ownership; it does not
determine delivery. Delivery is declared per phase, in §5.

---

## 4. What policy prose contains

Two audiences, disjoint content.

### 4.1 Planner-facing — about twelve lines, carried in the tool schema

The planning agent's action space is four arguments: `parallelism` ∈
{small, medium, large}, `compute_environment` ∈ {local, aws, gcp}, a `vcpus`
override, and an optional `ind_jobs`. Guidance about anything outside that set
cannot change its output. What remains:

- which memory preset suits which host, and that the preset bounds one task
  rather than the host;
- name the target environment rather than guessing its numbers; override
  `vcpus` only against a known target;
- omit `ind_jobs` unless carrying an explicit user constraint, and state the
  constraint when carrying one;
- the failure mode, stated correctly: a value above the feasible maximum is
  reduced and costs nothing; a value below it gives each task proportionally
  more work than the memory budget allows, and on Nextflow exceeds the task's
  own declared memory limit.

This fragment lives in the tool description because that is the only channel the
planning agent cannot route around, which is what C3 requires. If the prompt
gains a guaranteed policy section, the fragment may live there instead —
generated from one source either way, never maintained twice.

### 4.2 Maintainer-facing — the evidence, next to the code it justifies

What a reader cannot recover from the source:

- the cost model with the measurements behind it, the host, the worker image,
  and the date; the observed range, not only the fitted constant; and the
  margin — the model over-predicts peak RSS by 1.6–2.1× at 11k, 41k and 166k
  rows;
- what invalidates the calibration: any change to the individuals worker's
  buffering or archiving, a region whose filter pass rate exceeds the ~10%
  assumed, a cohort far from 1153, or a change in container memory accounting;
- the kind of each number: `mem_budget_mb` a hard per-task ceiling, `min_work` a
  guess rather than a measurement, `engine_reserve` and `host_reserve_mb` tuning
  knobs, the `aws` and `gcp` profiles representative rather than measured;
- the two inconsistencies RFC-003 §8 leaves open: the pass-fraction assumption,
  and the per-task and host budgets being set independently.

### 4.3 Removed

Field tables, profile tables, preset lists, override precedence, and
descriptions of `check_budget_consistency` restate code, are held in step by
string-presence tests, and are read by nobody. "Keep every available core busy"
restates what the core term already does. "Call `recommend_parallelism`, do not
compute by hand" is tool instruction and belongs in the schema.

---

## 5. Phases

A phase boundary exists where evidence arrives or a value binds. Seven phases
and two gates follow; gates are checks that transfer control, not stages that
transform state, and the distinction is worth keeping.

The list is a dependency order over evidence, not a schedule. EXTRACT and
MEASURE are independent and may overlap; RESOLVE needs both.

| Phase | Produces | Consumes policy as |
|---|---|---|
| INTERPRET | `ResearchIntent`, no execution parameters | domain prose only |
| PLAN | scientific scope, typed constraints with a source | the planner fragment (§4.1) |
| *gate:* VALIDATE INTENT | accept or fail | executable invariant |
| EXTRACT | data on disk; V per chromosome, I after filtering | none |
| MEASURE | vCPUs, allocatable memory, backend reserve | none |
| RESOLVE | a self-consistent resolution object | configuration |
| MATERIALIZE | engine artefacts, values bound | executable invariant |
| EXECUTE | outputs and telemetry | none |
| *gate:* VALIDATE RESULTS | accept or fail | executable invariant |

### 5.1 Mapping to what exists

This is the part that keeps a third vocabulary from becoming a fourth
divergence. Every row names where the phase lives today, or says it does not.

| This RFC | Composer, HyperFlow path | Conductor phase | Paper phase |
|---|---|---|---|
| INTERPRET | `interpret_research_question` | `planning` (inside the agent loop) | 2 Workflow planning |
| PLAN | `plan_workflow`, deterministic | `planning` (same loop) | 2 Workflow planning |
| VALIDATE INTENT | `framework.py validate` — **verdict discarded** | `validation`, human gate only | 3 User validation |
| EXTRACT | `scripts/extract-data.sh` | `data_preparation` | 4 Provisioning |
| MEASURE | **absent** — profile resolved by name | `provisioning` measures, **does not forward** | 4 Provisioning |
| RESOLVE | inside `generate_workflow` | **absent** | 5 Deferred generation |
| MATERIALIZE | `HyperFlowGenerator.generate` | `generation` | 5 Deferred generation |
| EXECUTE | engine | `deployment`, `monitoring` | 6 Execution approval |
| VALIDATE RESULTS | `verify_outputs` — **passes on missing** | `completion` reports, does not judge | none |

The Conductor's `routing` precedes INTERPRET and is unaffected. Its `approval`
is a second human gate, orthogonal to the two above.

Four gaps are visible from the table alone: MEASURE produces evidence nobody
receives, RESOLVE is absent in the Conductor and fused into MATERIALIZE in the
composer, and both gates exist without failing.

### 5.2 INTERPRET receives domain knowledge only

`ResearchIntent` carries no resource field, so resource and backend policy
cannot change the output and can only crowd the vocabulary that does. This is
already the implemented behaviour and is asserted by
`tests/test_interpreter_isolation.py`; it is restated here because it is the one
placement decision the current tree gets right.

---

## 6. Resolution contract

RESOLVE consumes measured workload, measured environment, backend reserve, the
policy configuration version, and typed constraints carrying their source. It
consumes no prose.

It produces a resolution object in which every number describes the same
workflow:

```
effective_ind_jobs      >= 1
rows_per_task            = ceil(V / effective_ind_jobs)
effective_work           = rows_per_task * I
est_peak_mb              = memory_model(effective_work)
est_peak_mb             <= task_memory_hard_limit
concurrency * est_peak_mb <= host_mem - host_reserve - backend_reserve
```

`est_peak_mb` and `max_parallelism` are recomputed whenever a constraint or hint
changes `ind_jobs`. An estimate computed for a superseded recommendation is
never carried forward.

### 6.1 Override precedence

| Source | Treatment |
|---|---|
| hard limit | never overridable |
| explicit user constraint | honoured **where the invariants above permit**; otherwise RESOLVE fails and returns the conflict |
| named profile or preset | operative default |
| agent suggestion, unqualified | advisory; the recommendation is used |
| operator override | separately authorised, recorded prominently |

Objectives rank in this order: memory safety, explicit user constraints,
declared deadline, wall time, aggregate compute. Without the ordering, "safe",
"efficient" and "respect explicit values" conflict with no stated resolution.

The interaction that motivates this RFC deserves to be stated rather than
derived. An explicit user cap of `ind_jobs=1` on the HLA workload is honoured:
the effective estimate is 241 MB against a 1024 MB hard limit, and the run costs
throughput, which the user asked for. The same cap on whole chromosome 1 is
refused: the effective work implies roughly 18 GB in one task, the per-task
invariant fails, and RESOLVE returns the conflict for approval rather than
emitting a workflow that cannot run. A cap is a request, not a licence.

An unqualified number is not a user constraint because an agent produced it.

---

## 7. Backend obligation

Each backend declares the point at which its values bind and enforces the
resolution there.

| Backend | Binding point |
|---|---|
| HyperFlow | DAG task enumeration and launch environment construction |
| Nextflow | `ind_jobs`, `maxForks` and the memory directive, before process instantiation |
| dynamic engine (Parsl, Dask) | admission inside the running driver or scheduler, resolving repeatedly from telemetry |

`materialize` accepts only a resolution object it has validated as internally
consistent and computed for the same workload, environment, policy version and
reserve. It rejects any combination of task count, memory estimate and
concurrency that does not satisfy §6, and it binds every safety-relevant field
rather than recording some as metadata.

A backend that cannot name its binding point may not be handed a hinted
parameter, and receives the resolver's output alone.

This is what makes the dynamic engine a supported shape rather than a
counterexample. Because RESOLVE and MATERIALIZE are separate phases, "RESOLVE
runs repeatedly inside EXECUTE" is a property of one backend's phase graph, not
a contradiction of the model. A launch-time `LaunchSpec` is sufficient for
engines that bind at launch and insufficient for engines that do not; the
protocol must express both.

---

## 8. Verification

Each item names the perturbation that must fail. Every one of these currently
passes.

**Corruption, not deletion.** Change each named region's start and end
independently; substitute each population code in turn; make prose and
`KNOWN_REGIONS` disagree. Each mutation must fail a consistency test or a
semantic oracle. Presence probes over selected anchors do not discharge C2.

**Delivery.** Capture the prompt actually issued at each LLM-mediated stage.
Assert the required fragment is present, that policy and backend text are absent
where they cannot be acted on, and that the delivered documents' hashes appear
in provenance. Testing the loader in isolation asserts nothing about the prompt.

**Resolution properties.** Over a range of V, I, environments, backends and
constraints: effective estimates correspond to effective values; per-task memory
stays within the hard limit; concurrent memory stays within the host budget;
infeasible authoritative constraints fail rather than degrade; agent hints
cannot weaken a hard limit; neither raising nor lowering `ind_jobs` is assumed
safe without evaluating both per-task work and concurrency.

**Calibration.** Run the individuals worker on a fixture, measure peak RSS and
duration, and fail when the observation leaves the declared envelope or when the
worker image differs from the calibration's declared validity. Without this the
evidence in §4.2 is decoration: a rule that states why it holds still rots
silently if nothing reads it.

**Result oracles.** Fixtures carry known-positive and known-empty cases, so a
legitimate empty biological result is distinguishable from truncation or missing
annotation. Missing outputs fail. Archive members are inspected, not only the
archive. Row and coordinate coverage reconciles against the extracted variant
count. Cross-engine comparison is supplemented by an independent oracle so that
two identically empty results cannot pass.

---

## 9. Provenance

A plan and an execution artefact together record: the intent and its validation
verdict; the paths and hashes of documents delivered to each LLM stage; the
policy configuration version and hash; the environment measurements; the backend
and its reserve; requested values with their source; the recommendation; the
effective values with the work and memory estimate computed for them; rejected
constraints; the binding point; and the observed task count, concurrency,
memory and duration.

This is the minimum that supports a claim of complete composition provenance.
Where the claim is not worth the plumbing, the honest alternative is to weaken
the claim.

---

## 10. Adoption order

Wiring before enforcement. Turning the oracles on first produces a wall of red
that reports only what §1 already established.

1. Correct the guidance in `mcp_server.py:212` and the policy document, and
   generate one from the other. *(One source of live text.)*
2. Move operative values into versioned configuration; reduce policy prose to
   §4.1 and §4.2; delete §4.3.
3. Recompute `est_peak_mb` and `max_parallelism` from the effective `ind_jobs`
   and record those. *(Fixes §1.3 alone, and is small.)*
4. Add the §6 invariants to the resolver; introduce typed constraints with a
   source.
5. Deliver the planner fragment through the tool schema; add the delivery test.
6. Add MEASURE → RESOLVE in the Conductor: pass measured vCPUs, memory and
   backend reserve into resolution.
7. Give the Nextflow backend the same enforcement as HyperFlow, and make both
   validate the resolution they receive.
8. Add the corruption, calibration and resolution property tests.
9. Make the intent verdict, missing outputs and the scientific invariants fail.

Step 9 changes how existing runs are reported. Before scheduling it, count the
harness cases that have ever recorded `PASSED (partial outputs)` and those whose
intent differs from expected; that count decides whether step 9 lands in one
pass or in stages. It is not a footnote to the migration — it is the item that
determines whether the migration is adoptable.

---

## 11. Non-goals

- Asking a model to compute parallelism.
- Placing every policy document in every prompt.
- Requiring every engine to bind at launch.
- Self-tuning policy. Telemetry marks calibration stale; it does not rewrite
  policy.
- A blanket safety factor on the memory model. The model already over-predicts
  by 1.6–2.1× at every measured point; a further factor would over-fragment
  tasks to buy margin that exists. If the pass-fraction risk in RFC-003 §8
  warrants a margin, it should be measured against that risk and stated with
  its evidence, like any other number here.

---

## 12. Open questions

- **The pass fraction remains the weak point of the memory bound.** The 1.2 MB
  constant folds in the highest rate observed across HLA chunks. A region with
  more common variants would push memory the unsafe way. Measuring the first
  chunk and sizing the rest from the observation would remove the assumption
  entirely; whether that is worth a serialisation point is unresolved.
- **`min_work` is still a guess.** It binds on small regions with large hosts —
  the paper's cluster resolves HLA to 20 tasks, `min_work`-bound — so it decides
  more runs than its evidence supports. The calibration test in §8 gives the
  measurement needed to replace it.
- **Whether the planning agent benefits from policy at all is unmeasured.**
  RFC-004 §9 gated policy on an ablation measured against the interpreter, and
  applied the result to a stage nobody has measured. §4.1 is sized by what the
  agent can express, not by evidence about what helps it. The Skills ablation
  should be extended to the planning stage before the fragment grows.
- **The published ablation describes no shipped configuration.** S3 resolves
  `SKILL.md` through the alias in `skill_loader.py:33` to the HyperFlow manual,
  giving a 12,837-character context including engine text; the shipped
  interpreter receives 8,089 characters with the interpretation guidelines and
  no engine text. Either the alias goes and the harness is re-run, or the
  configurations are reported as what they are.
