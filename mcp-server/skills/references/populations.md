# Population Reference

The 1000 Genomes workflow analyzes genetic variation across 7 population groups.

## Available Populations

| Code | Name | Samples | Description |
|------|------|---------|-------------|
| **ALL** | All Populations | 2,504 | Complete dataset |
| **AFR** | African | 661 | Sub-Saharan African ancestry |
| **AMR** | Admixed American | 347 | Latino/Hispanic ancestry |
| **EAS** | East Asian | 504 | East Asian ancestry |
| **EUR** | European | 503 | European ancestry |
| **SAS** | South Asian | 489 | South Asian ancestry |
| **GBR** | British | 91 | British subset of EUR |

## Natural Language Mapping

When users describe populations in natural language:

| User Says | Population Code |
|-----------|-----------------|
| "all", "everyone", "complete" | ALL |
| "African", "Sub-Saharan", "Black" | AFR |
| "American", "Latino", "Hispanic", "Mexican" | AMR |
| "Asian", "East Asian", "Chinese", "Japanese", "Korean" | EAS |
| "European", "Western", "Caucasian", "White" | EUR |
| "South Asian", "Indian", "Pakistani", "Bengali" | SAS |
| "British", "UK", "English" | GBR |

## Sub-populations (Detail)

### AFR - African (661 samples)
- YRI: Yoruba in Ibadan, Nigeria (108)
- LWK: Luhya in Webuye, Kenya (99)
- GWD: Gambian in Western Division (113)
- MSL: Mende in Sierra Leone (85)
- ESN: Esan in Nigeria (99)
- ASW: African Ancestry in SW USA (61)
- ACB: African Caribbean in Barbados (96)

### EUR - European (503 samples)
- CEU: Utah residents, N/W European ancestry (99)
- TSI: Toscani in Italy (107)
- FIN: Finnish in Finland (99)
- GBR: British in England and Scotland (91)
- IBS: Iberian populations in Spain (107)

### EAS - East Asian (504 samples)
- CHB: Han Chinese in Beijing (103)
- JPT: Japanese in Tokyo (104)
- CHS: Han Chinese South (105)
- CDX: Chinese Dai in Xishuangbanna (93)
- KHV: Kinh in Ho Chi Minh City (99)

### SAS - South Asian (489 samples)
- GIH: Gujarati Indians in Houston (103)
- PJL: Punjabi in Lahore (96)
- BEB: Bengali in Bangladesh (86)
- STU: Sri Lankan Tamil in UK (102)
- ITU: Indian Telugu in UK (102)

### AMR - Admixed American (347 samples)
- MXL: Mexican Ancestry in LA (64)
- PUR: Puerto Rican in Puerto Rico (104)
- CLM: Colombian in Medellin (94)
- PEL: Peruvian in Lima (85)

## Research Context

### AFR
- Highest genetic diversity (ancestral population)
- Important for disease susceptibility studies
- Key for understanding human evolution

### EUR
- Most studied in GWAS
- Reference for pharmacogenomics
- Mendelian disease studies

### EAS
- Population-specific drug metabolism (ALDH2)
- Lactose tolerance studies
- Cancer predisposition research

### SAS
- Type 2 diabetes susceptibility
- Cardiovascular disease variants
- Metabolic syndrome research

### AMR
- Admixture studies
- Population history reconstruction
- Native American ancestry analysis

## Analysis Considerations

- **Homozygous focus**: The workflow identifies mutations where individuals have variants on **both alleles** (homozygous), which have stronger phenotypic effects.

- **Minimum sample size**: The `mutation_overlap` task requires at least 26 individuals per group for statistical validity.

- **GBR subset**: GBR is a subset of EUR, useful for UK-specific studies but may have reduced statistical power due to smaller sample size.
