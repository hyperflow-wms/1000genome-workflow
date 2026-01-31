#!/bin/bash
#
# 1000genome Workflow Integration Test
#
# Usage:
#   ./run-workflow.sh <workflow-directory>
#
# This script:
#   1. Validates workflow directory structure
#   2. Verifies input data files are present
#   3. Runs workflow using Docker Compose
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check arguments
if [ $# -ne 1 ]; then
    echo "Usage: $0 <workflow-directory>"
    echo ""
    echo "Example:"
    echo "  $0 ./workflow-tiny"
    exit 1
fi

WORKFLOW_DIR="$1"

# Validate workflow directory
if [ ! -d "$WORKFLOW_DIR" ]; then
    log_error "Workflow directory not found: $WORKFLOW_DIR"
    exit 1
fi

# Change to workflow directory and get absolute path
cd "$WORKFLOW_DIR"
WORKFLOW_DIR="$PWD"

log_info "Workflow directory: $WORKFLOW_DIR"

# Check for workflow.json
if [ ! -f "workflow.json" ]; then
    log_error "workflow.json not found in $WORKFLOW_DIR"
    exit 1
fi
log_success "Found workflow.json"

# Verify input files are present
log_info "Verifying input files..."

# Check for VCF files
VCF_COUNT=$(ls -1 *.vcf 2>/dev/null | wc -l)
VCF_GZ_COUNT=$(ls -1 *.vcf.gz 2>/dev/null | wc -l)
TOTAL_VCF=$((VCF_COUNT + VCF_GZ_COUNT))

if [ $TOTAL_VCF -eq 0 ]; then
    log_error "No VCF files found in workflow directory"
    exit 1
fi
log_success "Found $TOTAL_VCF VCF files"

# Check for annotation files
ANNOTATION_COUNT=$(ls -1 *.annotation.vcf 2>/dev/null | wc -l)
ANNOTATION_GZ_COUNT=$(ls -1 *.annotation.vcf.gz 2>/dev/null | wc -l)
TOTAL_ANNOTATION=$((ANNOTATION_COUNT + ANNOTATION_GZ_COUNT))

if [ $TOTAL_ANNOTATION -eq 0 ]; then
    log_warning "No annotation files found - sifting jobs may fail"
fi
log_success "Found $TOTAL_ANNOTATION annotation files"

# Check for columns.txt
if [ ! -f "columns.txt" ]; then
    log_error "columns.txt not found"
    exit 1
fi
log_success "Found columns.txt"

# Check for population files (flat structure - in root directory)
POP_FILES="AFR ALL AMR EAS EUR GBR SAS"
POP_COUNT=0
for pop in $POP_FILES; do
    if [ -f "$pop" ]; then
        POP_COUNT=$((POP_COUNT + 1))
    fi
done
if [ $POP_COUNT -eq 0 ]; then
    log_error "No population files found (expected: $POP_FILES)"
    exit 1
fi
log_success "Found $POP_COUNT population files"

# Check for docker-compose.yml
if [ ! -f "$SCRIPT_DIR/docker-compose.yml" ]; then
    log_error "docker-compose.yml not found at: $SCRIPT_DIR/docker-compose.yml"
    exit 1
fi

# Count jobs in workflow
JOB_COUNT=$(grep -c '"name":' workflow.json || echo "0")

# Summary
echo ""
echo "=================================="
echo "1000genome Workflow Test"
echo "=================================="
echo "Workflow directory: $WORKFLOW_DIR"
echo "VCF files: $TOTAL_VCF"
echo "Annotation files: $TOTAL_ANNOTATION"
echo "Population files: $POP_COUNT"
echo "Total jobs: $JOB_COUNT"
echo "=================================="
echo ""

# Ask for confirmation
read -p "Start workflow execution? [Y/n] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]] && [[ ! -z $REPLY ]]; then
    log_warning "Execution cancelled"
    exit 0
fi

# Run Docker Compose
log_info "Starting HyperFlow workflow execution..."
echo ""

export WORKFLOW_DIR
export USER_ID=$(id -u)
export USER_GID=$(id -g)

docker-compose -f "$SCRIPT_DIR/docker-compose.yml" up

COMPOSE_EXIT=$?

echo ""
if [ $COMPOSE_EXIT -eq 0 ]; then
    log_success "Workflow execution completed"

    echo ""
    log_info "Generated output files:"
    ls -lht *.tar.gz 2>/dev/null | head -20 || echo "  (no output files found)"
else
    log_error "Workflow execution failed (exit code: $COMPOSE_EXIT)"
    exit $COMPOSE_EXIT
fi

# Clean up
echo ""
read -p "Remove Docker containers and networks? [Y/n] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
    cd "$SCRIPT_DIR"  # Return to script dir to avoid getwd errors
    docker-compose -f "$SCRIPT_DIR/docker-compose.yml" down
    log_success "Cleaned up Docker resources"
fi

log_success "Done!"
