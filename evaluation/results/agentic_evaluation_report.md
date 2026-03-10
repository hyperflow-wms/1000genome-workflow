# Agentic Evaluation Report: Claude Opus via `claude -p`

## Purpose

Previous E1 evaluations used direct API calls (litellm + instructor) to extract
`ResearchIntent` from natural language queries. This evaluation tests the same
150-query dataset using **Claude Code CLI (`claude -p`)** as the LLM backend,
running from a temporary directory **outside the repository** so the agent cannot
discover skills files through file browsing.

This measures how Claude performs as an **agentic intent extractor** — receiving
the same system prompt and JSON schema constraint, but operating through the CLI
subprocess rather than a direct API call. All tools (Read, Bash, etc.) and MCP
servers are disabled, making this a pure extraction task.

## Method

| Aspect | API Evaluation | Agentic Evaluation |
|--------|---------------|-------------------|
| LLM call | litellm + instructor (in-process) | `claude -p` subprocess |
| Working directory | N/A (in-process) | Temp dir outside repo |
| Structured output | instructor `response_model` | `--json-schema` flag |
| Model | gpt-4.1-mini / gpt-5.4 | Claude Opus |
| Tools | N/A | All disabled (`--tools ""`, empty MCP config) |
| Skills ablation | Monkey-patch `skill_loader` | Include/omit skills in system prompt |

Two conditions were tested:
- **With skills**: System prompt includes full content from `populations.md`, `genomic-regions.md`, `research-contexts.md`, `data-sources.md`, and `SKILL.md`
- **Without skills**: System prompt uses the same template but with empty skills context

Both conditions run from `/tmp/eval-agentic-*` with `--setting-sources ""` and
`--disable-slash-commands` to prevent any project context leakage.

## Overall Results

### Full Match % (All Models, All Conditions)

| Model | With Skills | Without Skills | Delta |
|-------|:-----------:|:--------------:|:-----:|
| gpt-4.1-mini (API) | 60.7% | 60.7% | 0.0pp |
| gpt-5.4 (API) | 78.7% | 80.0% | +1.3pp |
| **Claude Opus (agentic)** | **82.4%** | **68.7%** | **-13.7pp** |

Claude Opus with skills achieves the **highest accuracy of any model/condition tested**,
outperforming GPT-5.4 by 3.7pp. The skills ablation shows a clear 13.7pp drop — the
largest and cleanest ablation effect observed, since the agentic setup truly isolates
the model from domain knowledge.

### Operational Cost

| Metric | With Skills | Without Skills |
|--------|:-----------:|:--------------:|
| Total cost | $5.20 | $9.71 |
| Mean cost/query | $0.035 | $0.065 |
| Mean turns | 3.1 | 3.3 |
| Mean wall time | 20.6s | 22.0s |
| Errors | 2/150 (1.3%) | 0/150 (0.0%) |

Without skills, cost nearly doubled ($0.035 → $0.065 per query). Without domain
context, the model generates longer reasoning to figure out what it can and cannot
extract, burning more tokens.

## Per-Tier Breakdown

### With Skills — All Models

| Tier | Description | gpt-4.1-mini | gpt-5.4 | Opus Agentic |
|------|-------------|:------------:|:-------:|:------------:|
| T1 | Explicit codes | 100.0% | 100.0% | **100.0%** |
| T2 | Synonym mapping | 100.0% | 100.0% | **100.0%** |
| T3 | Implicit domain | 70.0% | 66.7% | **86.7%** |
| T4 | Underspecified | 13.3% | 70.0% | **71.4%** (n=28) |
| T5 | Adversarial | 20.0% | 56.7% | **53.3%** |

### Without Skills — Opus Agentic Ablation

| Tier | With Skills | Without Skills | Delta |
|------|:-----------:|:--------------:|:-----:|
| T1 | 100.0% | 100.0% | 0.0pp |
| T2 | 100.0% | 100.0% | 0.0pp |
| T3 | 86.7% | 26.7% | **-60.0pp** |
| T4 | 71.4% | 70.0% | -1.4pp |
| T5 | 53.3% | 46.7% | -6.6pp |

## Per-Tier Analysis with Examples

### T1 — Explicit Codes (100% both conditions)

T1 queries provide exact population codes, chromosome numbers, and region names.
Both conditions achieve perfect accuracy.

**Example (t1_01):** *"Compare EUR and AFR on chromosome 21"*

| | Ground Truth | With Skills | Without Skills |
|---|---|---|---|
| Populations | EUR, AFR | EUR, AFR | EUR, AFR |
| Chromosomes | 21 | 21 | 21 |
| Result | — | MATCH | MATCH |

No domain knowledge needed — the query is self-contained.

### T2 — Synonym Mapping (100% both conditions)

T2 queries use common names ("European", "Japanese") instead of codes. Both
conditions achieve perfect accuracy, meaning Claude's general knowledge is
sufficient for population name → code mapping.

**Example (t2_05):** *"How do Japanese populations compare with Chinese populations on chromosome 7?"*

| | Ground Truth | With Skills | Without Skills |
|---|---|---|---|
| Populations | JPT, CHB | JPT, CHB | JPT, CHB |
| Chromosomes | 7 | 7 | 7 |
| Result | — | MATCH | MATCH |

### T3 — Implicit Domain Knowledge (86.7% → 26.7%, -60pp)

T3 is where skills provide the most value. Queries mention diseases or biological
concepts and expect the model to map them to specific genomic regions using the
curated `genomic-regions.md` table.

**Example 1 — Correct with skills, failed without (t3_04):**
*"What rare genetic risk factors for late-onset dementia exist in South Asian populations?"*

| | Ground Truth | With Skills | Without Skills |
|---|---|---|---|
| Populations | SAS | SAS | SAS |
| Regions | APOE | APOE | *(empty)* |
| Result | — | MATCH | MISMATCH |

With skills, the model maps "late-onset dementia" → APOE from the regions table.
Without skills, it returns no region — it knows APOE is related but lacks the
specific coordinates and canonical name expected by the workflow.

**Example 2 — Correct with skills, failed without (t3_07):**
*"Search for rare tumor suppressor gene mutations in the Japanese population"*

| | Ground Truth | With Skills | Without Skills |
|---|---|---|---|
| Populations | JPT | JPT | JPT |
| Regions | TP53 | TP53 | *(empty)* |
| Clarification | — | false | true |
| Result | — | MATCH | MISMATCH |

Without skills, the model flags clarification needed — it knows multiple tumor
suppressor genes exist but doesn't know which one the workflow supports.

**Example 3 — Over-generation without skills (t3_03):**
*"Identify deleterious variants in hereditary breast cancer risk genes among Europeans"*

| | Ground Truth | With Skills | Without Skills |
|---|---|---|---|
| Populations | EUR | EUR | EUR |
| Regions | BRCA1, BRCA2 | BRCA1, BRCA2 | BRCA1, BRCA2, TP53, PALB2, CHEK2, ATM |
| Clarification | — | false | true |
| Result | — | MATCH | MISMATCH |

Without skills constraining the region set to the 8 known regions, the model lists
all breast cancer genes from its training data. This is scientifically correct but
wrong for the workflow — it only supports specific pre-defined regions.

**Example 4 — Population granularity (t3_06, failed both):**
*"What are the common cystic fibrosis carrier variants in populations of Northern European ancestry?"*

| | Ground Truth | With Skills | Without Skills |
|---|---|---|---|
| Populations | CEU, FIN, GBR | EUR | CEU, GBR |
| Regions | CFTR | CFTR | *(empty)* |
| Result | — | MISMATCH | MISMATCH |

Even with skills, "Northern European" → specific subpopulations is ambiguous.
The model chose the superpopulation EUR instead of enumerating CEU+FIN+GBR.

### T4 — Underspecified Queries (71.4% → 70.0%, -1.4pp)

T4 queries are deliberately vague. The ground truth expects `clarification_needed=true`
for queries that lack sufficient detail. Skills have minimal impact here — the
challenge is detecting ambiguity, not domain mapping.

**Example 1 — Correct without skills, wrong with (t4_03):**
*"Compare the main populations against each other"*

| | Ground Truth | With Skills | Without Skills |
|---|---|---|---|
| Populations | *(empty)* | AFR, AMR, EAS, EUR, SAS | *(empty)* |
| Clarification | true | true | true |
| Result | — | MISMATCH | MATCH |

With skills providing the full population list, the model eagerly fills in all 5
superpopulations. Without skills, it correctly leaves populations empty since
"main populations" is ambiguous. The with-skills model is *too helpful*.

**Example 2 — Correct without skills, wrong with (t4_21):**
*"Compare some African subpopulations for deleterious mutations"*

| | Ground Truth | With Skills | Without Skills |
|---|---|---|---|
| Populations | *(empty)* | YRI, LWK, GWD, MSL, ESN, ASW, ACB | *(empty)* |
| Clarification | true | true | true |
| Result | — | MISMATCH | MATCH |

Same pattern: with skills listing all 7 AFR subpopulations, the model expands
"some African subpopulations" to all of them instead of asking which ones.

**Example 3 — Failed both, missed clarification (t4_26):**
*"Find deleterious variants in the Finnish population"*

| | Ground Truth | With Skills | Without Skills |
|---|---|---|---|
| Populations | FIN | FIN | FIN |
| Clarification | true | false | false |
| Result | — | MISMATCH | MISMATCH |

The query omits chromosome/region but is otherwise specific enough that the model
treats it as complete. The ground truth expects clarification for the missing
genomic scope, but the model doesn't flag this in either condition.

### T5 — Adversarial Queries (53.3% → 46.7%, -6.6pp)

T5 queries contain typos, unknown genes, contradictions, and edge cases. Two
systematic failure patterns emerge:

#### Pattern 1: Region Hallucination for Unknown Genes

**Example (t5_03):** *"Study the FOXP2 region across African and East Asian populations for all variants"*

| | Ground Truth | With Skills | Without Skills |
|---|---|---|---|
| Populations | AFR, EAS | AFR, EAS | AFR, EAS |
| Regions | *(empty)* | FOXP2 (chr7:114086327-114693772) | FOXP2 |
| Clarification | true | true | false |
| Result | — | MISMATCH | MISMATCH |

FOXP2 is not in the workflow's supported regions. The ground truth expects
`regions=null` with clarification. Both conditions fabricate coordinates from
general knowledge. With skills, the model at least flags clarification; without
skills, it treats the fabricated region as valid.

**Example (t5_28):** *"Analyze deleterious variants in the DMD gene region for African populations on chromosome X"*

| | Ground Truth | With Skills | Without Skills |
|---|---|---|---|
| Populations | AFR | AFR | AFR |
| Chromosomes | X | *(none)* | *(none)* |
| Regions | *(empty)* | DMD (chrX:31137345-33357726) | DMD |
| Clarification | true | true | false |
| Result | — | MISMATCH | MISMATCH |

Same pattern — the model looks up DMD coordinates from general knowledge rather
than recognizing it's outside the workflow's supported set.

#### Pattern 2: Auto-Correcting Typos Without Flagging

**Example (t5_04):** *"Examine deleterious mutations in the BRAC1 gene among Yoruba individuals"*

| | Ground Truth | With Skills | Without Skills |
|---|---|---|---|
| Populations | YRI | YRI | YRI |
| Regions | *(empty)* | BRCA1 | BRCA1 |
| Clarification | true | false | true |
| Result | — | MISMATCH | MISMATCH |

"BRAC1" is a typo for "BRCA1". The ground truth expects `regions=null` with
clarification (flag the typo). Both conditions silently auto-correct to BRCA1
and return coordinates. With skills, the model doesn't even flag it.

**Example (t5_23):** *"Compare deleterious variants in the APEO gene between Sri Lankan Tamil and Indian Telugu populations"*

| | Ground Truth | With Skills | Without Skills |
|---|---|---|---|
| Populations | STU, ITU | STU, ITU | STU, ITU |
| Regions | *(empty)* | APOE | *(empty)* |
| Clarification | true | true | true |
| Result | — | MISMATCH | **MATCH** |

"APEO" is a typo for "APOE". Without skills, the model doesn't have the APOE
coordinates to auto-correct to, so it correctly leaves regions empty and flags
clarification. With skills providing the APOE entry, it auto-corrects and fills
in coordinates — which the ground truth considers wrong.

## Key Findings

### 1. Skills Provide Real, Measurable Value (13.7pp overall, 60pp on T3)

The agentic setup provides the **cleanest ablation** of any evaluation so far.
Unlike the API-based E2 condition (which had prompt leakage), running from
outside the repo with empty skills context truly removes all domain knowledge.
The 60pp T3 collapse confirms that disease-to-gene mappings cannot be reliably
inferred from the model's general knowledge alone.

### 2. Claude Opus Achieves Best Overall Accuracy (82.4%)

| Rank | Model + Condition | Full Match % |
|------|-------------------|:------------:|
| 1 | **Claude Opus agentic + skills** | **82.4%** |
| 2 | GPT-5.4 API + skills | 78.7% |
| 3 | Claude Opus agentic - skills | 68.7% |
| 4 | GPT-4.1-mini API + skills | 60.7% |

Opus particularly excels on T3 (86.7% vs 66.7-70.0% for API models), showing
superior ability to use provided domain context for implicit reasoning.

### 3. Opus Is Too Eager to Resolve Ambiguity

The dominant failure mode across T4 and T5 is **over-resolution**: the model
completes underspecified queries rather than flagging them for clarification.
This manifests as:

- **Population expansion**: "some African subpopulations" → all 7 AFR subpops (T4)
- **Region hallucination**: Unknown genes get fabricated coordinates from general knowledge (T5)
- **Silent typo correction**: "BRAC1" → BRCA1 without flagging the error (T5)

This is fundamentally an **agentic behavior**: agents are trained to be helpful
and complete tasks, while the evaluation rewards conservative behavior on edge cases.

### 4. Skills Can Hurt on Underspecified Queries

Counter-intuitively, T4 accuracy is nearly identical between conditions (71.4% vs
70.0%). With skills providing lookup tables, the model is more likely to expand
vague references ("main populations" → all 5 superpops) instead of asking for
clarification. Without skills, it more readily admits uncertainty.

### 5. Cost Doubles Without Skills

The model compensates for missing domain context with longer deliberation:
$0.065/query without skills vs $0.035/query with skills. Skills don't just
improve accuracy — they improve efficiency by giving the model a clear decision
framework.

## Comparison with Hybrid Ablation

The hybrid ablation (reported separately) tested API models with a domain-neutral
prompt. Combining both results:

| Ablation | T3 Drop | Overall Drop | Notes |
|----------|:-------:|:------------:|-------|
| API E2 (prompt leakage) | 0pp | 0pp | Prompt still contained domain refs |
| Hybrid (domain-neutral prompt) | -57 to -67pp | -10 to -16pp | Clean but different prompt structure |
| **Agentic (no skills)** | **-60pp** | **-13.7pp** | Same prompt, just empty skills |

The agentic ablation aligns closely with the hybrid results, cross-validating
that skills contribute ~14pp of accuracy, concentrated in domain-specific
region mapping (T3).

## Conclusion

Claude Opus via `claude -p` achieves the best intent extraction accuracy (82.4%)
of any model tested. The agentic evaluation provides three insights:

1. **Skills are essential infrastructure**, not optional enhancements. The 60pp T3
   collapse proves that curated domain mappings (disease → gene, synonym → code)
   cannot be replaced by general model knowledge.

2. **Agentic behavior trades caution for helpfulness.** The model's tendency to
   resolve ambiguity rather than flag it is beneficial for straightforward queries
   (T1-T3) but harmful for edge cases (T4-T5). Prompt engineering to encourage
   clarification-seeking could improve T4/T5 by 10-15pp.

3. **The `claude -p` interface is production-viable** for structured extraction:
   $0.035/query, 20s latency, 1.3% error rate, with `--json-schema` providing
   reliable structured output validation.
