# Hybrid Ablation Report: Skills Value Assessment

## Purpose

The original E1 vs E2 comparison (with-skills vs without-skills) showed near-identical
results, suggesting skills add no value. However, the E2 "without skills" condition
still contained **prompt leakage**: the system prompt referenced "1000 Genomes codes",
specific gene names (BRCA1, TP53, CFTR, HLA), and "the genomic-regions.md table above"
even when skills content was removed.

The **hybrid condition** tests a truly domain-neutral prompt that removes all
1000 Genomes-specific references while keeping the same extraction structure and
format hints (e.g., "use standard 3-letter population codes", "use GRCh37/hg19 coordinates").

## Three Conditions

| Condition | System Prompt | Skills Content | Domain References |
|-----------|--------------|----------------|-------------------|
| **E1 (with_skills)** | Full 1000G prompt | Loaded from skills/ | Yes (populations.md, genomic-regions.md) |
| **E2 (without_skills)** | Full 1000G prompt | Empty string `""` | Yes (prompt still says "1000 Genomes codes") |
| **Hybrid** | Domain-neutral prompt | None | No (generic genomics language only) |

## Overall Results

| Model | E1 (with skills) | E2 (without skills) | Hybrid | E1-Hybrid Delta |
|-------|:-:|:-:|:-:|:-:|
| **gpt-4.1-mini** | 60.7% | 60.7% | 50.7% | **-10.0pp** |
| **gpt-5.4** | 78.7% | 80.0% | 62.7% | **-16.0pp** |

## Per-Tier Breakdown: Full Match %

### gpt-4.1-mini

| Tier | E1 | E2 | Hybrid | Description |
|------|:--:|:--:|:------:|-------------|
| T1 (explicit) | 100.0% | 100.0% | 86.7% | Exact codes given in query |
| T2 (synonyms) | 100.0% | 100.0% | 93.3% | Common name -> code mapping |
| T3 (implicit) | 70.0% | 70.0% | 3.3% | Disease -> gene inference |
| T4 (underspecified) | 13.3% | 13.3% | 66.7% | Vague queries needing clarification |
| T5 (adversarial) | 20.0% | 20.0% | 3.3% | Invalid terms, edge cases |

### gpt-5.4

| Tier | E1 | E2 | Hybrid | Description |
|------|:--:|:--:|:------:|-------------|
| T1 (explicit) | 100.0% | 100.0% | 96.7% | Exact codes given in query |
| T2 (synonyms) | 100.0% | 100.0% | 96.7% | Common name -> code mapping |
| T3 (implicit) | 66.7% | 66.7% | 10.0% | Disease -> gene inference |
| T4 (underspecified) | 70.0% | 73.3% | 70.0% | Vague queries needing clarification |
| T5 (adversarial) | 56.7% | 60.0% | 40.0% | Invalid terms, edge cases |

## Key Findings

### 1. T3 (Implicit Domain Knowledge) Collapsed

This is the most dramatic result. T3 queries mention diseases (e.g., "autoimmune disease",
"breast cancer", "late-onset dementia") and expect the model to map them to specific
genomic regions (HLA, BRCA1/BRCA2, APOE). With skills, the prompt includes a curated
`genomic-regions.md` table that provides these disease-to-gene mappings.

Without this table (hybrid), models either:
- **Miss the region entirely** (e.g., "autoimmune disease" -> no HLA region)
- **Over-generate regions** (e.g., "breast cancer genes" -> BRCA1, BRCA2, PALB2, TP53, ATM, CHEK2, CDH1, PTEN, STK11 instead of just BRCA1, BRCA2)

This proves skills provide **genuine domain-specific value** that cannot be replicated
by the model's general knowledge alone.

### 2. T1/T2 Slightly Degraded

Even simple explicit queries showed minor issues without skills:
- **"HLA region"** vs **"HLA"**: Without the skills table defining exact region names, the model returned "HLA region" instead of "HLA" (exact-match failure)
- **STU misidentified**: The population code "STU" (Sri Lankan Tamil) was misclassified as a genomic region by gpt-4.1-mini without domain context
- These are format/vocabulary issues that skills resolve by defining the canonical names

### 3. T4 (Underspecified) — Paradoxically Improved for gpt-4.1-mini

gpt-4.1-mini hybrid scored 66.7% on T4 vs 13.3% with skills. This happened because the
with-skills prompt encouraged the model to attempt full extraction (hallucinating
populations), while the hybrid prompt more readily flagged queries as needing clarification.
This suggests the skills prompt may be **too aggressive** about extraction for vague queries.

### 4. E1 vs E2 Similarity Explained

E1 and E2 produce nearly identical results (within 0-3.3pp) because the E2 condition
only removes the skills *content* while leaving all domain references in the prompt.
The model has enough information from the prompt text alone ("valid 1000 Genomes codes",
"BRCA1, TP53, CFTR, HLA" examples) to perform nearly as well. **This is prompt leakage,
not evidence that skills are valueless.**

## Side-by-Side Examples

### Example 1: T3 — Disease-to-Gene Mapping

**Query**: "Investigate how autoimmune disease susceptibility varies across all major continental groups"

| | Ground Truth | E1 (with skills) | Hybrid |
|---|---|---|---|
| Populations | AFR, AMR, EAS, EUR, SAS | AFR, AMR, EAS, EUR, SAS | AFR, AMR, EAS, EUR, SAS |
| Regions | HLA | HLA | *(empty)* |
| Match | -- | MATCH | MISMATCH |

Without the genomic-regions.md table mapping "autoimmune disease" to the HLA region,
both models failed to make this connection.

### Example 2: T3 — Over-Generation

**Query**: "Identify deleterious variants in hereditary breast cancer risk genes among Europeans"

| | Ground Truth | E1 (with skills) | Hybrid |
|---|---|---|---|
| Populations | EUR | EUR | EUR |
| Regions | BRCA1, BRCA2 | BRCA1, BRCA2 | BRCA1, BRCA2, PALB2, TP53 (mini) / +ATM, CDH1, CHEK2, PTEN, STK11 (5.4) |
| Match | -- | MATCH | MISMATCH |

Without skills constraining the region set, models listed all known breast cancer genes
from their training data rather than the specific ones available in the workflow.

### Example 3: T1 — Format Mismatch

**Query**: "Analyze common variants in GIH in the HLA region"

| | Ground Truth | E1 (with skills) | Hybrid |
|---|---|---|---|
| Populations | GIH | GIH | GIH |
| Regions | HLA | HLA | HLA region |
| Match | -- | MATCH | MISMATCH |

The skills define canonical region names. Without them, the model used "HLA region"
instead of "HLA", causing an exact-match failure.

### Example 4: T1 — Population Code Misidentified

**Query**: "Analyze deleterious variants in STU on chromosome 2"

| | Ground Truth | E1 (with skills) | Hybrid (gpt-4.1-mini) |
|---|---|---|---|
| Populations | STU | STU | *(empty)* |
| Chromosomes | 2 | 2 | *(empty)* |
| Regions | *(none)* | *(none)* | STU |
| Match | -- | MATCH | MISMATCH |

Without skills listing STU as a valid population code, gpt-4.1-mini misinterpreted
it as a genomic region.

## Conclusion

The hybrid ablation demonstrates that **skills provide real, measurable value** —
approximately 10-16 percentage points of accuracy. The original E1 vs E2 comparison
was confounded by prompt leakage, making skills appear unnecessary.

Skills contribute value in three ways:
1. **Domain mapping**: Disease -> gene/region associations (T3, biggest impact)
2. **Vocabulary normalization**: Canonical names for regions and populations (T1/T2)
3. **Scope constraint**: Limiting extraction to available workflow entities (prevents over-generation)

The E2 ablation design should be revised: either use the hybrid prompt for a clean
no-skills baseline, or remove domain-specific references from the system prompt
when skills are disabled.
