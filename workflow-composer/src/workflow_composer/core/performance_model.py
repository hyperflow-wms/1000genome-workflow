"""
Performance model: the cost coefficients that turn a workload's shape into
predicted work and span.

Capacity planning (``core/capacity.py``) needs a cost per stage, per region,
before any data has been downloaded. That cost is expressed as a fixed
per-task term plus a term proportional to ``D_r = V_r * I`` (variants times
individuals) for each of the four stages that dominate wall time:

- ``individuals`` -- one task per chunk. ``a_ind`` is the fixed cost of
  starting a task and rescanning the input up to its chunk (container start,
  argument parsing, seek); ``b_ind`` is the marginal cost per variant *
  individual actually written to that chunk's archive.
- ``individuals_merge`` -- one task per region, concatenating every chunk's
  archive into the region's combined output. ``b_merge`` is the marginal
  cost per variant * individual being concatenated; ``c_merge`` is the fixed
  cost per archive merged in, so it scales with the chunk count ``J_r`` --
  more, smaller chunks mean more merge overhead even though each chunk did
  less individuals-stage work.
- ``mutation_overlap`` -- one task per population, per region. ``a_mo`` is
  the fixed cost of loading and comparing against the reference; ``b_mo`` is
  the marginal cost per variant * individual scanned.
- ``frequency`` -- one task per population, per region. ``a_fr`` is the fixed
  cost, dominated by reading every sample column regardless of region size;
  ``b_fr`` is the marginal cost per variant * individual.

``sifting`` has no coefficients here. It costs about 1 second per region and
runs concurrently with ``individuals``, so it contributes materially to
neither the work sum nor the longest path -- see
``CAPACITY-IMPLEMENTATION-PLAN.md`` section 2.1.

This module is deliberately parallel in structure to ``core/environment.py``:
a frozen dataclass, a dict of named profiles, and a ``resolve`` classmethod
with the same override contract. The difference is what each module's
fields describe. ``ComputeEnvironment`` describes the *machine* a plan will
run on, and its three most deployment-relevant fields (``vcpus``,
``host_mem_mb``, ``mem_budget_mb``) accept environment-variable overrides
because a deployment is likely to need to set them without touching code.
``PerformanceModel`` describes *calibration data* -- how long stages actually
took on a measured host -- and calibration data is not a deployment knob.
There is deliberately no environment-variable override for any coefficient:
changing one is a decision to recalibrate, not a decision about where this
plan is running, and it should be made in code (a new named profile, or an
explicit ``resolve(..., a_ind=...)`` override for one-off experiments) so the
change is visible in a diff rather than silently sourced from the
environment a plan happened to run in.

Changing a shipped coefficient invalidates any test pinned to the profile's
current values -- see ``tests/test_performance_model.py`` and the Q1/Q3
reproductions in ``tests/test_capacity.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace


@dataclass(frozen=True)
class PerformanceModel:
    """Calibrated cost coefficients for one named performance profile.

    Every coefficient pairs a fixed per-task term with a term proportional
    to ``D = V * I`` (variants times individuals), per stage -- see the
    module docstring for what each one means and RFC-006-REVIEW.md section 8
    for how the shipped values were fitted.
    """

    name: str
    version: str
    provenance: str

    a_ind: float
    b_ind: float
    b_merge: float
    c_merge: float
    a_mo: float
    b_mo: float
    a_fr: float
    b_fr: float

    @classmethod
    def resolve(cls, name: str, **overrides: float) -> "PerformanceModel":
        """Resolve a named performance profile, applying explicit overrides.

        Unlike ``ComputeEnvironment.resolve``, there is no environment-variable
        tier: coefficients are calibration data, not deployment knobs, so the
        only way to change one is an explicit keyword argument -- see the
        module docstring for why.

        Args:
            name: one of the shipped profile names (e.g. "rfc-006-review").
            **overrides: any ``PerformanceModel`` field except ``name``
                (``version``, ``provenance``, or any of the eight
                coefficients).

        Raises:
            ValueError: if ``name`` is not a known profile.
            TypeError: if an override does not name a real field.
        """
        if name not in _PROFILES:
            raise ValueError(
                f"Unknown performance model '{name}'. "
                f"Known profiles: {sorted(_PROFILES)}"
            )

        overridable_fields = {f.name for f in fields(cls)} - {"name"}
        unknown = set(overrides) - overridable_fields
        if unknown:
            raise TypeError(
                f"resolve() got unexpected override(s) {sorted(unknown)}; "
                f"valid fields are {sorted(overridable_fields)}"
            )

        model = _PROFILES[name]
        if overrides:
            model = replace(model, **overrides)
        return model


# ---------------------------------------------------------------------------
# Shipped profiles
# ---------------------------------------------------------------------------

_PROFILES: dict[str, PerformanceModel] = {
    # Fitted from the runs recorded in RFC-006-REVIEW.md section 8, on one
    # host, with one shared filesystem, at low concurrency. See that
    # section's Q1/Q3 table for the work and span predictions these
    # coefficients reproduce, and CAPACITY-IMPLEMENTATION-PLAN.md section 6
    # for what would make them trustworthy rather than a first cut.
    "rfc-006-review": PerformanceModel(
        name="rfc-006-review",
        version="1.0.0",
        provenance=(
            "Fitted from the runs recorded in RFC-006-REVIEW.md section 8 "
            "on one host, one shared filesystem, at low concurrency. Not a "
            "controlled calibration -- see CAPACITY-IMPLEMENTATION-PLAN.md "
            "section 6."
        ),
        a_ind=8.0,
        b_ind=2.0e-6,
        b_merge=1.3e-6,
        c_merge=0.8,
        a_mo=15.0,
        b_mo=3.5e-8,
        a_fr=105.0,
        b_fr=6.0e-7,
    ),
}


# Shipped profile so callers need not name it.
DEFAULT_PERFORMANCE_MODEL = _PROFILES["rfc-006-review"]
