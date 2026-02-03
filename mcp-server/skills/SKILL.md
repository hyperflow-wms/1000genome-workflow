# 1000 Genomes Workflow Generation Skill

Generate HyperFlow workflows for analyzing genetic variation from the 1000 Genomes Project Phase 3 data.

## Quick Reference

| Parameter | Description | Default | Constraint |
|-----------|-------------|---------|------------|
| `ind_jobs` | Number of parallel jobs per chromosome | 250 | **Must divide 250,000 evenly** |
| `name` | Workflow name | "1000genome" | Any string |
| `version` | Workflow version | "1.0.0" | Any string |

## Critical Constraint

**`ind_jobs` must divide 250,000 evenly.**

Each VCF file has 250,000 rows. The `ind_jobs` parameter controls how many parallel jobs process each chromosome. Higher values = more parallelism.

### Valid Values

```
1, 2, 4, 5, 8, 10, 16, 20, 25, 40, 50, 100, 125, 200, 250,
500, 625, 1000, 1250, 2000, 2500, 5000, 6250, 10000, 12500,
25000, 50000, 62500, 125000, 250000
```

### Recommended Values

| User Intent | ind_jobs | Jobs/Chromosome | Rows/Job | Use Case |
|-------------|----------|-----------------|----------|----------|
| Quick test | 5 | 5 | 50,000 | Testing, development |
| Default | 250 | 250 | 1,000 | Production runs |
| High parallelism | 2500 | 2,500 | 100 | HPC clusters |
| Single task | 1 | 1 | 250,000 | Debugging |

## Parameter Extraction

When a user describes a workflow request, extract parameters as follows:

### Parallelism Hints

| User Says | Recommended `ind_jobs` |
|-----------|------------------------|
| "quick test", "fast", "small" | 5 (5 jobs/chr) |
| "default", "normal", "production" | 250 (250 jobs/chr) |
| "parallel", "HPC", "cluster" | 2500-5000 (high parallelism) |
| "single", "sequential", "debug" | 1 (1 job/chr) |

### Invalid Values - Common Mistakes

These values do NOT divide 250,000 evenly and will fail:

- 3, 6, 7, 9, 11, 12, 13, 14, 15, 17, 18, 19...
- 30, 60, 70, 80, 90...
- 300, 600, 700...

If user requests an invalid value, suggest the nearest valid alternative.

## Workflow Structure

The workflow processes VCF files through this pipeline:

```
VCF Input
    |
    +-> individuals_0 -+
    +-> individuals_1  |
    +-> individuals_2  +-> individuals_merge -> sifting -+-> mutation_overlap (x7 populations)
    +-> ...            |                                 +-> frequency (x7 populations)
    +-> individuals_N -+
```

### Task Types

| Task | Count per Chromosome | Description |
|------|---------------------|-------------|
| `individuals` | ind_jobs | Parse VCF, extract homozygous variants |
| `individuals_merge` | 1 | Combine outputs from parallel individuals tasks |
| `sifting` | 1 | Filter variants using SIFT scores |
| `mutation_overlap` | 7 | Calculate pairwise sharing (one per population) |
| `frequency` | 7 | Compute allele frequency (one per population) |

### Task Count Formula

For `ind_jobs = N` with C chromosomes and P populations (P=7):

```
individuals tasks:       C x N
individuals_merge tasks: C
sifting tasks:           C
mutation_overlap tasks:  C x P
frequency tasks:         C x P
-----------------------------------------
Total:                   C x (N + 2 + 2P)
                       = C x (N + 16)
```

**Examples** (10 chromosomes, 7 populations):

| ind_jobs | individuals | merge | sifting | overlap | freq | Total |
|----------|-------------|-------|---------|---------|------|-------|
| 1 | 10 | 10 | 10 | 70 | 70 | 170 |
| 5 | 50 | 10 | 10 | 70 | 70 | 210 |
| 250 | 2,500 | 10 | 10 | 70 | 70 | 2,660 |
| 2500 | 25,000 | 10 | 10 | 70 | 70 | 25,160 |

## Example Requests

### Example 1: Quick Test
**User**: "Generate a quick test workflow"

**Parameters**:
```json
{
  "ind_jobs": 5,
  "name": "1000genome-test"
}
```

### Example 2: Production Run
**User**: "Create a workflow for production analysis"

**Parameters**:
```json
{
  "ind_jobs": 250,
  "name": "1000genome-production"
}
```

### Example 3: High Parallelism
**User**: "Generate a workflow for HPC cluster with maximum parallelism"

**Parameters**:
```json
{
  "ind_jobs": 5000,
  "name": "1000genome-hpc"
}
```

## Data Files

The workflow uses 10 chromosomes (chr1-chr10), each with:
- VCF file: `ALL.chr{N}.250000.vcf.gz` (~40-45MB each)
- Annotation file: `ALL.chr{N}...annotation.vcf.gz` (~90-170MB each)

## Populations

7 population groups are analyzed:
- **AFR** (661 samples): African
- **AMR** (347 samples): Admixed American
- **EAS** (504 samples): East Asian
- **EUR** (503 samples): European
- **SAS** (489 samples): South Asian
- **GBR** (91 samples): British (subset of EUR)
- **ALL** (2504 samples): All populations combined

## Scientific Purpose

This workflow creates a **null distribution** of mutational overlaps for statistical evaluation of disease-related mutations. It identifies homozygous variants (both alleles affected) and measures sharing patterns between individuals across populations.

## Attribution

HyperFlow port of the [Pegasus 1000genome-workflow](https://github.com/pegasus-isi/1000genome-workflow).

**Data citation**: 1000 Genomes Project Consortium. "A global reference for human genetic variation." *Nature* 526, 68-74 (2015).
