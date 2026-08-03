"""
Tests for the Nextflow backend (RFC-004 section 4.5).

Covers `intent_to_params`, `write_extract_csv`, `build_command`,
`NextflowBackend.reserve`, and `NextflowBackend.materialize`, plus the
acceptance criterion: for an intent naming EUR and AFR plus the BRCA1
region, the `LaunchSpec` contains an `extract.csv` with one correctly
formatted row and a command carrying both populations; a population absent
from the bundled population files is dropped and the drop is reported
rather than silent.
"""
from __future__ import annotations

import pytest

from workflow_composer.backends import get_backend
from workflow_composer.backends.base import EngineReserve, LaunchSpec
from workflow_composer.backends.nextflow import (
    NextflowBackend,
    NextflowParams,
    build_command,
    intent_to_params,
    write_extract_csv,
)
from workflow_composer.core.models import GenomicRegion, ResearchIntent
from workflow_composer.core.parallelism import Parallelism
from workflow_composer.interpretation.skill_loader import BACKENDS_DIR, load_skill_context

BRCA1 = GenomicRegion(
    name="BRCA1", chromosome="17", start=43044295, end=43125483, context="breast cancer",
)

RESOLUTION = Parallelism(
    ind_jobs=12,
    max_parallelism=8,
    est_peak_mb=340,
    binding="cores",
    reason="ind_jobs=12 max_parallelism=8 (core-bound; V=100,000 I=1,675 C=8 est_peak=340MB/task)",
)


def _eur_afr_brca1_intent(populations: list[str] | None = None) -> ResearchIntent:
    return ResearchIntent(
        analysis_type="population_comparison",
        populations=populations if populations is not None else ["EUR", "AFR"],
        regions=[BRCA1],
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_nextflow_backend_is_registered():
    backend = get_backend("nextflow")
    assert isinstance(backend, NextflowBackend)
    assert backend.name == "nextflow"


def test_nextflow_backend_skill_fragment_names_its_own_file():
    backend = get_backend("nextflow")
    assert backend.skill_fragment == "nextflow.md"
    assert (BACKENDS_DIR / backend.skill_fragment).exists()


# ---------------------------------------------------------------------------
# reserve(): JVM head + Docker daemon
# ---------------------------------------------------------------------------

def test_reserve_returns_engine_reserve():
    reserve = NextflowBackend().reserve()
    assert isinstance(reserve, EngineReserve)
    assert reserve.cores >= 1
    assert reserve.host_mb > 0


def test_reserve_rationale_names_jvm_and_docker():
    rationale = NextflowBackend().reserve().rationale.lower()
    assert "jvm" in rationale
    assert "docker" in rationale


# ---------------------------------------------------------------------------
# intent_to_params: population validation and region resolution
# ---------------------------------------------------------------------------

def test_intent_to_params_keeps_bundled_populations():
    params = intent_to_params(_eur_afr_brca1_intent())
    assert params.populations == ["EUR", "AFR"]
    assert params.dropped_populations == []


def test_intent_to_params_resolves_region_from_intent():
    params = intent_to_params(_eur_afr_brca1_intent())
    assert params.regions == [BRCA1]


def test_intent_to_params_drops_unbundled_population_and_reports_it():
    intent = _eur_afr_brca1_intent(populations=["EUR", "ZZZ"])
    params = intent_to_params(intent)
    assert params.populations == ["EUR"]
    assert "ZZZ" not in params.populations
    assert params.dropped_populations == ["ZZZ"]


def test_intent_to_params_drop_report_is_not_silently_empty():
    # The failure mode this guards: a population that disappears with no
    # trace anywhere in the returned params.
    intent = _eur_afr_brca1_intent(populations=["NOTAPOP"])
    params = intent_to_params(intent)
    assert params.populations == []
    assert params.dropped_populations == ["NOTAPOP"]


def test_intent_to_params_falls_back_to_whole_chromosome_when_no_region():
    intent = ResearchIntent(
        analysis_type="multi_population", populations=["EUR"], chromosomes=["22"],
    )
    params = intent_to_params(intent)
    assert len(params.regions) == 1
    assert params.regions[0].chromosome == "22"
    assert params.regions[0].start == 1
    assert params.regions[0].end > 1


# ---------------------------------------------------------------------------
# write_extract_csv: one correctly formatted row
# ---------------------------------------------------------------------------

def test_write_extract_csv_header():
    params = intent_to_params(_eur_afr_brca1_intent())
    csv_text = write_extract_csv(params)
    lines = csv_text.strip("\n").split("\n")
    assert lines[0] == "region,chromosome,start,end,populations"


def test_write_extract_csv_has_exactly_one_data_row_for_one_region():
    params = intent_to_params(_eur_afr_brca1_intent())
    csv_text = write_extract_csv(params)
    lines = csv_text.strip("\n").split("\n")
    assert len(lines) == 2  # header + one row


def test_write_extract_csv_row_is_correctly_formatted():
    params = intent_to_params(_eur_afr_brca1_intent())
    csv_text = write_extract_csv(params)
    _, row = csv_text.strip("\n").split("\n")
    assert row == "BRCA1,17,43044295,43125483,EUR;AFR"


def test_write_extract_csv_excludes_dropped_populations():
    params = intent_to_params(_eur_afr_brca1_intent(populations=["EUR", "ZZZ"]))
    csv_text = write_extract_csv(params)
    _, row = csv_text.strip("\n").split("\n")
    assert "ZZZ" not in row
    assert row == "BRCA1,17,43044295,43125483,EUR"


# ---------------------------------------------------------------------------
# build_command: carries both populations and all three parallelism dials
# ---------------------------------------------------------------------------

def test_build_command_starts_with_nextflow_run():
    params = intent_to_params(_eur_afr_brca1_intent())
    command = build_command(params, RESOLUTION)
    assert command[0] == "nextflow"
    assert command[1] == "run"


def test_build_command_carries_both_populations():
    params = intent_to_params(_eur_afr_brca1_intent())
    command = build_command(params, RESOLUTION)
    assert "--populations" in command
    populations_value = command[command.index("--populations") + 1]
    assert "EUR" in populations_value.split(",")
    assert "AFR" in populations_value.split(",")


def test_build_command_carries_all_three_parallelism_flags():
    params = intent_to_params(_eur_afr_brca1_intent())
    command = build_command(params, RESOLUTION)
    assert command[command.index("--ind_jobs") + 1] == "12"
    assert command[command.index("--ind_max_forks") + 1] == "8"
    assert command[command.index("--task_mem") + 1] == "340MB"


def test_build_command_never_recomputes_dials_from_a_different_resolution():
    params = intent_to_params(_eur_afr_brca1_intent())
    other = Parallelism(
        ind_jobs=99, max_parallelism=99, est_peak_mb=999, binding="memory", reason="x",
    )
    command = build_command(params, other)
    assert command[command.index("--ind_jobs") + 1] == "99"
    assert command[command.index("--ind_max_forks") + 1] == "99"
    assert command[command.index("--task_mem") + 1] == "999MB"


# ---------------------------------------------------------------------------
# materialize(): the LaunchSpec end to end
# ---------------------------------------------------------------------------

def test_materialize_returns_launch_spec():
    spec = NextflowBackend().materialize(
        intent=_eur_afr_brca1_intent(), measurements=None, resolution=RESOLUTION,
    )
    assert isinstance(spec, LaunchSpec)


def test_materialize_files_carry_extract_csv_and_columns_txt():
    spec = NextflowBackend().materialize(
        intent=_eur_afr_brca1_intent(), measurements=None, resolution=RESOLUTION,
    )
    assert "extract.csv" in spec.files
    assert "columns.txt" in spec.files
    assert spec.files["columns.txt"].startswith("#CHROM\t")


def test_materialize_extract_csv_has_one_correctly_formatted_row():
    spec = NextflowBackend().materialize(
        intent=_eur_afr_brca1_intent(), measurements=None, resolution=RESOLUTION,
    )
    lines = spec.files["extract.csv"].strip("\n").split("\n")
    assert len(lines) == 2
    assert lines[1] == "BRCA1,17,43044295,43125483,EUR;AFR"


def test_materialize_command_carries_both_populations():
    spec = NextflowBackend().materialize(
        intent=_eur_afr_brca1_intent(), measurements=None, resolution=RESOLUTION,
    )
    populations_value = spec.command[spec.command.index("--populations") + 1]
    assert set(populations_value.split(",")) == {"EUR", "AFR"}


def test_materialize_drops_unbundled_population_from_extract_csv_and_command():
    spec = NextflowBackend().materialize(
        intent=_eur_afr_brca1_intent(populations=["EUR", "AFR", "ZZZ"]),
        measurements=None,
        resolution=RESOLUTION,
    )
    assert "ZZZ" not in spec.files["extract.csv"]
    assert "ZZZ" not in spec.command[spec.command.index("--populations") + 1]


# ---------------------------------------------------------------------------
# Knowledge fragment wiring (complements test_interpreter_isolation.py)
# ---------------------------------------------------------------------------

def test_load_skill_context_with_nextflow_backend_includes_its_fragment():
    context = load_skill_context(backend="nextflow")
    fragment_text = (BACKENDS_DIR / "nextflow.md").read_text()
    assert fragment_text in context


def test_load_skill_context_default_excludes_nextflow_fragment():
    context = load_skill_context()
    fragment_text = (BACKENDS_DIR / "nextflow.md").read_text()
    assert fragment_text not in context
