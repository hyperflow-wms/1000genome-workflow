"""
Resource policy: the numbers that describe the target machine.

RFC-003 section 3.1 splits "policy" into two audiences that should not share
a file: domain policy (which populations a question implies, region lookups)
belongs to a genomics curator and lives in the skill prose; resource policy
(memory budget per task, vCPUs, host memory) belongs to whoever knows the
target machine and lives here.

This module holds two independent things that section 7 item 2 explicitly
forbids bundling together:

- ``MEMORY_BUDGET_PRESETS``: named per-task memory ceilings only. A curator
  can reason about "how much memory may one task use" without knowing
  anything about the host. They must NOT carry ``vcpus``, ``host_mem_mb``, or
  the derived core count ``C`` -- those describe the machine, not the task.
- ``ComputeEnvironment``: the machine-describing fields, resolved per named
  environment (``local``, ``aws``, ``gcp``) with explicit-argument and
  environment-variable overrides. This is where ``vcpus`` and
  ``host_mem_mb`` live.

``recommend_for_environment`` is the convenience that forwards a resolved
``ComputeEnvironment`` into ``recommend_parallelism`` (RFC-003 section 7 item
1, in ``core/parallelism.py``) so no caller has to assemble that argument
list by hand.

``check_budget_consistency`` implements the second bullet of RFC-003 section
8 ("Two memory budgets, set independently"), choosing the "keep both and add
a consistency check that refuses contradictory settings" option over
iterating the two budgets to a fixed point.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace

# ---------------------------------------------------------------------------
# Memory-budget presets: per-task ceiling only (RFC-003 section 7 item 2)
# ---------------------------------------------------------------------------

MEMORY_BUDGET_PRESETS: dict[str, int] = {
    # Half of "medium". For hosts that are themselves memory-constrained (a
    # laptop, a shared dev box), so a single task's ceiling leaves more
    # concurrent tasks fitting in a small host_mem_mb -- at the cost of more,
    # smaller tasks (a lower max_work in RFC-003 section 4.3).
    "small": 256,
    # The RFC-003 section 4.1 cost model is calibrated against measured peak
    # RSS on the HLA region; section 4.4's worked examples all assume this
    # value. Changing it invalidates those worked examples and the tests
    # pinned to them.
    "medium": 512,
    # Double "medium". For hosts with memory to spare, trading it for fewer,
    # larger tasks -- fewer container starts and less input-rescan overhead
    # per variant (RFC-003 section 4.1's fixed per-task cost).
    "large": 1024,
}


# ---------------------------------------------------------------------------
# ComputeEnvironment: the machine-describing fields
# ---------------------------------------------------------------------------

# Environment-variable overrides. Only these three fields have one: they are
# the knobs a deployment is most likely to need to set without touching code
# (e.g. a container with a memory limit, or a CI runner with a fixed core
# count). engine_reserve and host_reserve_mb are tuning knobs for people
# already editing this module, not deployment-time overrides, so they are
# only settable via explicit resolve() arguments.
_ENV_VARS = {
    "vcpus": "G1KWF_VCPUS",
    "host_mem_mb": "G1KWF_HOST_MEM_MB",
    "mem_budget_mb": "G1KWF_MEM_BUDGET_MB",
}


@dataclass(frozen=True)
class ComputeEnvironment:
    """Machine-describing resource policy for one named compute environment.

    RFC-003 section 3.1: this is the "resource" half of policy, owned by
    whoever knows the target machine -- distinct from the "domain" half
    (region/population mapping) that stays in skill prose.
    """

    name: str
    vcpus: int
    host_mem_mb: int
    engine_reserve: int
    host_reserve_mb: int
    mem_budget_mb: int

    @classmethod
    def resolve(cls, name: str, **overrides: int) -> "ComputeEnvironment":
        """Resolve a named environment, applying overrides in priority order.

        Precedence, highest first: explicit keyword argument, then the
        matching environment variable (``G1KWF_VCPUS``,
        ``G1KWF_HOST_MEM_MB``, ``G1KWF_MEM_BUDGET_MB``), then the shipped
        profile's default.

        This is a direct answer to the RFC-003 section 8 open question
        "Where do vcpus, host_mem_mb, and MAX_PARALLELISM come from for a
        remote target?" -- by declaration (a named profile plus explicit
        overrides), not by detection (probing the host at run time). A
        caller that wants detection is free to probe the host itself and
        pass the result in as an explicit override; this function never
        inspects the machine it runs on.

        Args:
            name: one of the shipped profile names ("local", "aws", "gcp").
            **overrides: any ``ComputeEnvironment`` field except ``name``
                (``vcpus``, ``host_mem_mb``, ``engine_reserve``,
                ``host_reserve_mb``, ``mem_budget_mb``).

        Raises:
            ValueError: if ``name`` is not a known profile.
            TypeError: if an override does not name a real field.
        """
        if name not in _PROFILES:
            raise ValueError(
                f"Unknown compute environment '{name}'. "
                f"Known environments: {sorted(_PROFILES)}"
            )

        overridable_fields = {f.name for f in fields(cls)} - {"name"}
        unknown = set(overrides) - overridable_fields
        if unknown:
            raise TypeError(
                f"resolve() got unexpected override(s) {sorted(unknown)}; "
                f"valid fields are {sorted(overridable_fields)}"
            )

        env = _PROFILES[name]

        # Environment variables beat the shipped profile.
        env_values: dict[str, int] = {}
        for field_name, var_name in _ENV_VARS.items():
            raw = os.environ.get(var_name)
            if raw is not None:
                env_values[field_name] = int(raw)
        if env_values:
            env = replace(env, **env_values)

        # Explicit arguments beat everything, including environment
        # variables.
        if overrides:
            env = replace(env, **overrides)

        return env


# Shipped profiles for the environment names already used across the tree
# (data_resolver.DATA_SOURCES, cli.py --env, mcp_server.py). Sizes are
# representative, not measured: pick a general-purpose instance class per
# cloud and let deployment-time overrides correct it for the actual target.
_PROFILES: dict[str, ComputeEnvironment] = {
    # RFC-003 section 4.4's worked examples: 31 GB host, 16 vCPUs,
    # engine_reserve=1, mem_budget_mb=512. This is the reference environment
    # the acceptance tests and section 4.4's table are pinned to.
    "local": ComputeEnvironment(
        name="local",
        vcpus=16,
        host_mem_mb=31744,  # 31 GiB
        engine_reserve=1,
        host_reserve_mb=2048,
        mem_budget_mb=MEMORY_BUDGET_PRESETS["medium"],
    ),
    # Representative of an AWS general-purpose instance sized for this
    # workload (e.g. m5.2xlarge: 8 vCPUs, 32 GiB).
    "aws": ComputeEnvironment(
        name="aws",
        vcpus=8,
        host_mem_mb=32768,  # 32 GiB
        engine_reserve=1,
        host_reserve_mb=2048,
        mem_budget_mb=MEMORY_BUDGET_PRESETS["medium"],
    ),
    # Representative of a comparable GCP general-purpose instance
    # (e.g. n2-standard-8: 8 vCPUs, 32 GiB).
    "gcp": ComputeEnvironment(
        name="gcp",
        vcpus=8,
        host_mem_mb=32768,  # 32 GiB
        engine_reserve=1,
        host_reserve_mb=2048,
        mem_budget_mb=MEMORY_BUDGET_PRESETS["medium"],
    ),
}


# ---------------------------------------------------------------------------
# Consistency check: RFC-003 section 8, "Two memory budgets, set independently"
# ---------------------------------------------------------------------------

def check_budget_consistency(env: ComputeEnvironment) -> None:
    """Refuse a ComputeEnvironment whose two independent budgets contradict.

    RFC-003 section 8 notes that ``mem_budget_mb`` (a single task's ceiling)
    and ``host_mem_mb`` (all concurrent tasks' ceiling) are set independently
    and nothing keeps them consistent. This picks the "keep both and add a
    consistency check that refuses contradictory settings" option over
    iterating the two to a fixed point.

    Checks two ways an environment can be self-contradictory:

    1. A single task's ``mem_budget_mb`` does not even fit in the host's
       usable memory (``host_mem_mb - host_reserve_mb``). If this fails,
       lower ``mem_budget_mb``, raise ``host_mem_mb``, or lower
       ``host_reserve_mb`` -- the error names which.
    2. ``vcpus <= engine_reserve`` leaves zero cores for individuals-stage
       tasks after reserving the engine, redis, and the merge step. If this
       fails, raise ``vcpus`` or lower ``engine_reserve``.

    This does not check that *concurrent* tasks fit (``max_parallelism *
    est_peak_mb <= host_mem_mb - host_reserve_mb``) -- that invariant is
    ``recommend_parallelism``'s job at plan time, once the actual workload
    (variants, individuals) is known. This function only rejects
    environments where a single task cannot possibly fit, which is knowable
    from the environment alone.

    Raises:
        ValueError: naming the offending field, if either check fails.
    """
    available_host_mem_mb = env.host_mem_mb - env.host_reserve_mb
    if env.mem_budget_mb > available_host_mem_mb:
        raise ValueError(
            f"ComputeEnvironment '{env.name}': mem_budget_mb "
            f"({env.mem_budget_mb} MB) does not fit in host_mem_mb - "
            f"host_reserve_mb ({env.host_mem_mb} - {env.host_reserve_mb} = "
            f"{available_host_mem_mb} MB). A single task cannot run. "
            f"Lower mem_budget_mb, raise host_mem_mb, or lower "
            f"host_reserve_mb."
        )

    if env.vcpus <= env.engine_reserve:
        raise ValueError(
            f"ComputeEnvironment '{env.name}': vcpus ({env.vcpus}) must be "
            f"greater than engine_reserve ({env.engine_reserve}), or no "
            f"cores remain for individuals-stage tasks after reserving the "
            f"engine, redis, and the merge step. Raise vcpus or lower "
            f"engine_reserve."
        )


# ---------------------------------------------------------------------------
# recommend_for_environment: forward a ComputeEnvironment into recommend_parallelism
# ---------------------------------------------------------------------------

def recommend_for_environment(
    variants: int,
    individuals: int,
    env: "ComputeEnvironment",
    chromosomes: int = 1,
):
    """Call ``recommend_parallelism`` with a resolved ComputeEnvironment's fields.

    Convenience so no caller assembles ``recommend_parallelism``'s argument
    list by hand from a ``ComputeEnvironment`` -- see ``core/parallelism.py``
    (RFC-003 section 7 item 1) for the formula this forwards into.

    Args:
        variants: V, actual row_count of the input (not bp span).
        individuals: I, individual count after population filtering.
        env: a resolved ``ComputeEnvironment`` (see ``ComputeEnvironment.resolve``).
        chromosomes: number of chromosomes running concurrently (RFC-003
            section 4.5).

    Returns:
        The same ``Parallelism`` that calling ``recommend_parallelism``
        directly with ``env``'s fields unpacked would return.
    """
    from .parallelism import recommend_parallelism

    return recommend_parallelism(
        variants=variants,
        individuals=individuals,
        vcpus=env.vcpus,
        host_mem_mb=env.host_mem_mb,
        chromosomes=chromosomes,
        mem_budget_mb=env.mem_budget_mb,
        engine_reserve=env.engine_reserve,
        host_reserve_mb=env.host_reserve_mb,
    )
