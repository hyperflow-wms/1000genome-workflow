# Data Sources

## 1000 Genomes Phase 3 Data Locations

### HTTPS (Default, works everywhere)
```
https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/release/20130502/
├── ALL.chr{1-22}.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
├── ALL.chrX.phase3_shapeit2_mvncall_integrated_v1b.20130502.genotypes.vcf.gz
├── ALL.chrY.phase3_integrated_v2a.20130502.genotypes.vcf.gz
├── ALL.chr{N}...vcf.gz.tbi  (tabix indexes)
└── integrated_call_samples_v3.20130502.ALL.panel
```

### AWS S3 (Use with compute_environment="aws")
```
s3://1000genomes/release/20130502/
```

### Google Cloud Storage (Use with compute_environment="gcp")
```
gs://genomics-public-data/ftp-trace.ncbi.nih.gov/1000genomes/ftp/release/20130502/
```

### Manual Data Access Patterns

#### Full Chromosome Download
Use when: Analyzing entire chromosome or multiple regions on same chromosome.
```bash
curl -LO https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/release/20130502/ALL.chr22.phase3_shapeit2_mvncall_integrated_v5b.20130502.genotypes.vcf.gz
```

#### Remote Region Extraction (tabix)
Use when: Analyzing specific region (HLA, BRCA, etc.). Reduces data transfer significantly.
```bash
tabix -h https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/release/20130502/ALL.chr6.phase3_shapeit2_mvncall_integrated_v5b.20130502.genotypes.vcf.gz 6:28477797-33448354 > region.vcf
```

#### Population Subsetting
After obtaining VCF (full or region), subset to specific populations:
```bash
bcftools view -S EUR.samples.list input.vcf.gz -Oz -o output_EUR.vcf.gz
```

## Data Transfer Estimates

| Data | Full Download | With tabix |
|------|---------------|------------|
| Chr22 | 205 MB | N/A |
| Chr6 | 943 MB | N/A |
| HLA region (chr6) | 943 MB | ~50 MB |
| BRCA1 region (chr17) | 436 MB | ~5 MB |

## Decision Logic

1. If specific region requested AND region is small (< 10% of chromosome):
   → Use tabix remote extraction

2. If whole chromosome or large region:
   → Download full VCF

3. Match data source to compute environment:
   - Default → HTTPS (works everywhere)
   - AWS EC2 → S3 (free egress within region, set compute_environment="aws")
   - GCP GCE → GCS (free egress within region, set compute_environment="gcp")
