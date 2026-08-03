# RFC-004: Engine Backends and a Shared Knowledge Layer

Status: draft
Scope: `workflow-composer/src/workflow_composer/`, plus a second engine port

## 1. Problem Statement

The composer's claim is that intent interpretation and domain knowledge are
independent of the workflow engine, and that only the deterministic layer
changes per engine. A second engine port now exists, and it shows the claim is
substantially true but structurally unexpressed.

### 1.1 What the second port proved

The Nextflow port reuses exactly three symbols:

```python
from workflow_composer.interpretation.llm_interpreter import interpret_research_question, LLMConfig
from workflow_composer.core.models import ResearchIntent
```

Everything downstream was written fresh. The five analysis scripts are reused
byte-for-byte from the same worker image. So the semantic layer and the science
port unchanged; the deterministic layer does not.

### 1.2 The knowledge layer is already engine-neutral

Measured across the six skill documents, 8 of 489 lines mention an engine:

| Document | Lines | Engine-specific |
|---|---|---|
| `populations.md`, `genomic-regions.md`, `research-contexts.md`, `data-sources.md` | 211 | 0 |
| `resource-policy.md` | 81 | 3 — the `engine_reserve` rationale |
| `SKILL.md` | 197 | 5 — `hflow run workflow.json`, `output_format: hyperflow` |

`SKILL.md` is two documents sharing a file: a tool manual (lines 7–138) and
domain policy (lines 140–197, "Choosing individuals parallelism" and
"Interpretation Guidelines").

### 1.3 What blocks a second engine

- `core/generator.py` emits HyperFlow JSON and sits in `core/`, alongside
  modules that are engine-neutral. There is no interface a second engine can
  implement, so the second port had to bypass the package entirely.
- `interpretation/skill_loader.py` has a hardcoded `SKILL_FILES` list resolved
  against a fixed package-relative `SKILL_DIR`, with no override and no
  registration. A second engine cannot contribute knowledge without editing the
  package.
- Nothing prevents engine-specific knowledge from reaching the interpreter, so
  "the intent is engine-independent" is a claim rather than a checked property.

## 2. Design

### 2.1 Both engines make the same decision

`core/parallelism.py:recommend_parallelism` is already a pure function of
`(variants, individuals, vcpus, host_mem_mb, chromosomes, mem_budget_mb,
engine_reserve, host_reserve_mb, min_work)`. Nothing in the formula is
HyperFlow-specific. The engines differ only in how the resolved values are
committed, and in when each dial binds:

| | HyperFlow | Nextflow |
|---|---|---|
| `ind_jobs` | enumerated into `workflow.json` before submission | consumed at runtime by the scatter |
| `max_parallelism` | `HF_VAR_REDIS_CMD_MAX_PARALLELISM` | `maxForks`, fixed at launch |
| `est_peak_mb` | recorded only | the `memory` directive |
| engine reserve | engine, Redis, merge step | JVM head process, Docker daemon |

### 2.2 Backend protocol

```python
# backends/base.py
@dataclass(frozen=True)
class EngineReserve:
    cores: int
    host_mb: int
    rationale: str

@dataclass(frozen=True)
class LaunchSpec:
    files: dict[str, str]      # relative path -> content
    command: list[str]
    env: dict[str, str]

class Backend(Protocol):
    name: str
    skill_fragment: str | None          # filename under knowledge/backends/
    def reserve(self) -> EngineReserve: ...
    def materialize(self, intent, measurements, resolution: Parallelism) -> LaunchSpec: ...
```

`EngineReserve` is how the one genuinely engine-specific piece of resource
policy is expressed: the skill documents the concept, the backend supplies the
number.

### 2.3 Knowledge layer, split by owner

`resource-policy.md` already argues for ownership-based separation. Apply it:

| Directory | Content | Owner | Engine-specific |
|---|---|---|---|
| `knowledge/domain/` | population codes, region coordinates, research contexts, data sources, interpretation guidelines | genomics curator | no |
| `knowledge/policy/` | memory budgets, vCPU profiles, work per task | whoever knows the machine | no |
| `knowledge/backends/` | how to invoke this engine, what artifact it consumes | backend maintainer | yes |

### 2.4 Knowledge reaches the stage that can act on it

`interpret_research_question` loads domain knowledge only: never a backend
fragment, and no longer policy either. The extracted intent therefore cannot be influenced by which
engine will run it. This is the property that makes engine independence
checkable rather than asserted, and it must be enforced by a test.

## 3. Work outside the agent loop

These steps involve git history, container images, or a full integration run.
They are done directly, not by implementer agents, and none of the tasks in §4
depend on them:

- Merge `1000genome-nextflow` preserving authorship; place `main.nf`,
  `nextflow.config`, `worker-nf.Dockerfile` and `testdata/` under
  `engines/nextflow/`, the GUI under `gui/`, and the reference bundles under
  `tests/equivalence/reference/`.
- Move `tests/integration/` to `engines/hyperflow/harness/`, updating
  `gui.py:_default_hf_integ`, `Makefile`, `.github/workflows/`, the
  `${REPO_ROOT}` interpolations in `cases.yaml`, and the `REPO_ROOT` /
  `FRAMEWORK_PY` / `EXTRACT_SCRIPT` paths in `run-research-tests.sh`.
- Delete `individuals.streaming.py`, which is byte-identical to
  `worker-base-image/scripts/individuals.py`, and have `worker-nf.Dockerfile`
  copy from the shared location instead. Rebase that image off the current
  worker tag rather than `1.1-latest`.
- Split `main.nf` so extraction can run alone, and delete
  `params.ind_chunk_lines`, which silently overrides the computed task size
  whenever policy asks for tasks larger than 5000 rows.
- Add the cross-engine equivalence CI job.

## 4. Implementation tasks

Each task is confined to `workflow-composer/`, is verified by
`python3 -m pytest workflow-composer/tests/ -q`, and needs no Docker, network,
image rebuild, or LLM call. They are ordered; each builds only on its
predecessors.

### 4.1 Backend protocol and registry

Add `backends/base.py` with `EngineReserve`, `LaunchSpec` and the `Backend`
protocol from §2.2, and `backends/__init__.py` with `register`,
`get_backend(name)` and `list_backends()`. No existing behaviour changes.

Acceptance: registry round-trips a stub backend; `get_backend` raises a clear
error for an unknown name; the existing suite still passes unchanged.

### 4.2 Move the HyperFlow and WfCommons emitters behind the protocol

Move `core/generator.py` to `backends/hyperflow/generator.py` and
`core/export.py:to_wfcommons` to `backends/wfcommons/exporter.py`. Leave
re-exports at both old module paths so `cli.py`, `mcp_server.py` and existing
imports keep working. Register a `hyperflow` backend whose `reserve()` returns
the engine/Redis/merge reserve currently implied by
`core/environment.py:engine_reserve`.

Acceptance: `tests/test_generator.py` and `tests/test_export.py` pass with no
edits to those files. That they are untouched is the point — it demonstrates the
move preserved behaviour.

### 4.3 Split the knowledge layer

Reorganise `skills/` into `knowledge/domain/`, `knowledge/policy/` and
`knowledge/backends/`. Split `SKILL.md` at its existing seam: the tool manual
(lines 7–138) becomes `knowledge/backends/hyperflow.md`; "Choosing individuals
parallelism" becomes `knowledge/policy/individuals-parallelism.md`;
"Interpretation Guidelines" becomes `knowledge/domain/interpretation.md`.
Update the `include` globs in `workflow-composer/pyproject.toml`.

Acceptance: `tests/test_skill_guidance.py` passes; every domain and policy
document is reachable through the loader; no document is lost — the
concatenated content of the new tree equals the old one modulo headers.

### 4.4 Backend-aware skill loading and interpreter isolation

Give `load_skill_context` a `backend: str | None = None` parameter composing
domain + policy + optionally one backend fragment. `interpret_research_question`
must call it with no backend.

Acceptance: a test asserts the context passed to the interpreter contains no
text from any file under `knowledge/backends/`; a second test asserts
`load_skill_context(backend="hyperflow")` does contain it. This is the property
in §2.4 and must fail if the wiring regresses.

### 4.5 Nextflow backend

Add `backends/nextflow/params.py` implementing `Backend`. Port
`intent_to_params`, `write_extract_csv` and `build_command` from the second
port's `composer.py`. `materialize` returns a `LaunchSpec` whose `files` carry
`extract.csv` and `columns.txt`, and whose `command` is a `nextflow run`
invocation carrying `--populations`, `--ind_jobs`, `--ind_max_forks` and
`--task_mem`. `reserve()` returns the JVM-head-plus-Docker-daemon reserve. Add
`knowledge/backends/nextflow.md`.

Acceptance: for an intent naming EUR and AFR plus the BRCA1 region, the
`LaunchSpec` contains an `extract.csv` with one correctly formatted row and a
command carrying both populations; populations absent from the bundled
population files are dropped, and the drop is reported rather than silent.

### 4.6 Resolution parity and correct individual counts

Wire `generate_columns_txt` into the Nextflow backend so the individual count
reflects the intent's populations rather than the full 2504-sample cohort.

Acceptance: a test asserts that for one fixed `(variants, individuals,
ComputeEnvironment)`, both backends receive an identical `Parallelism` —
same `ind_jobs`, `max_parallelism` and `est_peak_mb`. A second test asserts the
generated `columns.txt` for a single-population intent has that population's
sample count, not 2504.

## 5. Validation

- The suite in `workflow-composer/tests/` stays green throughout; tasks 4.2 and
  4.3 must not require edits to the existing generator, export or skill tests.
- Interpreter isolation (§4.4) is the property that carries the architectural
  claim; treat its test as a regression guard, not a formality.
- Cross-engine equivalence — one intent through both backends, extracted result
  trees compared with `diff -r` — is covered in §3 because it needs Docker and a
  real run. Never compare `.tar.gz` bytes, and never byte-compare the unseeded
  `mutation_overlap` or `frequency` outputs.

## 6. Non-goals

- Extracting `workflow-composer` into its own repository. Deferred until a
  second scientific domain exists.
- Generating `main.nf` from the composer. The Nextflow DAG is application code
  that the backend parameterises; generating it would reintroduce the
  artifact-centric coupling this RFC removes.
- Moving the repository out of the `hyperflow-wms` organisation.


## 9. Refinement: policy is opt-in, not default

Measured after the split: resource policy made up 44% of the interpreter's
context -- `resource-policy.md` 30.4%, `individuals-parallelism.md` 13.5% --
while a `ResearchIntent` carries no resource field any of it could inform. The
Skills ablation records what surplus context costs this task: GPT-4.1-mini
scored 8.7pp lower with the full document set than with vocabulary alone, its
T4 clarification accuracy falling from 53% to 13%.

`load_skill_context` therefore gates policy behind `include_policy=True`. The
interpretation path takes the default and gets domain vocabulary only; whoever
sizes parallelism asks for policy explicitly, and the MCP server continues to
expose every document as a resource regardless.

Two documents also held content belonging to a different stage, which the
original split moved wholesale rather than by ownership:

- `domain/interpretation.md` ended with "Always call plan_workflow with the
  extracted parameters", an instruction the interpreter cannot follow -- it
  does structured extraction through `instructor` and has no tools.
- `domain/data-sources.md` carried a "Data Extraction" section documenting
  `extract-data.sh`, `plan_workflow` and `g1kwf generate`. Where the data
  lives is domain knowledge; driving one engine's toolchain is not, and
  Nextflow extracts inside its own DAG instead.

Both moved to `backends/hyperflow.md`. The interpretation context fell from
14,988 to 8,089 characters with every vocabulary anchor intact, and tests now
assert both that policy is absent from the default context and that it remains
reachable with `include_policy=True`.
