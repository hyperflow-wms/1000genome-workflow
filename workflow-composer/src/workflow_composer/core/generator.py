"""
Native HyperFlow workflow generator for 1000genome.

This replaces: daxgen.py + hflow-convert-dax

CRITICAL: Output must be functionally equivalent to the original pipeline.
See implementation plan for validation strategy and known differences.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
import csv
import shutil

from .environment import ComputeEnvironment
from .parallelism import Parallelism, format_parallelism_reason, recommend_parallelism

logger = logging.getLogger(__name__)

# Bundled population files directory
BUNDLED_POPULATIONS_DIR = Path(__file__).parent.parent / "data" / "populations"


@dataclass
class ChromosomeData:
    """Data from one row of data.csv"""
    vcf_file: str        # e.g., "ALL.chr1.250000.vcf"
    row_count: int       # e.g., 250000
    annotation_file: str # e.g., "ALL.chr1...annotation.vcf"
    chromosome: str      # extracted, e.g., "1"


def parse_chromosome_number(vcf_filename: str) -> str:
    """Extract chromosome number from VCF filename.

    Exactly mirrors daxgen.py lines 104-106:
        c_num = base_file[base_file.find('chr')+3:]
        c_num = c_num[0:c_num.find('.')]
    """
    c_num = vcf_filename[vcf_filename.find('chr') + 3:]
    c_num = c_num[0:c_num.find('.')]
    return c_num


def load_data_csv(path: Path) -> list[ChromosomeData]:
    """Load chromosome data from data.csv"""
    chromosomes = []
    with open(path) as f:
        for row in csv.reader(f):
            if not row or not row[0].strip():
                continue
            vcf_file = row[0]
            chromosomes.append(ChromosomeData(
                vcf_file=vcf_file,
                row_count=int(row[1]),
                annotation_file=row[2],
                chromosome=parse_chromosome_number(vcf_file)
            ))
    return chromosomes


def load_populations(path: Path) -> list[str]:
    """Load population names from directory.

    Note: os.listdir order may vary — sort for determinism.
    """
    return sorted([f.name for f in path.iterdir() if f.is_file()])


def validate_ind_jobs(ind_jobs: int, threshold: int, vcf_file: str) -> int:
    """Validate ind_jobs constraint.

    Returns the effective ind_jobs value, clamped to threshold if needed.

    Note: We no longer require exact divisibility. The worker scripts handle
    partial ranges correctly via min(stop, total). The last task simply
    processes fewer rows if there's a remainder.

    Original daxgen.py required divisibility, but this was overly strict.
    """
    if threshold <= 0:
        raise ValueError(f"Row count must be positive, got {threshold} for {vcf_file}")

    if ind_jobs <= 0:
        raise ValueError(f"ind_jobs must be positive, got {ind_jobs}")

    ind_jobs = min(ind_jobs, threshold)
    return ind_jobs


def _resolve_clamp_environment(
    compute_environment: str | ComputeEnvironment | None,
    mem_budget_mb: int | None,
) -> ComputeEnvironment:
    """Resolve the environment ``clamp_ind_jobs`` clamps against.

    ``compute_environment`` may be a named profile ("local", "aws", "gcp"),
    an already-resolved ``ComputeEnvironment``, or ``None`` (defaults to
    "local", the reference profile the worked examples use). A separately
    supplied ``mem_budget_mb`` overrides the profile's default regardless of
    which form ``compute_environment`` took.
    """
    if isinstance(compute_environment, ComputeEnvironment):
        env = compute_environment
        if mem_budget_mb is not None:
            env = replace(env, mem_budget_mb=mem_budget_mb)
        return env

    overrides: dict[str, int] = {}
    if mem_budget_mb is not None:
        overrides["mem_budget_mb"] = mem_budget_mb
    return ComputeEnvironment.resolve(compute_environment or "local", **overrides)


def clamp_ind_jobs(
    ind_jobs_hint: int,
    row_count: int,
    individuals: int,
    env: ComputeEnvironment,
    chromosomes: int = 1,
) -> tuple[int, Parallelism]:
    """Clamp an ``ind_jobs`` hint to the memory-safe range for one chromosome.

    Whatever ``ind_jobs`` arrives -- agent-proposed, preset-derived, or CLI
    flag -- ``generate_workflow``
    treats it as a hint and clamps it to the range ``recommend_parallelism``
    computes, so an agent that ignores the prose guidance in ``SKILL.md``
    costs throughput but cannot cost a host.

    The clamp is per chromosome and uses that chromosome's *exact*
    ``row_count`` as V: the generator reads the real
    count from ``data.csv``, so unlike ``planner._estimate_max_variants_per_
    chromosome`` (which has to estimate and take the max across chromosomes
    touched by an intent) no estimate is needed here.

    Two-sided: never exceed the memory-safe ``ind_jobs`` from
    ``recommend_parallelism``, and never fall below 1. The pre-existing
    ``validate_ind_jobs`` threshold rule (never exceed ``row_count``) is
    applied separately, by the caller, exactly as before.

    Returns:
        A tuple of the clamped ``ind_jobs`` and the ``Parallelism`` computed
        for this chromosome, so the caller can report ``max_parallelism``,
        ``est_peak_mb``, and ``reason`` alongside it.
    """
    recommended = recommend_parallelism(
        variants=row_count,
        individuals=individuals,
        vcpus=env.vcpus,
        host_mem_mb=env.host_mem_mb,
        chromosomes=chromosomes,
        mem_budget_mb=env.mem_budget_mb,
        engine_reserve=env.engine_reserve,
        host_reserve_mb=env.host_reserve_mb,
    )
    clamped = max(1, min(ind_jobs_hint, recommended.ind_jobs))
    return clamped, recommended


class HyperFlowGenerator:
    """Generates HyperFlow workflow JSON directly.

    Replaces: Pegasus DAX generation + hflow-convert-dax conversion
    """

    def __init__(self):
        self.processes = []
        self.signals = []
        self.signal_map = {}  # filename -> signal_id
        self.next_signal_id = 0

        # Track workflow inputs/outputs
        self.workflow_ins = []
        self.workflow_outs = []

    def _get_or_create_signal(self, name: str, is_input: bool = False,
                              is_output: bool = False) -> int:
        """Get existing signal ID or create new signal.

        CRITICAL: This method ensures each unique filename gets exactly ONE signal ID.
        We use explicit membership check (name in self.signal_map) rather than
        truthiness check to avoid the falsy-value bug when signal_id is 0.

        The hflow-convert-dax tool has a bug where it uses:
            if (!dataNames[dataName]) { ... }
        which fails when dataNames[dataName] === 0 (first signal).
        We avoid this by using explicit 'in' check.
        """
        # CORRECT: explicit membership check (handles signal_id 0 correctly)
        if name in self.signal_map:
            signal_id = self.signal_map[name]
            # Update input/output tracking if needed
            if is_input and signal_id not in self.workflow_ins:
                self.workflow_ins.append(signal_id)
            if is_output and signal_id not in self.workflow_outs:
                self.workflow_outs.append(signal_id)
            return signal_id

        # Create new signal
        signal_id = self.next_signal_id
        self.next_signal_id += 1

        # Input signals need "data": [{}] to mark them as already available
        # This tells HyperFlow the file exists and doesn't need to be produced
        if is_input:
            self.signals.append({"data": [{}], "name": name})
        else:
            self.signals.append({"name": name})

        self.signal_map[name] = signal_id

        if is_input:
            self.workflow_ins.append(signal_id)
        if is_output:
            self.workflow_outs.append(signal_id)

        return signal_id

    def _add_process(self, name: str, executable: str, args: list,
                     ins: list[int], outs: list[int]):
        """Add a process (task) to the workflow."""
        self.processes.append({
            "name": name,
            "function": "{{function}}",
            "type": "dataflow",
            "firingLimit": 1,
            "ins": ins,
            "outs": outs,
            "config": {
                "executor": {
                    "executable": executable,
                    "args": args
                }
            }
        })

    def generate(
        self,
        chromosomes: list[ChromosomeData],
        populations: list[str],
        ind_jobs: int,
        name: str = "1000genome",
        version: str = "1.0.0",
        individuals: int | None = None,
        compute_environment: ComputeEnvironment | None = None,
    ) -> dict:
        """Generate complete HyperFlow workflow.

        Algorithm mirrors daxgen.py exactly.

        Args:
            individuals: I, the individual count used to clamp ``ind_jobs``
. ``None`` (the
                default) skips clamping entirely, preserving the exact
                pre-clamp behaviour -- ``ind_jobs`` is used as given, subject
                only to the pre-existing ``validate_ind_jobs`` threshold
                rule.
            compute_environment: resolved ``ComputeEnvironment`` to clamp
                against. Ignored when ``individuals`` is ``None``.
        """

        # Shared input: columns.txt
        columns_signal = self._get_or_create_signal("columns.txt", is_input=True)

        # Population file signals
        pop_signals = {}
        for pop in populations:
            pop_signals[pop] = self._get_or_create_signal(pop, is_input=True)

        # Track per-chromosome outputs for final analysis jobs
        c_nums = []
        individuals_merged_signals = []
        sifted_signals = []
        parallelism_metadata = []

        # ============================================================
        # PER-CHROMOSOME PROCESSING
        # (mirrors daxgen.py lines 84-162)
        # ============================================================

        for chrom in chromosomes:
            c_num = chrom.chromosome
            threshold = chrom.row_count

            # ind_jobs is a hint, trusted but clamped. When
            # a caller supplies individuals/compute_environment, clamp it to
            # the memory-safe range for this chromosome's exact row_count
            # before the pre-existing threshold clamp below ever sees it.
            ind_jobs_for_chrom = ind_jobs
            recommended: Parallelism | None = None
            if individuals is not None and compute_environment is not None:
                ind_jobs_for_chrom, recommended = clamp_ind_jobs(
                    ind_jobs,
                    threshold,
                    individuals,
                    compute_environment,
                    chromosomes=len(chromosomes),
                )

            # Validate and adjust ind_jobs (never exceed row_count)
            actual_ind_jobs = validate_ind_jobs(ind_jobs_for_chrom, threshold, chrom.vcf_file)

            if recommended is not None:
                # Emit both dials and the reason
                # for the *effective* (post-clamp) ind_jobs, and log both the
                # hint and the effective value when they differ -- never
                # just the hint, and never a reason naming a different
                # ind_jobs than the one that actually ran.
                reason = recommended.reason
                if actual_ind_jobs != recommended.ind_jobs:
                    # recommended.reason names recommended.ind_jobs (the
                    # memory/core-safe recommendation); the hint clamped
                    # tighter than that (or the pre-existing row_count cap
                    # bound), so actual_ind_jobs differs -- rebuild through
                    # the single format_parallelism_reason helper rather
                    # than let the reason state a value nothing ran with.
                    reason = format_parallelism_reason(
                        ind_jobs=actual_ind_jobs,
                        max_parallelism=recommended.max_parallelism,
                        binding=recommended.binding,
                        variants=threshold,
                        individuals=individuals,
                        cores=max(1, compute_environment.vcpus - compute_environment.engine_reserve),
                        est_peak_mb=recommended.est_peak_mb,
                    )
                if ind_jobs != actual_ind_jobs:
                    reason = f"{reason} [hint ind_jobs={ind_jobs} clamped to {actual_ind_jobs}]"
                logger.info("chr%s: %s", c_num, reason)
                parallelism_metadata.append({
                    "chromosome": c_num,
                    "ind_jobs_hint": ind_jobs,
                    "ind_jobs": actual_ind_jobs,
                    "max_parallelism": recommended.max_parallelism,
                    "est_peak_mb": recommended.est_peak_mb,
                    "binding": recommended.binding,
                    "reason": reason,
                })
            # Round the step up so ind_jobs tasks cover the whole file. Rounding
            # down leaves a remainder that spawns an extra task for a handful of
            # rows, and that task still scans the file up to its offset.
            step = -(-threshold // actual_ind_jobs)

            # VCF input signal (reused across all individuals jobs for this chromosome)
            vcf_signal = self._get_or_create_signal(chrom.vcf_file, is_input=True)

            # === INDIVIDUALS JOBS ===
            # (mirrors daxgen.py lines 108-124)
            individuals_output_signals = []
            counter = 1

            while counter < threshold:
                stop = counter + step

                out_name = f"chr{c_num}n-{counter}-{stop}.tar.gz"
                out_signal = self._get_or_create_signal(out_name)
                individuals_output_signals.append(out_signal)

                self._add_process(
                    name="individuals",
                    executable="individuals.py",
                    args=[chrom.vcf_file, c_num, str(counter), str(stop), str(threshold)],
                    ins=[vcf_signal, columns_signal],
                    outs=[out_signal]
                )

                counter = counter + step

            # === INDIVIDUALS MERGE ===
            # (mirrors daxgen.py lines 126-144)
            merged_name = f"chr{c_num}n.tar.gz"
            merged_signal = self._get_or_create_signal(merged_name)
            individuals_merged_signals.append(merged_signal)

            merge_args = [c_num] + [
                f"chr{c_num}n-{1 + i*step}-{1 + (i+1)*step}.tar.gz"
                for i in range(len(individuals_output_signals))
            ]

            self._add_process(
                name="individuals_merge",
                executable="individuals_merge.py",
                args=merge_args,
                ins=individuals_output_signals,
                outs=[merged_signal]
            )

            # === SIFTING ===
            # (mirrors daxgen.py lines 146-162)
            annotation_signal = self._get_or_create_signal(chrom.annotation_file, is_input=True)
            sifted_name = f"sifted.SIFT.chr{c_num}.txt"
            sifted_signal = self._get_or_create_signal(sifted_name)
            sifted_signals.append(sifted_signal)

            self._add_process(
                name="sifting",
                executable="sifting.py",
                args=[chrom.annotation_file, c_num],
                ins=[annotation_signal],
                outs=[sifted_signal]
            )

            c_nums.append(c_num)

        # ============================================================
        # ANALYSIS JOBS (per chromosome × per population)
        # (mirrors daxgen.py lines 164-195)
        #
        # IMPORTANT: mutation_overlap and frequency run IN PARALLEL
        # They have the SAME inputs:
        #   - individuals_merge output
        #   - sifting output
        #   - population file
        #   - columns.txt
        # ============================================================

        for i, c_num in enumerate(c_nums):
            for pop in populations:
                # Shared inputs for both mutation_overlap and frequency
                shared_ins = [
                    individuals_merged_signals[i],
                    sifted_signals[i],
                    pop_signals[pop],
                    columns_signal
                ]

                # === MUTATION OVERLAP ===
                mut_out_name = f"chr{c_num}-{pop}.tar.gz"
                mut_out_signal = self._get_or_create_signal(mut_out_name, is_output=True)

                self._add_process(
                    name="mutation_overlap",
                    executable="mutation_overlap.py",
                    args=["-c", c_num, "-pop", pop],
                    ins=shared_ins,
                    outs=[mut_out_signal]
                )

                # === FREQUENCY ===
                # NOTE: Runs in PARALLEL with mutation_overlap (same inputs)
                freq_out_name = f"chr{c_num}-{pop}-freq.tar.gz"
                freq_out_signal = self._get_or_create_signal(freq_out_name, is_output=True)

                self._add_process(
                    name="frequency",
                    executable="frequency.py",
                    args=["-c", c_num, "-pop", pop],
                    ins=shared_ins,  # Same inputs as mutation_overlap
                    outs=[freq_out_signal]
                )

        workflow = {
            "name": name,
            "version": version,
            "processes": self.processes,
            "signals": self.signals,
            "ins": self.workflow_ins,
            "outs": self.workflow_outs
        }

        # Return the effective dials alongside the
        # workflow rather than only logging them, so a caller cannot see one
        # dial without the other. Only added when clamping ran
        # (individuals/compute_environment supplied) -- otherwise the
        # workflow dict is byte-identical to the pre-clamp shape.
        if parallelism_metadata:
            workflow["metadata"] = {"parallelism": parallelism_metadata}

        return workflow


def generate_columns_txt(
    data_csv: Path,
    populations_dir: Path,
    population_filter: list[str] | None = None,
    max_samples_per_pop: int | None = None,
) -> str:
    """Generate columns.txt content filtered to requested populations.

    Reads the VCF header from the first VCF in data.csv to get all individual IDs,
    then filters to individuals belonging to the requested populations.

    Args:
        data_csv: Path to data.csv (VCF files must be in the same directory)
        populations_dir: Path to populations/ directory
        population_filter: If provided, only include these populations
        max_samples_per_pop: If provided, cap individuals per population

    Returns:
        columns.txt content as a string (single header line)
    """
    chromosomes = load_data_csv(data_csv)
    if not chromosomes:
        raise ValueError("No chromosomes found in data.csv")

    # Read VCF header from the first file to get individual IDs
    vcf_path = data_csv.parent / chromosomes[0].vcf_file
    header_line = None
    with open(vcf_path) as f:
        for line in f:
            if line.startswith("#CHROM"):
                header_line = line.rstrip("\n")
                break

    if header_line is None:
        raise ValueError(f"No #CHROM header found in {vcf_path}")

    fields = header_line.split("\t")
    vcf_fields = fields[:9]
    all_individuals = fields[9:]

    # Determine which populations to use
    populations = load_populations(populations_dir)
    if population_filter:
        populations = [p for p in population_filter if p in set(populations)]

    # Read population files and collect individuals
    selected = []
    for pop in populations:
        pop_path = populations_dir / pop
        if not pop_path.exists():
            continue
        pop_ids = set(pop_path.read_text().split())
        available = [ind for ind in all_individuals if ind in pop_ids]
        if max_samples_per_pop is not None:
            available = available[:max_samples_per_pop]
        selected.extend(available)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for s in selected:
        if s not in seen:
            seen.add(s)
            unique.append(s)

    return "\t".join(vcf_fields + unique) + "\n"


def copy_population_files(
    output_dir: Path,
    populations_dir: Path,
    population_filter: list[str] | None = None,
) -> list[str]:
    """Copy population files to the output directory.

    Args:
        output_dir: Destination directory
        populations_dir: Source populations/ directory
        population_filter: If provided, only copy these populations

    Returns:
        List of copied population names
    """
    populations = load_populations(populations_dir)
    if population_filter:
        populations = [p for p in population_filter if p in set(populations)]

    copied = []
    for pop in populations:
        src = populations_dir / pop
        if src.exists():
            shutil.copy2(src, output_dir / pop)
            copied.append(pop)
    return copied


def generate_workflow(
    data_csv: Path,
    populations_dir: Path,
    ind_jobs: int = 250,
    name: str = "1000genome",
    version: str = "1.0.0",
    chromosome_filter: list[str] | None = None,
    population_filter: list[str] | None = None,
    individuals: int | None = None,
    compute_environment: str | ComputeEnvironment | None = None,
    mem_budget_mb: int | None = None,
) -> dict:
    """Main entry point for workflow generation.

    Args:
        data_csv: Path to data.csv
        populations_dir: Path to populations/ directory
        ind_jobs: Number of individuals jobs per chromosome. Treated as a
            hint whenever
            ``individuals`` is supplied -- see below.
        name: Workflow name
        version: Workflow version
        chromosome_filter: If provided, only process these chromosomes (e.g., ["6"] for HLA)
        population_filter: If provided, only use these populations (e.g., ["EUR", "AFR"])
        individuals: I, the individual count after population filtering
            (e.g. the real ``columns.txt`` sample count). When given,
            ``ind_jobs`` is clamped per chromosome to the memory-safe range
            ``recommend_parallelism`` computes for that chromosome's exact
            ``row_count``. ``None`` (the default)
            skips clamping entirely -- ``ind_jobs`` is used exactly as given,
            subject only to the pre-existing ``validate_ind_jobs`` threshold
            rule, and the returned workflow carries no ``metadata`` key.
        compute_environment: named ``ComputeEnvironment`` profile ("local",
            "aws", "gcp") or an already-resolved ``ComputeEnvironment`` to
            clamp against. Ignored when ``individuals`` is ``None``; defaults
            to the "local" profile when ``individuals`` is given but this is
            not.
        mem_budget_mb: per-task memory ceiling overriding the resolved
            environment's default. Ignored when ``individuals`` is ``None``.

    Returns:
        HyperFlow workflow as dict (ready for json.dumps). When clamping ran
        (``individuals`` given), also carries
        ``workflow["metadata"]["parallelism"]``: one entry per chromosome
        with the effective ``ind_jobs``, ``max_parallelism``, ``est_peak_mb``,
        and ``reason`` -- returned together so a caller
        cannot see one dial without the other.
    """
    chromosomes = load_data_csv(data_csv)
    populations = load_populations(populations_dir)

    # Apply chromosome filter if specified
    if chromosome_filter:
        chromosomes = [c for c in chromosomes if c.chromosome in chromosome_filter]
        if not chromosomes:
            raise ValueError(
                f"No chromosomes match filter {chromosome_filter}. "
                f"Available: {[c.chromosome for c in load_data_csv(data_csv)]}"
            )

    # Apply population filter if specified
    if population_filter:
        available_pops = set(populations)
        filtered_pops = [p for p in population_filter if p in available_pops]
        if not filtered_pops:
            raise ValueError(
                f"No populations match filter {population_filter}. "
                f"Available: {populations}"
            )
        populations = filtered_pops

    resolved_env = None
    if individuals is not None:
        resolved_env = _resolve_clamp_environment(compute_environment, mem_budget_mb)

    generator = HyperFlowGenerator()
    return generator.generate(
        chromosomes=chromosomes,
        populations=populations,
        ind_jobs=ind_jobs,
        name=name,
        version=version,
        individuals=individuals,
        compute_environment=resolved_env,
    )
