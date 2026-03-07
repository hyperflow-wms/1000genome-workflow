#!/bin/bash
# Generate evaluation dataset queries using Claude Code (Opus)
# Runs all 5 tiers in parallel, outputs to datasets/intent-extraction/
#
# Usage:
#   cd evaluation
#   bash scripts/generate_queries.sh
#
# Prerequisites:
#   - Claude Code CLI (claude) installed and authenticated
#   - Run from the evaluation/ directory

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EVAL_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$EVAL_DIR")"
OUTPUT_DIR="$EVAL_DIR/datasets/intent-extraction"

mkdir -p "$OUTPUT_DIR"

# Common context block included in every tier prompt
CONTEXT=$(cat <<'CONTEXT_EOF'
You are generating an evaluation dataset for a genomics workflow system.
The system extracts structured parameters from natural language research questions
about the 1000 Genomes Project.

## Valid Parameter Space

### Populations
Super-populations: AFR (African, 661), AMR (Ad Mixed American, 347), EAS (East Asian, 504), EUR (European, 503), SAS (South Asian, 489)

Sub-populations:
- AFR: YRI (Yoruba, Nigeria), LWK (Luhya, Kenya), GWD (Gambian), MSL (Mende, Sierra Leone), ESN (Esan, Nigeria), ASW (African SW USA), ACB (African Caribbean Barbados)
- EUR: CEU (Utah N/W European), TSI (Toscani Italy), FIN (Finnish), GBR (British), IBS (Iberian Spain)
- EAS: CHB (Han Chinese Beijing), JPT (Japanese Tokyo), CHS (Han Chinese South), CDX (Chinese Dai), KHV (Kinh Vietnam)
- SAS: GIH (Gujarati Houston), PJL (Punjabi Lahore), BEB (Bengali Bangladesh), STU (Sri Lankan Tamil UK), ITU (Indian Telugu UK)
- AMR: MXL (Mexican LA), PUR (Puerto Rican), CLM (Colombian Medellín), PEL (Peruvian Lima)

### Chromosomes: 1-22, X, Y

### Named Genomic Regions (GRCh37/hg19 coordinates)
- HLA: chr6:28477797-33448354 (immune function, autoimmune disease)
- BRCA1: chr17:43044295-43125483 (breast/ovarian cancer)
- BRCA2: chr13:32315086-32400266 (breast/ovarian cancer)
- APOE: chr19:44905796-44909393 (Alzheimer's, cardiovascular)
- CYP2D6: chr22:42518900-42528000 (drug metabolism)
- HBB: chr11:5225464-5229395 (sickle cell, thalassemia)
- CFTR: chr7:117120017-117308718 (cystic fibrosis)
- TP53: chr17:7668421-7687490 (cancer, Li-Fraumeni)

### Analysis Types: single_population, population_comparison, multi_population, region_analysis
### Focus: all_variants, deleterious, common, rare

### Disease-to-Region Mappings (for T3)
- autoimmune/transplant/type 1 diabetes/rheumatoid arthritis → HLA
- breast cancer/ovarian cancer → BRCA1, BRCA2
- Alzheimer's/dementia → APOE
- drug metabolism/pharmacogenomics → CYP2D6
- sickle cell/thalassemia/malaria resistance → HBB
- cystic fibrosis → CFTR
- Li-Fraumeni/tumor suppressor → TP53

## Output Format

Output ONLY valid YAML. Each query must follow this exact format:

  - id: {tier}_{number:02d}
    tier: {TIER}
    question: "the natural language question"
    ground_truth:
      analysis_type: <one of the four types>
      populations: [CODE1, CODE2]
      chromosomes: ["N"] or null
      regions: null or list of {name, chromosome, start, end}
      focus: <one of the four focus types>

## Diversity Requirements

- Cover ALL 5 super-populations at least twice
- Use at least 8 different sub-populations
- Use at least 5 of the 8 named regions
- Mix all 4 analysis types
- Mix all 4 focus values
- Don't repeat the same population pair more than twice
- Vary chromosome numbers (don't always use chr6 or chr22)
CONTEXT_EOF
)

# ---- T1: Explicit ----
echo "Generating T1 (explicit)..."
claude --print --model opus -p "$CONTEXT

## Tier: T1 — Explicit

Generate exactly 30 queries at tier T1. Rules:
- Questions use EXACT 1000 Genomes codes (EUR, AFR, GBR, chr6, BRCA1, etc.)
- No common English names — only codes
- The mapping from question to ground truth should be trivially obvious
- Use the id format: t1_01, t1_02, ..., t1_30

Examples:
  - \"Compare EUR and AFR on chromosome 21\" → populations: [EUR, AFR], chromosomes: [\"21\"]
  - \"Analyze rare variants in GBR in the BRCA1 region\" → populations: [GBR], regions: [{name: BRCA1, ...}], focus: rare

Output ONLY the YAML list of 30 entries (no markdown fences, no explanation)." > "$OUTPUT_DIR/tier_t1.yaml" &

# ---- T2: Synonym ----
echo "Generating T2 (synonym)..."
claude --print --model opus -p "$CONTEXT

## Tier: T2 — Synonym

Generate exactly 30 queries at tier T2. Rules:
- Questions use common English names instead of codes
- \"European\" not EUR, \"British\" not GBR, \"Japanese\" not JPT
- \"HLA region\" or \"BRCA1 gene\" are OK (these are common names too)
- The ground truth uses the correct codes
- Use the id format: t2_01, t2_02, ..., t2_30

Examples:
  - \"Compare European and African populations on chromosome 21\" → populations: [EUR, AFR]
  - \"Study common variants in British and Finnish people\" → populations: [GBR, FIN], focus: common

Output ONLY the YAML list of 30 entries (no markdown fences, no explanation)." > "$OUTPUT_DIR/tier_t2.yaml" &

# ---- T3: Implicit ----
echo "Generating T3 (implicit)..."
claude --print --model opus -p "$CONTEXT

## Tier: T3 — Implicit (requires domain inference)

Generate exactly 30 queries at tier T3. Rules:
- Questions mention diseases, research contexts, or vague groupings — NEVER codes or explicit region names
- \"autoimmune disease\" instead of \"HLA region\"
- \"continental groups\" instead of listing populations
- \"breast cancer risk genes\" instead of \"BRCA1 and BRCA2\"
- The system must infer the correct parameters from domain knowledge
- Use the id format: t3_01, t3_02, ..., t3_30

Examples:
  - \"Investigate autoimmune disease susceptibility across continental groups\" → populations: [AFR, AMR, EAS, EUR, SAS], regions: [{name: HLA, ...}]
  - \"Study sickle cell trait prevalence in West African communities\" → populations: [YRI, GWD, MSL, ESN], regions: [{name: HBB, ...}]

Output ONLY the YAML list of 30 entries (no markdown fences, no explanation)." > "$OUTPUT_DIR/tier_t3.yaml" &

# ---- T4: Underspecified ----
echo "Generating T4 (underspecified)..."
claude --print --model opus -p "$CONTEXT

## Tier: T4 — Underspecified (missing required parameters)

Generate exactly 30 queries at tier T4. Rules:
- Each question is deliberately MISSING something important
- Missing populations, missing scope, ambiguous analysis type, no region when one is implied, etc.
- Ground truth should contain what CAN be extracted, plus: clarification_needed: true
- Use the id format: t4_01, t4_02, ..., t4_30

Types of underspecification to cover (mix these):
- No population specified: \"Analyze variants in the HLA region\"
- No scope/chromosome: \"Look at genetic differences in Europeans\"
- Ambiguous grouping: \"Compare the main populations\" (which ones?)
- Missing analysis detail: \"Do something with chromosome 6 data\"
- Vague focus: \"Find interesting mutations in African populations\"

Additional ground truth field:
      clarification_needed: true

Examples:
  - question: \"Look at genetic differences in Europeans\"
    ground_truth:
      analysis_type: single_population
      populations: [EUR]
      chromosomes: null
      regions: null
      focus: all_variants
      clarification_needed: true

  - question: \"Analyze variants in the HLA region\"
    ground_truth:
      analysis_type: region_analysis
      populations: []
      chromosomes: null
      regions:
        - name: HLA
          chromosome: \"6\"
          start: 28477797
          end: 33448354
      focus: all_variants
      clarification_needed: true

Output ONLY the YAML list of 30 entries (no markdown fences, no explanation)." > "$OUTPUT_DIR/tier_t4.yaml" &

# ---- T5: Adversarial ----
echo "Generating T5 (adversarial)..."
claude --print --model opus -p "$CONTEXT

## Tier: T5 — Adversarial (misleading or invalid terms)

Generate exactly 30 queries at tier T5. Rules:
- Questions contain INVALID or MISLEADING terms mixed with valid ones
- The system should extract what is valid and flag/ignore what is invalid
- Ground truth populations should only contain VALID codes
- Use the id format: t5_01, t5_02, ..., t5_30

Types of adversarial inputs to cover (mix these):
- Non-existent population codes: \"Eastern European\", \"Northern African\", \"Central Asian\", \"Oceanian\"
- Invalid chromosomes: \"chromosome 25\", \"chromosome 0\", \"chromosome W\"
- Non-existent region names: \"FOXP2 region\", \"HGH gene\", \"insulin region\"
- Contradictions: \"Compare EUR and all European sub-populations\" (EUR IS the super-population)
- Misspellings of valid codes: \"EAAS\", \"Europian\", \"BRAC1\"
- Mixing valid and invalid: \"Compare EUR and Eastern European populations on chr21\"

Additional ground truth fields:
      clarification_needed: true
      invalid_terms:
        - term: \"the invalid term\"
          reason: \"why it is invalid\"

Examples:
  - question: \"Compare EUR and Eastern European populations on chromosome 21\"
    ground_truth:
      analysis_type: population_comparison
      populations: [EUR]
      chromosomes: [\"21\"]
      regions: null
      focus: all_variants
      clarification_needed: true
      invalid_terms:
        - term: \"Eastern European\"
          reason: \"Not a valid 1000G population or super-population code\"

  - question: \"Study the FOXP2 region in African and Asian populations\"
    ground_truth:
      analysis_type: region_analysis
      populations: [AFR, EAS]
      chromosomes: null
      regions: null
      focus: all_variants
      clarification_needed: true
      invalid_terms:
        - term: \"FOXP2 region\"
          reason: \"FOXP2 is not in the known genomic regions list\"

Output ONLY the YAML list of 30 entries (no markdown fences, no explanation)." > "$OUTPUT_DIR/tier_t5.yaml" &

# Wait for all background jobs
echo ""
echo "Waiting for all tiers to complete..."
wait

echo ""
echo "All tiers generated. Files:"
ls -la "$OUTPUT_DIR"/tier_t*.yaml

echo ""
echo "To merge into queries.yaml, run:"
echo "  python $SCRIPT_DIR/merge_tiers.py"
