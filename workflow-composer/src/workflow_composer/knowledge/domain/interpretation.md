## Interpretation Guidelines

When a user describes their research, extract:

1. **Populations**: Look for population names or descriptions
   - "European" → EUR
   - "African ancestry" → AFR
   - "compare Europeans and Africans" → ["EUR", "AFR"] with analysis_type="population_comparison"

2. **Regions**: Look for gene names, disease contexts, or region names
   - "HLA region" → regions=[HLA]
   - "autoimmune disease" → regions=[HLA] (see research-contexts.md)
   - "breast cancer genes" → regions=[BRCA1, BRCA2]

3. **Focus**: Look for variant type descriptions
   - "harmful mutations" → focus="deleterious"
   - "common variants" → focus="common"

4. **Scale**: Determine appropriate scope
   - Specific region mentioned → use that region only
   - No region → whole chromosome or genome-wide

Do not fabricate data or make assumptions about results. Extract only what the
question supports; where a required parameter is missing or a term maps to no
valid code, say so through `clarification_needed` rather than guessing.
