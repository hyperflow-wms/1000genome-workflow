# Data Sources

## 1000 Genomes Phase 3 Data Locations

### AWS S3 (Recommended for AWS compute)
```
s3://1000genomes/release/20130502/
├── ALL.chr{1-22}.phase3_shapeit2_mvncall_integrated_v5a.20130502.genotypes.vcf.gz
├── ALL.chrX.phase3_shapeit2_mvncall_integrated_v1b.20130502.genotypes.vcf.gz
├── ALL.chrY.phase3_integrated_v2a.20130502.genotypes.vcf.gz
├── ALL.chr{N}...vcf.gz.tbi  (tabix indexes)
└── integrated_call_samples_v3.20130502.ALL.panel
```

### Google Cloud Storage (Recommended for GCP compute)
```
gs://genomics-public-data/ftp-trace.ncbi.nih.gov/1000genomes/ftp/release/20130502/
```

### FTP (Fallback, rate-limited)
```
ftp://ftp.1000genomes.ebi.ac.uk/vol1/ftp/release/20130502/
```

## Data Extraction

The recommended way to extract data is using the `extract-data.sh` resource
(available via MCP resources). It takes a `plan.json` from `plan_workflow` and
handles all extraction automatically:

```bash
bash extract-data.sh --plan plan.json --output-dir /path/to/workdir
```

The script runs the tabix commands from the plan, builds `data.csv`, and prints
the `g1kwf generate` command to run next. It does NOT require `g1kwf` installed.

### Manual Data Access Patterns

#### Full Chromosome Download
Use when: Analyzing entire chromosome or multiple regions on same chromosome.
```bash
aws s3 cp s3://1000genomes/release/20130502/ALL.chr22...vcf.gz ./ --no-sign-request
```

#### Remote Region Extraction (tabix)
Use when: Analyzing specific region (HLA, BRCA, etc.). Reduces data transfer significantly.
```bash
tabix -h s3://1000genomes/release/20130502/ALL.chr6...vcf.gz 6:28477797-33448354 | bgzip > region.vcf.gz
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
   - AWS EC2 → S3 (free egress within region)
   - GCP GCE → GCS (free egress within region)
   - Local/HPC → FTP (or nearest cloud mirror)
