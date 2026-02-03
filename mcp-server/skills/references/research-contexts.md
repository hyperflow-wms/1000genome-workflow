# Research Contexts

Scientific background and computational requirements for the 1000 Genomes workflow.

## Scientific Purpose

This workflow creates a **null distribution** of mutational overlaps for rigorous statistical evaluation of disease-related mutations.

### Key Concept: Null Distribution

When studying disease mutations, researchers need to know: "Is this pattern of mutation sharing statistically significant?"

The workflow answers this by:
1. Analyzing healthy individuals from diverse populations
2. Measuring how often mutations overlap between random pairs
3. Creating a baseline distribution for comparison

### Homozygous Variants

The workflow focuses on **homozygous variants** - mutations present on both copies of a chromosome. These have stronger effects because:
- No wild-type allele to compensate
- Full expression of variant phenotype
- More relevant for recessive disease studies

## Workflow Tasks Explained

### individuals
**Purpose**: Extract variant data for a subset of VCF rows

**Input**: VCF file, row range
**Output**: Per-individual variant lists

**What it does**:
- Parses VCF genotype data
- Identifies homozygous alternate genotypes (1/1)
- Extracts rsID numbers for each individual

### individuals_merge
**Purpose**: Combine parallel task outputs

**Input**: Multiple individuals task outputs
**Output**: Single merged file

### sifting
**Purpose**: Filter variants by predicted functional impact

**Input**: Merged variants, annotation VCF
**Output**: Filtered variants with SIFT scores

**SIFT Score Interpretation**:
- **< 0.05**: Deleterious (affects protein function)
- **≥ 0.05**: Tolerated (neutral)
- Lower scores = more harmful

### mutation_overlap
**Purpose**: Calculate pairwise sharing between individuals

**Input**: Sifted variants, population lists
**Output**: Overlap statistics per population

**Statistical approach**:
- Samples random pairs of individuals
- Counts shared homozygous variants
- Requires minimum 26 individuals per group

### frequency
**Purpose**: Compute allele frequency distribution

**Input**: Overlap results
**Output**: Frequency histograms

## Resource Estimates

### Memory Requirements

| individuals_per_job | Rows/Task | Memory/Task |
|---------------------|-----------|-------------|
| 250000 | 250,000 | ~6 GB |
| 50000 | 50,000 | ~4 GB |
| 250 | 250 | ~2 GB |
| 25 | 25 | ~1 GB |

### Runtime Estimates

For 10 chromosomes on a single machine:

| individuals_per_job | Tasks | Approx. Runtime |
|---------------------|-------|-----------------|
| 50000 | 91 | 10-20 minutes |
| 250 | 10,041 | 2-4 hours |
| 25 | 100,041 | 8-24 hours |

**Note**: Actual runtime depends heavily on:
- CPU cores available
- I/O speed
- HyperFlow executor configuration
- Max parallelism settings

### Data Size

| Component | Size |
|-----------|------|
| VCF files (10 chr) | ~425 MB |
| Annotation files (10 chr) | ~1.2 GB |
| Total input data | ~1.7 GB |
| Intermediate files | ~500 MB - 2 GB |
| Final outputs | ~50-100 MB |

## Common Analysis Patterns

### Quick Validation
Test workflow setup with minimal computation:
```json
{"individuals_per_job": 50000}
```
~5 tasks per chromosome, runs in minutes.

### Population Comparison
Compare mutation patterns between populations:
1. Run workflow with `individuals_per_job: 250`
2. Compare `mutation_overlap` outputs across populations
3. Statistical tests on frequency distributions

### Disease Variant Context
Establish null distribution for disease study:
1. Run full workflow (`individuals_per_job: 250`)
2. Use output as background distribution
3. Compare disease cohort overlap to null distribution

## Integration with HyperFlow

### Executor Configuration

The workflow uses HyperFlow's `redisCommand` function for job execution:

```yaml
HF_VAR_function: redisCommand
HF_VAR_REDIS_CMD_MAX_PARALLELISM: 20
```

Adjust `MAX_PARALLELISM` based on available resources.

### Running the Workflow

```bash
# Prepare data
docker run --rm -v $(pwd)/data:/mnt/data hyperflowwms/1000genome-data:1.0 sh /prepare_data.sh

# Run workflow
hflow run workflow.json
```

## References

1. 1000 Genomes Project Consortium. "A global reference for human genetic variation." *Nature* 526, 68-74 (2015).

2. Original Pegasus workflow: https://github.com/pegasus-isi/1000genome-workflow

3. SIFT algorithm: Ng PC, Henikoff S. "SIFT: Predicting amino acid changes that affect protein function." *Nucleic Acids Research* 31(13):3812-4 (2003).
