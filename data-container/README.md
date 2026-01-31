# 1000genome Data Container

Docker image containing input data for the 1000genome workflow.

## Contents

The data image (`hyperflowwms/1000genome-data`) contains:

- **VCF files** (chr1-10): `ALL.chr{1-10}.250000.vcf.gz` (~425MB)
- **Annotation files** (chr1-10): `ALL.chr{1-10}...annotation.vcf.gz` (~1.2GB)
- **Population files**: Sample ID lists for AFR, ALL, AMR, EAS, EUR, GBR, SAS
- **workflow.json**: HyperFlow workflow definition (~2MB)
- **columns.txt**: Column definitions

Total image size: ~1.7GB (compressed)

## Usage

### Option 1: Use the Docker image (recommended)

Pull and run the data image to prepare input data:

```bash
# Pull the image
docker pull hyperflowwms/1000genome-data:1.0

# Prepare data in a local directory
docker run --rm -v $(pwd)/input-data:/mnt/data hyperflowwms/1000genome-data:1.0 sh /prepare_data.sh

# Or prepare data in a named volume
docker run --rm -v workflow-data:/mnt/data hyperflowwms/1000genome-data:1.0 sh /prepare_data.sh
```

### Option 2: Download annotation files manually

The VCF files and population files are included in the repository. Annotation files must be downloaded separately (~1.2GB):

```bash
cd data-container
./download_annotations.sh
```

Then build the image locally:

```bash
make image
```

## Building the image

If you have all data files locally:

```bash
make image    # Build image
make push     # Push to Docker Hub
```

## Data sources

All data originates from the [1000 Genomes Project](https://www.internationalgenome.org/) Phase 3 release (20130502):

- **VCF files**: Subsets of chromosome variant data (250,000 lines each) from the [1000 Genomes FTP](ftp://ftp.1000genomes.ebi.ac.uk/vol1/ftp/release/20130502/). These files contain SNP variants for 2,504 individuals across 26 populations.

- **Annotation files**: Functional annotation data from [1000 Genomes FTP - functional_annotation](ftp://ftp.1000genomes.ebi.ac.uk/vol1/ftp/release/20130502/supporting/functional_annotation/filtered/). Used by the sifting task for SIFT score calculations.

- **Population files**: Sample ID lists grouping individuals by population (AFR, AMR, EAS, EUR, SAS, GBR, ALL). Used by the frequency task.
