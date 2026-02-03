# 1000genome Data Container

Docker image containing input data for the 1000genome workflow.

## Contents

The data image (`hyperflowwms/1000genome-data:1.0`) contains:

- **VCF files** (chr1-10): `ALL.chr{1-10}.250000.vcf.gz` (~425MB)
- **Annotation files** (chr1-10): `ALL.chr{1-10}...annotation.vcf.gz` (~1.2GB)
- **Population files**: Sample ID lists for AFR, ALL, AMR, EAS, EUR, GBR, SAS
- **workflow.json**: HyperFlow workflow definition (~2MB)
- **columns.txt**: Column definitions

Total image size: ~1.7GB

## Usage

Pull and run the data image to prepare input data:

```bash
# Pull the image
docker pull hyperflowwms/1000genome-data:1.0

# Prepare data in a local directory
docker run --rm -v $(pwd)/input-data:/mnt/data hyperflowwms/1000genome-data:1.0 sh /prepare_data.sh

# Or prepare data in a named volume
docker run --rm -v workflow-data:/mnt/data hyperflowwms/1000genome-data:1.0 sh /prepare_data.sh
```

## Data Sources

All data originates from the [1000 Genomes Project](https://www.internationalgenome.org/) Phase 3 release (20130502).

### VCF Files

The VCF files contain **250,000 rows** extracted from the original chromosome variant data. This subset was created specifically for the workflow to reduce data footprint while maintaining scientific validity.

Original full VCF files are available from the [1000 Genomes FTP](ftp://ftp.1000genomes.ebi.ac.uk/vol1/ftp/release/20130502/):
```
ALL.chr{N}.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
```

The 250k-row trimmed versions used in this workflow were originally created for the [Pegasus 1000genome-workflow](https://github.com/pegasus-isi/1000genome-workflow).

### Annotation Files

Functional annotation data from [1000 Genomes FTP - functional_annotation](ftp://ftp.1000genomes.ebi.ac.uk/vol1/ftp/release/20130502/supporting/functional_annotation/filtered/):
```
ALL.chr{N}.phase3_shapeit2_mvncall_integrated_v5.20130502.sites.annotation.vcf.gz
```

Used by the sifting task for SIFT score calculations.

### Population Files

Sample ID lists grouping individuals by population:
- **AFR** (661): African
- **AMR** (347): Admixed American
- **EAS** (504): East Asian
- **EUR** (503): European
- **SAS** (489): South Asian
- **GBR** (91): British (subset of EUR)
- **ALL** (2504): All populations

## Building the Image

The data files are stored only in the Docker image (not in git) due to size. To rebuild the image, you need access to the original data files or extract them from the existing Docker image.

```bash
make image    # Build image
make push     # Push to Docker Hub
```
