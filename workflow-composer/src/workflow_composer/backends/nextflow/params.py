"""
The Nextflow backend: turns a resolved research intent into the artifacts a
`nextflow run` invocation consumes.

Ports three functions from the second port's (out-of-tree) `composer.py`,
rebuilt from the behaviour specified in RFC-004 sections 2.1 and 4.5 rather
than by reading that file -- it is not part of this repository:

- `intent_to_params` validates `intent.populations` against the bundled
  population files and resolves `intent.regions` (or, absent those,
  `intent.chromosomes`) into concrete genomic regions to extract.
- `write_extract_csv` renders the region/population selection `main.nf`'s
  extraction step consumes.
- `build_command` renders the `nextflow run` invocation, binding the three
  parallelism dials `recommend_parallelism` produced -- `materialize` never
  recomputes them (see `core/parallelism.py`).

See RFC-004 sections 2.1 and 4.5.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ..base import EngineReserve, LaunchSpec
from ...core.data_resolver import CHROMOSOME_LENGTH_BP
from ...core.generator import BUNDLED_POPULATIONS_DIR, generate_columns_txt, load_populations
from ...core.models import GenomicRegion, ResearchIntent
from ...core.parallelism import Parallelism

logger = logging.getLogger(__name__)

__all__ = [
    "NextflowBackend",
    "NextflowParams",
    "intent_to_params",
    "write_extract_csv",
    "build_command",
]

# Where RFC-004 section 3 places the pipeline once the second port is merged.
DEFAULT_PIPELINE_PATH = "engines/nextflow/main.nf"

EXTRACT_CSV_HEADER = "region,chromosome,start,end,populations"

# Standard VCF fixed columns preceding the per-sample columns, shared by
# columns.txt and the worker scripts that read it.
_VCF_FIXED_FIELDS = ["#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT"]


@dataclass(frozen=True)
class NextflowParams:
    """Validated inputs to a Nextflow launch.

    `populations` and `dropped_populations` partition `intent.populations`
    against the bundled population files (`data/populations/`, exactly AFR,
    ALL, AMR, EAS, EUR, GBR, SAS). A population absent from that set is
    dropped rather than silently forwarded to the pipeline, and
    `dropped_populations` is how the drop is reported to the caller instead
    of disappearing.

    `regions` is `intent.regions` when given; otherwise one whole-chromosome
    region per entry in `intent.chromosomes`, using `CHROMOSOME_LENGTH_BP`
    for the end coordinate; otherwise empty (whole genome, no per-region
    scatter).
    """
    populations: list[str]
    dropped_populations: list[str]
    regions: list[GenomicRegion]


def intent_to_params(
    intent: ResearchIntent,
    populations_dir: Path = BUNDLED_POPULATIONS_DIR,
) -> NextflowParams:
    """Resolve a `ResearchIntent` into validated Nextflow launch inputs.

    Population validation preserves `intent.populations` order and drops
    duplicates. A population named in the intent but absent from the
    bundled population files is moved into `dropped_populations` and logged
    as a warning -- never silently discarded.
    """
    bundled = set(load_populations(populations_dir))

    populations: list[str] = []
    dropped: list[str] = []
    for pop in intent.populations:
        if pop in bundled:
            if pop not in populations:
                populations.append(pop)
        elif pop not in dropped:
            dropped.append(pop)

    if dropped:
        logger.warning(
            "Dropping population(s) %s: not among the bundled population "
            "files %s",
            dropped, sorted(bundled),
        )

    if intent.regions:
        regions = list(intent.regions)
    elif intent.chromosomes:
        regions = [
            GenomicRegion(
                name=f"chr{chrom}",
                chromosome=chrom,
                start=1,
                end=CHROMOSOME_LENGTH_BP.get(chrom, 250_000_000),
            )
            for chrom in intent.chromosomes
        ]
    else:
        regions = []

    return NextflowParams(
        populations=populations,
        dropped_populations=dropped,
        regions=regions,
    )


def write_extract_csv(params: NextflowParams) -> str:
    """Render extract.csv: one row per region to extract, each carrying the
    validated (post-drop) population set.

    Columns: `region,chromosome,start,end,populations`, with `populations`
    a `;`-joined list (a plain CSV field, needing no quoting, since none of
    the bundled population codes contain a comma or semicolon).
    """
    pop_field = ";".join(params.populations)
    lines = [EXTRACT_CSV_HEADER]
    for region in params.regions:
        lines.append(
            f"{region.name},{region.chromosome},{region.start},{region.end},{pop_field}"
        )
    return "\n".join(lines) + "\n"


def _placeholder_columns_txt(populations_dir: Path = BUNDLED_POPULATIONS_DIR) -> str:
    """A columns.txt header row over the full bundled cohort (the `ALL`
    population file), unfiltered by the intent's populations.

    `materialize` uses this only when no `data_csv` is available to read a
    real VCF `#CHROM` header from -- e.g. cost estimation before data
    extraction has run. Once a `data_csv` is available, `materialize`
    prefers `generate_columns_txt`, which narrows the individual count to
    the intent's populations instead of this full-cohort placeholder.
    """
    all_pop_path = populations_dir / "ALL"
    individuals = all_pop_path.read_text().split() if all_pop_path.exists() else []
    return "\t".join(_VCF_FIXED_FIELDS + individuals) + "\n"


def build_command(
    params: NextflowParams,
    resolution: Parallelism,
    pipeline_path: str = DEFAULT_PIPELINE_PATH,
) -> list[str]:
    """Render the `nextflow run` invocation.

    Binds the three parallelism dials exactly as RFC-004 section 2.1's table
    says the Nextflow port must, all three read from `resolution`
    (`recommend_parallelism`'s output) rather than recomputed here:

    - `ind_jobs` -> `--ind_jobs`, consumed at runtime by the scatter.
    - `max_parallelism` -> `--ind_max_forks`, `main.nf`'s `maxForks`, fixed
      at launch.
    - `est_peak_mb` -> `--task_mem`, the `memory` directive.
    """
    return [
        "nextflow", "run", pipeline_path,
        "--populations", ",".join(params.populations),
        "--extract_csv", "extract.csv",
        # main.nf declares this as params.columns; the name is part of the
        # pipeline's documented CLI, so the backend matches it rather than
        # introducing a second spelling.
        "--columns", "columns.txt",
        "--ind_jobs", str(resolution.ind_jobs),
        "--ind_max_forks", str(resolution.max_parallelism),
        "--task_mem", f"{resolution.est_peak_mb}MB",
    ]


class NextflowBackend:
    """`Backend` implementation that launches the Nextflow port."""

    name = "nextflow"
    skill_fragment = "nextflow.md"

    def reserve(self) -> EngineReserve:
        """Cores and host memory the Nextflow launch holds back.

        Unlike HyperFlow's engine/Redis/merge reserve, Nextflow's own
        coordination cost is the JVM head process running `main.nf` plus the
        Docker daemon that launches each task's container -- see
        `knowledge/backends/nextflow.md`.
        """
        return EngineReserve(
            cores=1,
            host_mb=1536,
            rationale=(
                "cores and host memory reserved for the Nextflow JVM head "
                "process and the Docker daemon launching each task container"
            ),
        )

    def materialize(
        self,
        intent,
        measurements,
        resolution: Parallelism,
        *,
        data_csv: Path | None = None,
    ) -> LaunchSpec:
        """Turn `intent` plus a resolved `resolution` into extract.csv,
        columns.txt, and the `nextflow run` command to launch them with.

        `measurements` is accepted for protocol conformance but unused here:
        `resolution` already carries everything `recommend_parallelism`
        computed from whatever measurements produced it, and this backend
        does not read raw variant/individual counts itself.

        `data_csv`, when given, points at the prepared `data.csv` (its VCF
        files alongside it) that `generate_columns_txt` reads the real
        `#CHROM` header from, so `columns.txt` carries only the individuals
        belonging to the intent's (post-drop) populations rather than the
        full bundled cohort. Absent it -- data not yet prepared -- columns.txt
        falls back to the full-cohort placeholder.
        """
        params = intent_to_params(intent)
        if data_csv is not None:
            columns_txt = generate_columns_txt(
                data_csv=data_csv,
                populations_dir=BUNDLED_POPULATIONS_DIR,
                population_filter=params.populations,
            )
        else:
            columns_txt = _placeholder_columns_txt()
        files = {
            "extract.csv": write_extract_csv(params),
            "columns.txt": columns_txt,
        }
        command = build_command(params, resolution)
        return LaunchSpec(files=files, command=command, env={})
