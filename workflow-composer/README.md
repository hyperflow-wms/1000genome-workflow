# Workflow Composer

Generate genomics workflow plans from natural language for the 1000 Genomes Project.

## Overview

The **workflow-composer** is an agent that transforms natural language research questions into executable workflow plans for scientific computing. It produces structured output suitable for human review and downstream execution agents.

**This component replaces the `mcp-server/` folder** and eliminates the external dependency on `daxgen.py + hflow-convert-dax`.

## Key Features

- **Dual-mode operation**: MCP server (chat) + CLI (standalone)
- **Native workflow generation**: Direct HyperFlow JSON output (no Pegasus dependency)
- **Human-in-the-loop**: Plans returned for review before execution
- **Structured contracts**: Pydantic models for validation and documentation
- **Format export**: HyperFlow and WfCommons formats supported

## Installation

```bash
# Basic installation
pip install -e .

# With MCP server support
pip install -e ".[mcp]"

# With LLM interpretation support
pip install -e ".[llm]"

# Full installation
pip install -e ".[all]"
```

## Usage

### Direct Workflow Generation (daxgen.py replacement)

```bash
# Generate HyperFlow workflow directly
g1kwf generate \
    --data-csv ../workflow-generator/data.csv \
    --populations-dir ../workflow-generator/data/populations \
    --ind-jobs 250 \
    -o workflow.json
```

### Natural Language Interface

```bash
# Generate workflow from research question (requires LLM deps)
g1kwf compose "Compare EUR vs AFR in HLA region"
```

### Structured Intent

```bash
# Generate from structured JSON
g1kwf plan '{"analysis_type": "population_comparison", "populations": ["EUR", "AFR"]}'
```

### List Available Options

```bash
# List known genomic regions
g1kwf regions

# List population codes
g1kwf populations
```

## MCP Server

For integration with Claude Desktop or other MCP-compatible clients:

```bash
python -m workflow_composer.mcp_server
```

## Task Count Formula

For **C** chromosomes, **P** populations, **J** ind_jobs per chromosome:

| Task Type | Count |
|-----------|-------|
| individuals | C × J |
| individuals_merge | C × 1 |
| sifting | C × 1 |
| mutation_overlap | C × P |
| frequency | C × P |
| **TOTAL** | **C × (J + 2 + 2P)** |

Example: C=10, P=7, J=250 → 10 × (250 + 2 + 14) = **2660 tasks**

## Architecture

```
workflow-composer/
├── src/workflow_composer/
│   ├── core/                  # Business logic
│   │   ├── generator.py       # ★ Native HyperFlow generation
│   │   ├── models.py          # Pydantic models
│   │   ├── planner.py         # Wraps generator + metadata
│   │   ├── data_resolver.py   # Data source selection
│   │   └── export.py          # Format converters
│   ├── interpretation/        # LLM layer (CLI mode)
│   │   ├── skill_loader.py    # Load skills as context
│   │   └── llm_interpreter.py # Research question → intent
│   ├── mcp_server.py          # MCP server interface
│   └── cli.py                 # CLI interface
├── skills/                    # Domain knowledge documents
├── tests/                     # Test suite
└── pyproject.toml
```

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_generator.py -v
```

## Known Differences from daxgen.py

The native generator produces **functionally equivalent** but not byte-identical output:

| Aspect | daxgen.py + hflow-convert-dax | Native Generator |
|--------|-------------------------------|------------------|
| File count | 2689 (one duplicate bug) | 2688 (correct) |
| Population order | Non-deterministic | Sorted alphabetically |

## Test Data vs Production Data

The workflow-composer generates two distinct outputs:

### 1. Workflow Structure (from test data)
The `workflow` field in the output uses `data.csv` which references **test data files** like `ALL.chr6.250000.vcf`. These are pre-processed files with exactly 250,000 rows each for workflow structure testing. The row counts are NOT genomic coordinates.

### 2. Data Preparation Plan (for production)
The `data_preparation` field describes how to obtain **real 1000 Genomes data** for production execution:

- **Full chromosome download**: For broad analyses, download complete VCF files
- **Tabix remote extraction**: For specific regions (e.g., HLA), use tabix to extract only relevant variants:
  ```
  tabix -h s3://1000genomes/.../ALL.chr6...vcf.gz 6:28477797-33448354 > chr6_hla.vcf.gz
  ```

When `use_remote_extraction: true`, the workflow should be run against the extracted VCF files rather than the test data. The data prep steps show the exact tabix commands/regions needed.

### Example Data Prep Output
```json
{
  "action": "extract_region",
  "source": "s3://1000genomes/release/20130502/ALL.chr6...vcf.gz",
  "region": "6:28477797-33448354",
  "output_file": "chr6_hla.vcf.gz"
}
```

## License

See repository LICENSE file.
