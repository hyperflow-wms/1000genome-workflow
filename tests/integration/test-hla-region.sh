#!/bin/bash
#
# Integration test: HLA Region with Real Data
#
# This test validates the full workflow-composer pipeline:
#   1. Uses a natural language-like research intent (HLA region analysis)
#   2. Downloads real chromosome 6 HLA region data via tabix
#   3. Generates and executes the workflow
#   4. Verifies expected outputs
#
# The HLA region (chr6:28477797-33448354) is ~5Mb and contains genes
# critical for immune function.
#
# Usage:
#   ./test-hla-region.sh [--parallelism small|medium|large] [--yes]
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WORKFLOW_DIR="$SCRIPT_DIR/workflow-hla"

# HLA region coordinates (from data_resolver.py KNOWN_REGIONS)
HLA_CHROM="6"
HLA_START="28477797"
HLA_END="33448354"
HLA_REGION="chr${HLA_CHROM}:${HLA_START}-${HLA_END}"

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
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Parse arguments
PARALLELISM="small"
NONINTERACTIVE=false
SKIP_DOWNLOAD=false
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
        --skip-download)
            # For re-running tests without re-downloading data
            SKIP_DOWNLOAD=true
            shift
            ;;
        --quick|-q)
            # Quick mode: extract smaller region (~100kb instead of 5Mb)
            QUICK_MODE=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--parallelism small|medium|large] [--yes] [--skip-download] [--quick]"
            exit 1
            ;;
    esac
done

# In quick mode, extract a smaller region for faster testing
if [ "$QUICK_MODE" = true ]; then
    HLA_END="28577797"  # 100kb region instead of full 5Mb
    log_info "Quick mode: extracting 100kb subset of HLA region"
fi

echo ""
echo "=========================================="
echo "HLA Region Integration Test"
echo "=========================================="
echo "Region: $HLA_REGION (~5 Mb)"
echo "Parallelism: $PARALLELISM"
echo ""

# Create workflow directory
if [ "$SKIP_DOWNLOAD" = false ]; then
    rm -rf "$WORKFLOW_DIR"
fi
mkdir -p "$WORKFLOW_DIR"

# ============================================================================
# Step 1: Download HLA region data via tabix
# ============================================================================

if [ "$SKIP_DOWNLOAD" = false ] || [ ! -f "$WORKFLOW_DIR/ALL.chr6.hla.vcf" ]; then
    log_info "Extracting HLA region from 1000 Genomes (this may take a few minutes)..."

    # Use GATK container which has tabix with HTTPS/SSL support
    docker run --rm \
        -v "$WORKFLOW_DIR:/output" \
        "$TABIX_IMAGE" \
        sh -c "
            set -e
            cd /output

            echo 'Downloading HLA region from chromosome 6...'
            echo 'Region: $HLA_REGION'

            # Extract HLA region using tabix (remote random access)
            # This downloads only the ~5Mb region, not the full 850MB chromosome
            tabix -h '$VCF_URL' $HLA_CHROM:$HLA_START-$HLA_END > ALL.chr6.hla.vcf

            # Count variants extracted
            VARIANT_COUNT=\$(grep -v '^#' ALL.chr6.hla.vcf | wc -l)
            echo \"Extracted \$VARIANT_COUNT variants\"

            # Also extract the annotation file for sifting
            echo 'Downloading HLA region annotations...'
            tabix -h '$ANNOTATION_URL' $HLA_CHROM:$HLA_START-$HLA_END > ALL.chr6.hla.annotation.vcf

            ANNOTATION_COUNT=\$(grep -v '^#' ALL.chr6.hla.annotation.vcf | wc -l)
            echo \"Extracted \$ANNOTATION_COUNT annotation entries\"
        "

    log_success "HLA region data extracted"
else
    log_info "Using existing HLA data (--skip-download)"
fi

# Show data stats
VCF_LINES=$(wc -l < "$WORKFLOW_DIR/ALL.chr6.hla.vcf" 2>/dev/null || echo "0")
log_info "VCF file: $VCF_LINES lines"

# ============================================================================
# Step 2: Copy supporting files from data container
# ============================================================================

if [ "$SKIP_DOWNLOAD" = false ] || [ ! -f "$WORKFLOW_DIR/columns.txt" ]; then
    log_info "Extracting supporting files from data container..."

    DATA_IMAGE="hyperflowwms/1000genome-data:1.0"

    docker run --rm -v "$WORKFLOW_DIR:/output" "$DATA_IMAGE" \
        sh -c "
            # columns.txt (sample metadata)
            cp /data/20130502/columns.txt /output/
            # Population files
            cp /data/populations/* /output/
        "

    # Trim columns.txt to 30 individuals for faster testing
    # (minimum 26 required by mutation_overlap.py)
    # First 9 columns are metadata, rest are individuals
    log_info "Trimming columns.txt to 30 individuals (80x faster)..."
    cd "$WORKFLOW_DIR"
    cut -f1-39 columns.txt > columns_30.txt
    mv columns_30.txt columns.txt

    log_success "Supporting files extracted and trimmed"
fi

# ============================================================================
# Step 3: Create data.csv for HLA region
# ============================================================================

log_info "Creating data configuration..."

# Count actual variant lines (exclude header)
VARIANT_COUNT=$(grep -v '^#' "$WORKFLOW_DIR/ALL.chr6.hla.vcf" | wc -l)

# Create data.csv with the HLA VCF file
# Format: vcf_file,row_count,annotation_file
cat > "$WORKFLOW_DIR/data.csv" << EOF
ALL.chr6.hla.vcf,$VARIANT_COUNT,ALL.chr6.hla.annotation.vcf
EOF

log_success "Created data.csv ($VARIANT_COUNT variants)"

# ============================================================================
# Step 4: Generate workflow using workflow-composer
# ============================================================================

log_info "Generating workflow with workflow-composer..."

cd "$REPO_ROOT/workflow-composer"

# Ensure workflow-composer is installed
if ! python3 -c "import workflow_composer" 2>/dev/null; then
    log_info "Installing workflow-composer..."
    pip install -e . -q
fi

# Generate workflow
python3 -m workflow_composer.cli generate \
    --data-csv "$WORKFLOW_DIR/data.csv" \
    --populations-dir "$REPO_ROOT/workflow-generator/data/populations" \
    --parallelism "$PARALLELISM" \
    --output "$WORKFLOW_DIR/workflow.json"

log_success "Workflow generated"

# Show workflow stats
PROC_COUNT=$(python3 -c "import json; print(len(json.load(open('$WORKFLOW_DIR/workflow.json'))['processes']))")
SIGNAL_COUNT=$(python3 -c "import json; print(len(json.load(open('$WORKFLOW_DIR/workflow.json'))['signals']))")
echo "  Processes: $PROC_COUNT"
echo "  Signals: $SIGNAL_COUNT"

# ============================================================================
# Step 5: Show directory contents
# ============================================================================

echo ""
log_info "Workflow directory contents:"
ls -lh "$WORKFLOW_DIR" | head -20

# ============================================================================
# Step 6: Execute workflow
# ============================================================================

echo ""
echo "=========================================="
echo "Ready to execute HLA workflow"
echo "=========================================="
echo "Directory: $WORKFLOW_DIR"
echo "Processes: $PROC_COUNT"
echo "Variants: $VARIANT_COUNT"
echo ""

if [ "$NONINTERACTIVE" = false ]; then
    read -p "Start workflow execution? [Y/n] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]] && [[ ! -z $REPLY ]]; then
        log_info "Execution cancelled"
        log_info "To run later: cd $SCRIPT_DIR && WORKFLOW_DIR=$WORKFLOW_DIR docker-compose up"
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

    # ============================================================================
    # Step 7: Verify outputs
    # ============================================================================

    echo ""
    log_info "Verifying outputs..."

    cd "$WORKFLOW_DIR"

    EXPECTED_OUTPUTS=(
        "chr6n.tar.gz"                # Merged individuals
        "sifted.SIFT.chr6.txt"        # Sifting output
    )

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
        if [ -f "chr6-${pop}.tar.gz" ]; then
            log_success "Found chr6-${pop}.tar.gz"
        else
            log_error "Missing: chr6-${pop}.tar.gz"
            MISSING=$((MISSING + 1))
        fi

        if [ -f "chr6-${pop}-freq.tar.gz" ]; then
            log_success "Found chr6-${pop}-freq.tar.gz"
        else
            log_error "Missing: chr6-${pop}-freq.tar.gz"
            MISSING=$((MISSING + 1))
        fi
    done

    echo ""
    echo "Output files:"
    ls -lh *.tar.gz 2>/dev/null | head -20 || echo "  (no tar.gz files found)"
    ls -lh sifted.* 2>/dev/null || echo "  (no sifted files found)"

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

# ============================================================================
# Cleanup
# ============================================================================

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
