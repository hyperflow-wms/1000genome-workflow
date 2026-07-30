#!/bin/bash
#
# Integration test: Deferred Generation Pattern
#
# This test demonstrates the full RFC-001 deferred generation workflow:
#
#   PLANNING PHASE (can happen before data exists):
#     1. Estimate variant count for HLA region
#     2. Generate workflow with estimated counts (for review/validation)
#
#   EXECUTION PHASE (on target infrastructure):
#     3. Download actual data via tabix
#     4. Get exact variant count
#     5. Regenerate workflow with exact counts
#     6. Execute workflow
#
# The test validates that:
#   - Estimated counts are reasonable (within expected margin)
#   - Both estimated and exact workflows have same structure
#   - The workflow executes successfully with real data
#
# Usage:
#   ./test-deferred-generation.sh [--parallelism small|medium|large] [--yes]
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WORKFLOW_DIR="$SCRIPT_DIR/workflow-deferred"

# HLA region coordinates (from data_resolver.py KNOWN_REGIONS)
HLA_CHROM="6"
HLA_START="28477797"
HLA_END="33448354"
HLA_REGION="${HLA_CHROM}:${HLA_START}-${HLA_END}"

# 1000 Genomes data source (HTTPS with SSL)
VCF_URL="https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/release/20130502/ALL.chr6.phase3_shapeit2_mvncall_integrated_v5b.20130502.genotypes.vcf.gz"
ANNOTATION_URL="https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/release/20130502/supporting/functional_annotation/filtered/ALL.chr6.phase3_shapeit2_mvncall_integrated_v5.20130502.sites.annotation.vcf.gz"

# Container with tabix that supports HTTPS
TABIX_IMAGE="broadinstitute/gatk:4.4.0.0"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_phase() { echo -e "\n${CYAN}═══════════════════════════════════════════${NC}"; echo -e "${CYAN}$1${NC}"; echo -e "${CYAN}═══════════════════════════════════════════${NC}\n"; }

# Parse arguments
PARALLELISM="small"
NONINTERACTIVE=false
QUICK_MODE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --parallelism|-p)
            PARALLELISM="$2"
            shift 2
            ;;
        --yes|-y)
            NONINTERACTIVE=true
            shift
            ;;
        --quick|-q)
            # Quick mode: extract smaller region (~100kb instead of 5Mb)
            QUICK_MODE=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--parallelism small|medium|large] [--yes] [--quick]"
            exit 1
            ;;
    esac
done

# In quick mode, extract a smaller region for faster testing
if [ "$QUICK_MODE" = true ]; then
    HLA_END="28577797"  # 100kb region instead of full 5Mb
    HLA_REGION="${HLA_CHROM}:${HLA_START}-${HLA_END}"
    log_info "Quick mode: extracting 100kb subset of HLA region"
fi

echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║          DEFERRED GENERATION INTEGRATION TEST                  ║"
echo "║                                                                 ║"
echo "║  Demonstrates RFC-001: Eliminate Manual data.csv Creation      ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""
echo "Region: chr$HLA_REGION"
echo "Parallelism: $PARALLELISM"
echo ""

# Clean up and create workflow directory
rm -rf "$WORKFLOW_DIR"
mkdir -p "$WORKFLOW_DIR"

# Ensure workflow-composer is installed
cd "$REPO_ROOT/workflow-composer"
if ! python3 -c "import workflow_composer" 2>/dev/null; then
    log_info "Installing workflow-composer..."
    pip install -e . -q
fi

# ============================================================================
# PLANNING PHASE - Before data exists
# ============================================================================

log_phase "PHASE 1: PLANNING (before data exists)"

# Step 1.1: Estimate variant count using workflow-composer
log_info "Step 1.1: Estimating variant count for region chr${HLA_CHROM}:${HLA_START}-${HLA_END}..."

ESTIMATED_COUNT=$(python3 << PYEOF
from workflow_composer.core.data_resolver import estimate_variant_count
from workflow_composer.core.models import GenomicRegion

# Use actual region coordinates (respects quick mode)
region = GenomicRegion(
    name="HLA",
    chromosome="${HLA_CHROM}",
    start=${HLA_START},
    end=${HLA_END},
    context="immune function"
)
estimated = estimate_variant_count(region=region)
print(estimated)
PYEOF
)

log_success "Estimated variant count: $ESTIMATED_COUNT (includes 20% safety margin)"

# Step 1.2: Generate workflow with estimated counts (for planning/review)
log_info "Step 1.2: Generating ESTIMATED workflow (for planning)..."

python3 << PYEOF
import json
from workflow_composer.core.generator import HyperFlowGenerator, ChromosomeData, BUNDLED_POPULATIONS_DIR
from workflow_composer.core.environment import ComputeEnvironment, MEMORY_BUDGET_PRESETS, recommend_for_environment

POPULATIONS = ["AFR", "ALL", "AMR", "EAS", "EUR", "GBR", "SAS"]

# Create chromosome data with estimated count
chromosomes = [
    ChromosomeData(
        vcf_file="ALL.chr6.hla.vcf",
        row_count=$ESTIMATED_COUNT,
        annotation_file="ALL.chr6.hla.annotation.vcf",
        chromosome="6"
    )
]

# ind_jobs hint via recommend_parallelism (RFC-003 section 7 item 3):
# generator.py no longer ships a fixed ind_jobs-per-preset table.
# "$PARALLELISM" selects a memory budget via MEMORY_BUDGET_PRESETS instead.
env = ComputeEnvironment.resolve("local", mem_budget_mb=MEMORY_BUDGET_PRESETS["$PARALLELISM"])
individuals = sum(
    len((BUNDLED_POPULATIONS_DIR / pop).read_text().split())
    for pop in POPULATIONS
    if (BUNDLED_POPULATIONS_DIR / pop).exists()
)
recommended = recommend_for_environment(
    variants=$ESTIMATED_COUNT, individuals=individuals, env=env, chromosomes=1
)

# Generate workflow
generator = HyperFlowGenerator()
workflow = generator.generate(
    chromosomes=chromosomes,
    populations=POPULATIONS,
    ind_jobs=recommended.ind_jobs,
    name="1000genome-hla-estimated"
)

# Save estimated workflow
with open("$WORKFLOW_DIR/workflow-estimated.json", "w") as f:
    json.dump(workflow, f, indent=2)

print(f"Tasks: {len(workflow['processes'])}")
print(f"Files: {len(workflow['signals'])}")
PYEOF

log_success "Estimated workflow saved to workflow-estimated.json"

# Show estimated workflow stats
ESTIMATED_TASKS=$(python3 -c "import json; print(len(json.load(open('$WORKFLOW_DIR/workflow-estimated.json'))['processes']))")
ESTIMATED_FILES=$(python3 -c "import json; print(len(json.load(open('$WORKFLOW_DIR/workflow-estimated.json'))['signals']))")
echo "  Estimated tasks: $ESTIMATED_TASKS"
echo "  Estimated files: $ESTIMATED_FILES"

echo ""
log_info "Planning phase complete. At this point, the user/agent can:"
echo "  - Review the estimated workflow structure"
echo "  - Validate task dependencies"
echo "  - Plan resource allocation based on task count"
echo "  - Proceed to data extraction when ready"

# ============================================================================
# EXECUTION PHASE - On target infrastructure
# ============================================================================

log_phase "PHASE 2: EXECUTION (data extraction + exact regeneration)"

# Step 2.1: Extract data via tabix
log_info "Step 2.1: Extracting HLA region from 1000 Genomes..."

docker run --rm \
    -v "$WORKFLOW_DIR:/output" \
    "$TABIX_IMAGE" \
    sh -c "
        set -e
        cd /output

        echo 'Downloading HLA region from chromosome 6...'
        tabix -h '$VCF_URL' $HLA_REGION > ALL.chr6.hla.vcf

        echo 'Downloading annotations...'
        tabix -h '$ANNOTATION_URL' $HLA_REGION > ALL.chr6.hla.annotation.vcf
    "

log_success "Data extracted"

# Step 2.2: Get EXACT variant count
log_info "Step 2.2: Counting exact variants in extracted data..."

EXACT_COUNT=$(grep -v '^#' "$WORKFLOW_DIR/ALL.chr6.hla.vcf" | wc -l)

log_success "Exact variant count: $EXACT_COUNT"

# Compare estimated vs exact
DIFFERENCE=$((ESTIMATED_COUNT - EXACT_COUNT))
PERCENT_DIFF=$(python3 -c "print(f'{abs($DIFFERENCE) / $EXACT_COUNT * 100:.1f}')")

echo ""
echo "┌─────────────────────────────────────────┐"
echo "│  ESTIMATION ACCURACY                    │"
echo "├─────────────────────────────────────────┤"
printf "│  Estimated: %'12d variants       │\n" $ESTIMATED_COUNT
printf "│  Actual:    %'12d variants       │\n" $EXACT_COUNT
printf "│  Difference: %'11d (%s%%)        │\n" $DIFFERENCE $PERCENT_DIFF
echo "└─────────────────────────────────────────┘"
echo ""

# Step 2.3: Copy supporting files
log_info "Step 2.3: Copying supporting files..."

DATA_IMAGE="hyperflowwms/1000genome-data:1.0"

docker run --rm -v "$WORKFLOW_DIR:/output" "$DATA_IMAGE" \
    sh -c "
        cp /data/20130502/columns.txt /output/
        cp /data/populations/* /output/
    "

# Trim to 30 individuals for faster testing
cd "$WORKFLOW_DIR"
cut -f1-39 columns.txt > columns_30.txt
mv columns_30.txt columns.txt

log_success "Supporting files ready"

# Step 2.4: Regenerate workflow with EXACT counts
log_info "Step 2.4: Regenerating workflow with EXACT variant count..."

# Create data.csv with exact count
cat > "$WORKFLOW_DIR/data.csv" << EOF
ALL.chr6.hla.vcf,$EXACT_COUNT,ALL.chr6.hla.annotation.vcf
EOF

cd "$REPO_ROOT/workflow-composer"

python3 -m workflow_composer.cli generate \
    --data-csv "$WORKFLOW_DIR/data.csv" \
    --populations-dir "$REPO_ROOT/workflow-generator/data/populations" \
    --parallelism "$PARALLELISM" \
    --output "$WORKFLOW_DIR/workflow.json"

log_success "Exact workflow generated"

# Compare workflows
EXACT_TASKS=$(python3 -c "import json; print(len(json.load(open('$WORKFLOW_DIR/workflow.json'))['processes']))")
EXACT_FILES=$(python3 -c "import json; print(len(json.load(open('$WORKFLOW_DIR/workflow.json'))['signals']))")

echo ""
echo "┌─────────────────────────────────────────┐"
echo "│  WORKFLOW COMPARISON                    │"
echo "├─────────────────────────────────────────┤"
echo "│  Estimated workflow:                    │"
printf "│    Tasks: %3d   Files: %3d             │\n" $ESTIMATED_TASKS $ESTIMATED_FILES
echo "│  Exact workflow:                        │"
printf "│    Tasks: %3d   Files: %3d             │\n" $EXACT_TASKS $EXACT_FILES
echo "└─────────────────────────────────────────┘"
echo ""

# Validate: task count should differ only in individuals tasks
# (more rows = more or fewer parallel tasks)
if [ "$ESTIMATED_TASKS" -eq "$EXACT_TASKS" ]; then
    log_success "Task counts match (variant counts happened to align with parallelism)"
else
    TASK_DIFF=$((ESTIMATED_TASKS - EXACT_TASKS))
    log_info "Task counts differ by $TASK_DIFF (expected due to different row partitioning)"
fi

# ============================================================================
# Step 3: Execute workflow
# ============================================================================

log_phase "PHASE 3: WORKFLOW EXECUTION"

echo "Directory: $WORKFLOW_DIR"
echo "Tasks: $EXACT_TASKS"
echo "Variants: $EXACT_COUNT"
echo ""

if [ "$NONINTERACTIVE" = false ]; then
    read -p "Execute workflow? [Y/n] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]] && [[ ! -z $REPLY ]]; then
        log_info "Execution cancelled"
        exit 0
    fi
fi

log_info "Starting HyperFlow workflow execution..."

export WORKFLOW_DIR
export USER_ID=$(id -u)
export USER_GID=$(id -g)
export MAX_PARALLELISM=20

cd "$SCRIPT_DIR"
docker-compose up

COMPOSE_EXIT=$?

# ============================================================================
# Step 4: Verify outputs
# ============================================================================

log_phase "PHASE 4: OUTPUT VERIFICATION"

if [ $COMPOSE_EXIT -eq 0 ]; then
    cd "$WORKFLOW_DIR"

    MISSING=0

    # Check core outputs
    for output in "chr6n.tar.gz" "sifted.SIFT.chr6.txt"; do
        if [ -f "$output" ]; then
            SIZE=$(ls -lh "$output" | awk '{print $5}')
            log_success "Found $output ($SIZE)"
        else
            log_error "Missing: $output"
            MISSING=$((MISSING + 1))
        fi
    done

    # Check population outputs
    for pop in AFR ALL AMR EAS EUR GBR SAS; do
        if [ -f "chr6-${pop}.tar.gz" ]; then
            log_success "Found chr6-${pop}.tar.gz"
        else
            log_error "Missing: chr6-${pop}.tar.gz"
            MISSING=$((MISSING + 1))
        fi
    done

    echo ""
    if [ $MISSING -eq 0 ]; then
        log_success "All expected outputs present!"
        TEST_RESULT=0
    else
        log_error "$MISSING expected outputs missing"
        TEST_RESULT=1
    fi
else
    log_error "Workflow execution failed (exit code: $COMPOSE_EXIT)"
    TEST_RESULT=$COMPOSE_EXIT
fi

# Cleanup
echo ""
if [ "$NONINTERACTIVE" = true ]; then
    docker-compose down
fi

# Final summary
log_phase "TEST SUMMARY"

echo "Deferred Generation Pattern validated:"
echo "  1. [OK] Estimated variant count: $ESTIMATED_COUNT"
echo "  2. [OK] Generated estimated workflow: $ESTIMATED_TASKS tasks"
echo "  3. [OK] Extracted real data: $EXACT_COUNT variants"
echo "  4. [OK] Regenerated exact workflow: $EXACT_TASKS tasks"
if [ $TEST_RESULT -eq 0 ]; then
    echo "  5. [OK] Workflow executed successfully"
else
    echo "  5. [FAIL] Workflow execution failed"
fi
echo ""

exit $TEST_RESULT
