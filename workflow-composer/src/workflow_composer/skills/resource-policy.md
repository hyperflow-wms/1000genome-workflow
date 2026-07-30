# Resource Policy

This is the resource half of parallelism policy (RFC-003 §3.1): the numbers
that describe the target machine, as opposed to domain policy (which
populations and regions a question implies — that half stays in `SKILL.md`
and `research-contexts.md`).

**Owner: whoever knows the target machine** — the person deploying or
operating the HyperFlow engine, not the genomics curator who writes the
domain-policy prose. A curator cannot sensibly choose a memory budget or a
vCPU count without knowing the host; conversely, whoever sets these numbers
does not need to know what HLA or AFR means. Keeping the two apart means
neither audience edits numbers it has no basis for.

The fields below are `ComputeEnvironment` (`core/environment.py`). Nothing
in this file computes a recommendation — that is `recommend_parallelism`
(`core/parallelism.py`), which every caller (`plan_workflow`,
`generate_workflow`, the CLI, the test harness) goes through rather than
reading these fields directly.

## Shipped compute environment profiles

Three named profiles ship in `core/environment.py`, selected by
`compute_environment` in `plan_workflow` / `generate_workflow`:

| Profile | vcpus | host_mem_mb | mem_budget_mb |
|---|---|---|---|
| `local` | 16 | 31744 (31 GiB) | 512 |
| `aws` | 8 | 32768 (32 GiB) | 512 |
| `gcp` | 8 | 32768 (32 GiB) | 512 |

`local` is the reference environment RFC-003 §4.4's worked examples are
pinned to. `aws` and `gcp` are representative general-purpose instance
sizes, not measurements of a specific instance type — a deployment that
knows its actual instance should override rather than trust the default.

## Fields, meaning, and owner

Every field on `ComputeEnvironment` is documented here; nothing on that
dataclass should be resource policy that this file omits.

| Field | Meaning | Owner |
|---|---|---|
| `name` | Which shipped profile this is (`"local"`, `"aws"`, `"gcp"`) | whoever knows the target machine |
| `vcpus` | vCPUs available on the target host, before reserving any for the engine | whoever knows the target machine |
| `host_mem_mb` | Total host memory in MB, before reserving any for the OS | whoever knows the target machine |
| `engine_reserve` | Cores reserved for the HyperFlow engine, Redis, and the merge step, subtracted from `vcpus` before sizing tasks | whoever knows the target machine |
| `host_reserve_mb` | Host memory in MB reserved for the OS and anything that is not an individuals-stage task, subtracted from `host_mem_mb` before sizing concurrency | whoever knows the target machine |
| `mem_budget_mb` | Memory ceiling for a single task; usually set via the `"small"`/`"medium"`/`"large"` preset rather than a raw number | whoever knows the target machine |

## Memory-budget presets

`MEMORY_BUDGET_PRESETS` (`core/environment.py`) names three per-task memory
ceilings — `"small"` (256 MB), `"medium"` (512 MB), `"large"` (1024 MB) —
and nothing else. They deliberately do not bundle `vcpus`, `host_mem_mb`, or
a derived core count: those describe the machine, not the task, and belong
in the `ComputeEnvironment` fields above instead. Picking a preset is still
resource policy — a curator who does not know the host's memory has no
basis for choosing among them.

## Overrides

Explicit keyword argument beats environment variable
(`G1KWF_VCPUS`, `G1KWF_HOST_MEM_MB`, `G1KWF_MEM_BUDGET_MB`) beats the
shipped profile's default. `engine_reserve` and `host_reserve_mb` have no
environment-variable override — they are tuning knobs for someone already
editing `core/environment.py`, not deployment-time overrides.

## Consistency rule

`check_budget_consistency` (`core/environment.py`) refuses a
`ComputeEnvironment` where a single task's `mem_budget_mb` does not fit in
the host's usable memory (`host_mem_mb - host_reserve_mb`) — a per-task
budget larger than the host budget allows is rejected rather than silently
producing a workflow that cannot run its own first task. It also refuses
`vcpus <= engine_reserve`, which would leave zero cores for individuals-stage
tasks after reserving the engine, Redis, and the merge step. It does not
check that *concurrent* tasks fit collectively — that is
`recommend_parallelism`'s job at plan time, once the actual workload
(variant and individual counts) is known, not something knowable from the
environment alone.
