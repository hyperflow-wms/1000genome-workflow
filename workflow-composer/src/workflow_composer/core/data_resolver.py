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

    Note: Chromosome sizes here are approximate VCF file sizes in MB, not base pair lengths.
    We use the region size in Mb (megabases) compared to typical chromosome length (~250 Mb max).
    """
    if not regions:
        return False

    # Approximate chromosome lengths in megabases (for comparison, not file size)
    CHROMOSOME_LENGTH_MB = {
        "1": 249, "2": 243, "3": 198, "4": 191, "5": 182,
        "6": 171, "7": 159, "8": 146, "9": 141, "10": 136,
        "11": 135, "12": 134, "13": 115, "14": 107, "15": 102,
        "16": 90, "17": 83, "18": 80, "19": 59, "20": 64,
        "21": 47, "22": 51, "X": 156, "Y": 57,
    }

    for region in regions:
        chrom_length = CHROMOSOME_LENGTH_MB.get(region.chromosome, 150)
        region_size_mb = (region.end - region.start) / 1_000_000

        # If region is < 20% of chromosome, tabix extraction saves significant bandwidth
        if region_size_mb < chrom_length * 0.2:
            return True

    return False


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
    # Get chromosome length in bp for proportion calculation
    CHROM_LENGTH_BP = {
        "1": 249e6, "2": 243e6, "3": 198e6, "4": 191e6, "5": 182e6,
        "6": 171e6, "7": 159e6, "8": 146e6, "9": 141e6, "10": 136e6,
        "11": 135e6, "12": 134e6, "13": 115e6, "14": 107e6, "15": 102e6,
        "16": 90e6, "17": 83e6, "18": 80e6, "19": 59e6, "20": 64e6,
        "21": 47e6, "22": 51e6, "X": 156e6, "Y": 57e6,
    }
    chrom_length = CHROM_LENGTH_BP.get(region.chromosome, 150e6)
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
