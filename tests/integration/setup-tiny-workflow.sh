#!/bin/bash
#
# Generate a tiny 1000genome workflow for testing
#
# This script:
#   1. Creates a workflow directory with input data
#   2. Generates a tiny workflow (1 chromosome, 1 individuals job = 17 jobs)
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_DIR="$SCRIPT_DIR/workflow-tiny"

echo "Setting up tiny 1000genome workflow..."
echo ""

# Create workflow directory
mkdir -p "$WORKFLOW_DIR"

# Copy input data from data-container
DATA_IMAGE="hyperflowwms/1000genome-data:1.0"

echo "Extracting input data from $DATA_IMAGE..."

# Extract all files to workflow directory root (HyperFlow expects flat structure)
docker run --rm -v "$WORKFLOW_DIR:/output" "$DATA_IMAGE" \
    sh -c "
        # VCF file for chromosome 1
        cp /data/20130502/ALL.chr1.250000.vcf.gz /output/ && gunzip -f /output/ALL.chr1.250000.vcf.gz
        # columns.txt
        cp /data/20130502/columns.txt /output/
        # Population files
        cp /data/populations/* /output/
        # Annotation file for sifting
        cp /data/20130502/ALL.chr1.phase3_shapeit2_mvncall_integrated_v5.20130502.sites.annotation.vcf.gz /output/ && gunzip -f /output/ALL.chr1*.annotation.vcf.gz
    "

echo "Input data extracted."
echo ""

# Generate tiny workflow
echo "Generating tiny workflow (1 chromosome, 1 individuals job)..."

docker run --rm \
    -v "$SCRIPT_DIR/data-tiny.csv:/1000genome-workflow/data.csv" \
    -v "$WORKFLOW_DIR:/output" \
    hyperflowwms/1000genome-generator:1.0 \
    sh -c "cd /1000genome-workflow && ./generate_workflow.sh -i 1 && cp workflow.json /output/"

echo ""
echo "Workflow generated at: $WORKFLOW_DIR/workflow.json"

# Show workflow stats
JOB_COUNT=$(grep -c '"name":' "$WORKFLOW_DIR/workflow.json" || echo "0")
echo "Total jobs: $JOB_COUNT"
echo ""

echo "Directory contents:"
ls -la "$WORKFLOW_DIR"
echo ""

echo "To run the workflow:"
echo "  cd $SCRIPT_DIR"
echo "  ./run-workflow.sh workflow-tiny"
