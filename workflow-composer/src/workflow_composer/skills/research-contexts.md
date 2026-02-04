# Research Contexts

When users describe research contexts without specific regions, use these mappings:

## Disease → Region Mappings

| Research Context | Suggested Regions | Rationale |
|------------------|-------------------|-----------|
| Autoimmune disease | HLA | MHC complex central to autoimmunity |
| Transplant rejection | HLA | HLA matching critical for transplant |
| Type 1 diabetes | HLA | Strong HLA association |
| Rheumatoid arthritis | HLA | HLA-DRB1 association |
| Cancer risk | BRCA1, BRCA2, TP53 | Common hereditary cancer genes |
| Breast cancer | BRCA1, BRCA2 | Well-established risk genes |
| Alzheimer's research | APOE | APOE ε4 is major risk factor |
| Drug response | CYP2D6 | Major drug metabolizing enzyme |
| Pharmacogenomics | CYP2D6 | Clinical pharmacogenomics |
| Sickle cell | HBB | Sickle cell mutation in HBB |
| Malaria resistance | HBB | Sickle cell trait protective |

## Analysis Type Inference

| User describes | Infer analysis_type |
|----------------|---------------------|
| "compare X and Y" | population_comparison |
| "X versus Y" | population_comparison |
| "differences between X and Y" | population_comparison |
| "in X population" | single_population |
| "across all populations" | multi_population |
| "in the HLA region" | region_analysis |

## Focus Inference

| User describes | Infer focus |
|----------------|-------------|
| harmful, deleterious, damaging, pathogenic | deleterious |
| common variants, frequent | common |
| rare variants, rare mutations | rare |
| all variants, comprehensive | all_variants |
