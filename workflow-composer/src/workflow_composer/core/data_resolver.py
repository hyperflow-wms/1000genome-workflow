"""
Data source resolution and preparation planning.
"""
from __future__ import annotations

from .models import (
    ResearchIntent,
    DataPreparationPlan,
    DataPrepStep,
    DataPrepAction,
    GenomicRegion
)

# Compressed VCF file sizes in MB (1000G Phase 3, .vcf.gz format)
# Source: ftp://ftp.1000genomes.ebi.ac.uk/vol1/ftp/release/20130502/
CHROMOSOME_VCF_SIZE_MB = {
    "1": 1100, "2": 1200, "3": 1000, "4": 950, "5": 900,
    "6": 850, "7": 800, "8": 750, "9": 600, "10": 700,
    "11": 700, "12": 650, "13": 480, "14": 450, "15": 420,
    "16": 460, "17": 400, "18": 380, "19": 320, "20": 320,
    "21": 190, "22": 190, "X": 500, "Y": 12,
}

# Variant counts per chromosome (1000G Phase 3)
# Source: 1000 Genomes Phase 3 release notes
# These are approximate counts for the full chromosome VCF files
CHROMOSOME_VARIANT_COUNT = {
    "1": 6_468_094, "2": 7_077_802, "3": 5_856_904, "4": 5_713_761, "5": 5_291_609,
    "6": 5_066_529, "7": 4_693_378, "8": 4_570_167, "9": 3_566_166, "10": 4_014_083,
    "11": 4_069_708, "12": 3_895_004, "13": 2_868_052, "14": 2_666_681, "15": 2_447_556,
    "16": 2_714_200, "17": 2_322_539, "18": 2_269_476, "19": 1_815_549, "20": 1_819_185,
    "21": 1_109_433, "22": 1_103_547, "X": 3_049_044, "Y": 62_042,
}

# Chromosome lengths in base pairs (GRCh37/hg19)
CHROMOSOME_LENGTH_BP = {
    "1": 249_250_621, "2": 243_199_373, "3": 198_022_430, "4": 191_154_276, "5": 180_915_260,
    "6": 171_115_067, "7": 159_138_663, "8": 146_364_022, "9": 141_213_431, "10": 135_534_747,
    "11": 135_006_516, "12": 133_851_895, "13": 115_169_878, "14": 107_349_540, "15": 102_531_392,
    "16": 90_354_753, "17": 81_195_210, "18": 78_077_248, "19": 59_128_983, "20": 63_025_520,
    "21": 48_129_895, "22": 51_304_566, "X": 155_270_560, "Y": 59_373_566,
}

# Known genomic regions
KNOWN_REGIONS = {
    "HLA": GenomicRegion(name="HLA", chromosome="6", start=28477797, end=33448354, context="immune function"),
    "BRCA1": GenomicRegion(name="BRCA1", chromosome="17", start=43044295, end=43125483, context="breast cancer"),
    "BRCA2": GenomicRegion(name="BRCA2", chromosome="13", start=32315086, end=32400266, context="breast cancer"),
    "APOE": GenomicRegion(name="APOE", chromosome="19", start=44905796, end=44909393, context="Alzheimer's"),
    "CYP2D6": GenomicRegion(name="CYP2D6", chromosome="22", start=42518900, end=42528000, context="pharmacogenomics"),
    "HBB": GenomicRegion(name="HBB", chromosome="11", start=5225464, end=5229395, context="sickle cell"),
    "CFTR": GenomicRegion(name="CFTR", chromosome="7", start=117120017, end=117308718, context="cystic fibrosis"),
    "TP53": GenomicRegion(name="TP53", chromosome="17", start=7668421, end=7687490, context="cancer"),
}

DATA_SOURCES = {
    "aws": {
        "type": "s3",
        "base_url": "s3://1000genomes/release/20130502",
        "supports_tabix": True
    },
    "gcp": {
        "type": "gcs",
        "base_url": "gs://genomics-public-data/ftp-trace.ncbi.nih.gov/1000genomes/ftp/release/20130502",
        "supports_tabix": True
    },
    "local": {
        "type": "ftp",
        "base_url": "ftp://ftp.1000genomes.ebi.ac.uk/vol1/ftp/release/20130502",
        "supports_tabix": True
    }
}


def resolve_region(region_name: str) -> GenomicRegion:
    """Resolve region name to coordinates."""
    if region_name.upper() in KNOWN_REGIONS:
        return KNOWN_REGIONS[region_name.upper()]
    raise ValueError(f"Unknown region: {region_name}. Known regions: {list(KNOWN_REGIONS.keys())}")


def should_use_remote_extraction(regions: list[GenomicRegion] | None, chromosomes: list[str]) -> bool:
    """Determine if tabix remote extraction is beneficial.

    Uses genomic region size as fraction of chromosome length to decide.
    If any specified region is < 20% of its chromosome, tabix extraction is worthwhile.
    """
    if not regions:
        return False

    for region in regions:
        chrom_length = CHROMOSOME_LENGTH_BP.get(region.chromosome, 150_000_000)
        region_size_bp = region.end - region.start

        # If region is < 20% of chromosome, tabix extraction saves significant bandwidth
        if region_size_bp < chrom_length * 0.2:
            return True

    return False


def estimate_variant_count(
    chromosome: str | None = None,
    region: GenomicRegion | None = None,
    safety_margin: float = 1.2
) -> int:
    """Estimate variant count for a chromosome or region.

    This is used for PLANNING ONLY. Actual workflow generation should use
    exact counts from scanned data files (deferred generation pattern).

    Args:
        chromosome: Chromosome number (e.g., "6", "22", "X")
        region: Specific genomic region
        safety_margin: Multiplier to ensure overestimation (default 1.2 = 20% buffer)

    Returns:
        Estimated variant count (intentionally overestimated for safety)

    Strategy:
    - Full chromosome: Use pre-computed counts from 1000G metadata
    - Known region: Scale by region's fraction of chromosome
    - Arbitrary region: Same scaling, with safety margin
    """
    if region:
        # Estimate based on region's fraction of chromosome
        chrom = region.chromosome
        chrom_variants = CHROMOSOME_VARIANT_COUNT.get(chrom, 3_000_000)
        chrom_length = CHROMOSOME_LENGTH_BP.get(chrom, 150_000_000)
        region_size = region.end - region.start

        # Base estimate: proportional to chromosome
        base_estimate = int((region_size / chrom_length) * chrom_variants)

        # Apply safety margin (overestimate is safe, underestimate loses data)
        return int(base_estimate * safety_margin)

    elif chromosome:
        # Use pre-computed count for full chromosome
        return CHROMOSOME_VARIANT_COUNT.get(chromosome, 3_000_000)

    else:
        raise ValueError("Must provide either chromosome or region")


def compute_optimal_ind_jobs(
    row_count: int,
    target: int | None = None,
    min_jobs: int = 1,
    max_jobs: int = 500
) -> int:
    """Compute optimal ind_jobs value for a given row count.

    Tries to find a value that divides row_count evenly for cleaner output,
    but the generator will work correctly even with non-divisible values.

    Args:
        row_count: Number of rows/variants to process
        target: Target parallelism (default: auto-select based on row_count)
        min_jobs: Minimum allowed jobs
        max_jobs: Maximum allowed jobs

    Returns:
        Recommended ind_jobs value
    """
    if row_count <= 0:
        return 1

    # Auto-select target based on row count magnitude
    if target is None:
        if row_count < 1_000:
            target = 10
        elif row_count < 50_000:
            target = 50
        elif row_count < 500_000:
            target = 100
        else:
            target = 250

    # Clamp to valid range
    target = max(min_jobs, min(target, max_jobs, row_count))

    # Try to find a nearby divisor for cleaner output
    # Search within ±20% of target
    search_range = max(1, target // 5)
    best = target
    best_remainder = row_count % target

    for offset in range(search_range + 1):
        for candidate in [target - offset, target + offset]:
            if min_jobs <= candidate <= min(max_jobs, row_count):
                remainder = row_count % candidate
                if remainder < best_remainder:
                    best = candidate
                    best_remainder = remainder
                    if remainder == 0:
                        return best  # Found exact divisor

    return best


def get_vcf_filename(chromosome: str) -> str:
    """Get VCF filename for chromosome."""
    if chromosome == "X":
        return "ALL.chrX.phase3_shapeit2_mvncall_integrated_v1b.20130502.genotypes.vcf.gz"
    elif chromosome == "Y":
        return "ALL.chrY.phase3_integrated_v2a.20130502.genotypes.vcf.gz"
    else:
        return f"ALL.chr{chromosome}.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz"


def estimate_region_size_mb(region: GenomicRegion) -> float:
    """Estimate compressed data transfer size for a region in MB.

    Uses chromosome's compressed VCF size scaled by region fraction.
    This gives a more accurate estimate than fixed bytes-per-position.
    """
    chrom_size_mb = CHROMOSOME_VCF_SIZE_MB.get(region.chromosome, 500)
    chrom_length = CHROMOSOME_LENGTH_BP.get(region.chromosome, 150_000_000)
    region_size_bp = region.end - region.start
    # Estimate: region's fraction of chromosome × compressed file size
    return (region_size_bp / chrom_length) * chrom_size_mb


def create_data_preparation_plan(
    intent: ResearchIntent,
    compute_environment: str = "aws"
) -> DataPreparationPlan:
    """Create data preparation plan from research intent."""

    source_config = DATA_SOURCES.get(compute_environment, DATA_SOURCES["aws"])
    steps = []
    total_transfer_mb = 0.0

    # Determine chromosomes
    if intent.regions:
        chromosomes = list(set(r.chromosome for r in intent.regions))
    elif intent.chromosomes:
        chromosomes = intent.chromosomes
    else:
        chromosomes = [str(i) for i in range(1, 23)]  # All autosomes

    use_remote = should_use_remote_extraction(intent.regions, chromosomes)

    for chrom in chromosomes:
        vcf_filename = get_vcf_filename(chrom)

        if use_remote and intent.regions:
            # Extract specific regions
            for region in intent.regions:
                if region.chromosome != chrom:
                    continue

                region_str = f"{chrom}:{region.start}-{region.end}"
                output_file = f"chr{chrom}_{region.name.lower()}.vcf.gz"

                steps.append(DataPrepStep(
                    action=DataPrepAction.EXTRACT_REGION,
                    source=f"{source_config['base_url']}/{vcf_filename}",
                    region=region_str,
                    output_file=output_file
                ))
                total_transfer_mb += estimate_region_size_mb(region)
        else:
            # Download full chromosome
            output_file = f"chr{chrom}.vcf.gz"
            steps.append(DataPrepStep(
                action=DataPrepAction.DOWNLOAD,
                source=f"{source_config['base_url']}/{vcf_filename}",
                output_file=output_file
            ))
            total_transfer_mb += CHROMOSOME_VCF_SIZE_MB.get(chrom, 500)

    # Add population subsetting steps if needed
    if intent.populations and len(intent.populations) < 5:  # Only subset if not analyzing all
        base_vcf = steps[-1].output_file if steps else "input.vcf.gz"
        for population in intent.populations:
            steps.append(DataPrepStep(
                action=DataPrepAction.SUBSET_POPULATION,
                input_file=base_vcf,
                population=population,
                output_file=f"{base_vcf.replace('.vcf.gz', '')}_{population}.vcf.gz"
            ))

    return DataPreparationPlan(
        source_type=source_config["type"],
        base_url=source_config["base_url"],
        steps=steps,
        estimated_transfer_mb=total_transfer_mb,
        use_remote_extraction=use_remote
    )
