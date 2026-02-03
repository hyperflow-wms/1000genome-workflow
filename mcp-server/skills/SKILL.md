# 1000 Genomes Workflow Generation Skill

Generate HyperFlow workflows for analyzing genetic variation from the 1000 Genomes Project Phase 3 data.

## Quick Reference

| Parameter | Description | Default | Constraint |
|-----------|-------------|---------|------------|
| `individuals_per_job` | Rows per parallel task | 250 | **Must divide 250,000 evenly** |
| `name` | Workflow name | "1000genome" | Any string |
| `version` | Workflow version | "1.0.0" | Any string |

## Critical Constraint

**`individuals_per_job` must divide 250,000 evenly.**

Each VCF file has 250,000 rows. The workflow splits processing into parallel tasks, and the division must be exact.

### Valid Values

```
1, 2, 4, 5, 8, 10, 16, 20, 25, 40, 50, 100, 125, 200, 250,
500, 625, 1000, 1250, 2000, 2500, 5000, 6250, 10000, 12500,
25000, 50000, 62500, 125000, 250000
```

### Recommended Values

| User Intent | Value | Tasks/Chromosome | Use Case |
|-------------|-------|------------------|----------|
| Quick test | 50000 | 5 | Testing, development |
| Default | 250 | 1,000 | Production runs |
| High parallelism | 25 | 10,000 | HPC clusters |
| Single task | 250000 | 1 | Debugging |

## Parameter Extraction

When a user describes a workflow request, extract parameters as follows:

### Parallelism Hints

| User Says | Recommended `individuals_per_job` |
|-----------|-----------------------------------|
| "quick test", "fast", "small" | 50000 (5 tasks/chr) |
| "default", "normal" | 250 (1000 tasks/chr) |
| "parallel", "HPC", "cluster" | 25-50 (5000-10000 tasks/chr) |
| "single", "sequential" | 250000 (1 task/chr) |

### Invalid Values - Common Mistakes

These values do NOT divide 250,000 evenly and will fail:

- 3, 6, 7, 9, 11, 12, 13, 14, 15, 17, 18, 19...
- 100000 (leaves remainder)
- 30, 60, 70, 80, 90...

If user requests an invalid value, suggest the nearest valid alternative.

## Workflow Structure

The workflow processes VCF files through this pipeline:

```
VCF Input
    │
    ├─► individuals_0 ─┐
    ├─► individuals_1  │
    ├─► individuals_2  ├─► individuals_merge ─► sifting ─► mutation_overlap ─► frequency
    ├─► ...            │                                         ▲
    └─► individuals_N ─┘                                         │
                                                          populations
```

### Task Types

| Task | Description |
|------|-------------|
| `individuals` | Parse VCF, extract homozygous variants for a subset of rows |
| `individuals_merge` | Combine outputs from parallel individuals tasks |
| `sifting` | Filter variants using SIFT scores from annotation files |
| `mutation_overlap` | Calculate pairwise mutation sharing between individuals |
| `frequency` | Compute allele frequency distribution |
| `populations` | Load population sample lists |

### Task Count Formula

For `individuals_per_job = N` with C chromosomes:

```
individuals tasks:       C × (250000 / N)
individuals_merge tasks: C
sifting tasks:           C
mutation_overlap tasks:  C
frequency tasks:         C
populations tasks:       1
─────────────────────────────────────────
Total:                   C × (250000/N + 4) + 1
```

**Examples** (10 chromosomes):

| individuals_per_job | Total Tasks |
|---------------------|-------------|
| 250000 | 51 |
| 50000 | 91 |
| 250 | 10,041 |
| 25 | 100,041 |

## Example Requests

### Example 1: Quick Test
**User**: "Generate a quick test workflow"

**Parameters**:
```json
{
  "individuals_per_job": 50000,
  "name": "1000genome-test"
}
```

### Example 2: Production Run
**User**: "Create a workflow for production analysis"

**Parameters**:
```json
{
  "individuals_per_job": 250,
  "name": "1000genome-production"
}
```

### Example 3: Invalid Request
**User**: "Use 100 parallel jobs per chromosome"

**Response**: 100 is not a valid divisor of 250,000. Suggest 125 (2000 tasks/chr) or 100 (2500 tasks/chr).

Wait - 100 IS valid (250000/100 = 2500). Let me check: 250000 % 100 = 0. Yes, 100 is valid.

## Data Files

The workflow uses 10 chromosomes (chr1-chr10), each with:
- VCF file: `ALL.chr{N}.250000.vcf.gz` (~40-45MB each)
- Annotation file: `ALL.chr{N}...annotation.vcf.gz` (~90-170MB each)

## Scientific Purpose

This workflow creates a **null distribution** of mutational overlaps for statistical evaluation of disease-related mutations. It identifies homozygous variants (both alleles affected) and measures sharing patterns between individuals across populations.

## Attribution

HyperFlow port of the [Pegasus 1000genome-workflow](https://github.com/pegasus-isi/1000genome-workflow).

**Data citation**: 1000 Genomes Project Consortium. "A global reference for human genetic variation." *Nature* 526, 68-74 (2015).
