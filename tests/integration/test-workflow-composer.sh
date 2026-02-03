#!/bin/bash
#
# Integration test for workflow-composer
#
# This script:
#   1. Generates a workflow using workflow-composer (not the old daxgen.py)
#   2. Sets up test data (micro dataset)
#   3. Runs the workflow via docker-compose
#   4. Verifies expected outputs
#
# Usage:
#   ./test-workflow-composer.sh [--parallelism small|medium|large]
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WORKFLOW_DIR="$SCRIPT_DIR/workflow-composer-test"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Parse arguments
PARALLELISM="small"
NONINTERACTIVE=false

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
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

echo ""
echo "=========================================="
echo "Workflow Composer Integration Test"
echo "=========================================="
echo "Parallelism: $PARALLELISM"
echo ""

# Clean up previous run
rm -rf "$WORKFLOW_DIR"
mkdir -p "$WORKFLOW_DIR"

# Step 1: Generate workflow using workflow-composer
log_info "Generating workflow with workflow-composer..."

cd "$REPO_ROOT/workflow-composer"

# Check if workflow-composer is installed
if ! python3 -c "import workflow_composer" 2>/dev/null; then
    log_info "Installing workflow-composer..."
    pip install -e . -q
fi

# Generate workflow
python3 -m workflow_composer.cli generate \
    --data-csv "$SCRIPT_DIR/data-micro.csv" \
    --populations-dir "$REPO_ROOT/workflow-generator/data/populations" \
    --parallelism "$PARALLELISM" \
    --output "$WORKFLOW_DIR/workflow.json"

log_success "Workflow generated"

# Show workflow stats
PROC_COUNT=$(python3 -c "import json; print(len(json.load(open('$WORKFLOW_DIR/workflow.json'))['processes']))")
SIGNAL_COUNT=$(python3 -c "import json; print(len(json.load(open('$WORKFLOW_DIR/workflow.json'))['signals']))")
echo "  Processes: $PROC_COUNT"
echo "  Signals: $SIGNAL_COUNT"

# Step 2: Extract test data
log_info "Extracting test data from Docker image..."

DATA_IMAGE="hyperflowwms/1000genome-data:1.0"

docker run --rm -v "$WORKFLOW_DIR:/output" "$DATA_IMAGE" \
    sh -c "
        # VCF file for chromosome 1
        cp /data/20130502/ALL.chr1.250000.vcf.gz /output/ && gunzip -f /output/ALL.chr1.250000.vcf.gz
        # columns.txt
        cp /data/20130502/columns.txt /output/
        # Population files (flat, in root)
        cp /data/populations/* /output/
        # Annotation file for sifting
        cp /data/20130502/ALL.chr1.phase3_shapeit2_mvncall_integrated_v5.20130502.sites.annotation.vcf.gz /output/ && gunzip -f /output/ALL.chr1*.annotation.vcf.gz
    "

log_success "Data extracted"

# Step 3: Trim data for micro test
log_info "Trimming data for micro test..."

cd "$WORKFLOW_DIR"

# Trim columns.txt to 30 individuals (minimum 26 required by mutation_overlap.py)
cut -f1-39 columns.txt > columns_30.txt
mv columns_30.txt columns.txt

# Truncate VCF to 10,000 lines (matches data-micro.csv)
head -n 10000 ALL.chr1.250000.vcf > ALL.chr1.10000.vcf
mv ALL.chr1.10000.vcf ALL.chr1.250000.vcf

log_success "Data trimmed (10,000 rows, 30 individuals)"

# Show directory contents
echo ""
log_info "Workflow directory contents:"
ls -la "$WORKFLOW_DIR"

# Step 4: Run workflow
echo ""
echo "=========================================="
echo "Ready to execute workflow"
echo "=========================================="
echo "Directory: $WORKFLOW_DIR"
echo "Processes: $PROC_COUNT"
echo ""

if [ "$NONINTERACTIVE" = false ]; then
    read -p "Start workflow execution? [Y/n] " -n 1 -r
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

echo ""
if [ $COMPOSE_EXIT -eq 0 ]; then
    log_success "Workflow execution completed!"

    # Step 5: Verify outputs
    echo ""
    log_info "Verifying outputs..."

    cd "$WORKFLOW_DIR"

    EXPECTED_OUTPUTS=(
        "chr1n.tar.gz"               # Merged individuals
        "sifted.SIFT.chr1.txt"       # Sifting output
    )

    # Also check for population outputs (7 populations × 2 analyses = 14 files)
    POPULATIONS="AFR ALL AMR EAS EUR GBR SAS"

    MISSING=0
    for output in "${EXPECTED_OUTPUTS[@]}"; do
        if [ -f "$output" ]; then
            SIZE=$(ls -lh "$output" | awk '{print $5}')
            log_success "Found $output ($SIZE)"
        else
            log_error "Missing: $output"
            MISSING=$((MISSING + 1))
        fi
    done

    # Check population outputs
    for pop in $POPULATIONS; do
        if [ -f "chr1-${pop}.tar.gz" ]; then
            log_success "Found chr1-${pop}.tar.gz"
        else
            log_error "Missing: chr1-${pop}.tar.gz"
            MISSING=$((MISSING + 1))
        fi

        if [ -f "chr1-${pop}-freq.tar.gz" ]; then
            log_success "Found chr1-${pop}-freq.tar.gz"
        else
            log_error "Missing: chr1-${pop}-freq.tar.gz"
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
if [ "$NONINTERACTIVE" = false ]; then
    read -p "Remove Docker containers and networks? [Y/n] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
        docker-compose down
        log_success "Cleaned up Docker resources"
    fi
else
    docker-compose down
fi

exit $TEST_RESULT
