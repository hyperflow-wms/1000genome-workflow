#!/bin/bash
#
# Generate a micro 1000genome workflow for fast testing
#
# Parameters:
#   - 1 chromosome
#   - 10,000 rows instead of 250,000 (25x smaller VCF file)
#   - 5 parallel individuals jobs (2000 rows each)
#   - 30 individuals instead of 2504 (minimum 26 required by mutation_overlap.py)
#   - Total: 21 jobs (5 individuals + 1 merge + 1 sifting + 14 analysis)
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_DIR="$SCRIPT_DIR/workflow-micro"

echo "Setting up micro 1000genome workflow..."
echo ""

# Create workflow directory
rm -rf "$WORKFLOW_DIR"
mkdir -p "$WORKFLOW_DIR"

# Copy input data from data-container
DATA_IMAGE="hyperflowwms/1000genome-data:1.0"

echo "Extracting input data from $DATA_IMAGE..."

# Extract all files to workflow directory root
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

echo "Trimming columns.txt to 30 individuals..."
# columns.txt is a single line with tab-separated values
# First 9 columns are metadata, rest are individuals
# Keep first 9 + 30 individuals = 39 columns (need 26+ for mutation_overlap.py)
cd "$WORKFLOW_DIR"
cut -f1-39 columns.txt > columns_30.txt
mv columns_30.txt columns.txt

echo "Truncating VCF file to 10,000 lines..."
# Keep only first 10,000 lines to speed up processing (25x smaller)
head -n 10000 ALL.chr1.250000.vcf > ALL.chr1.10000.vcf
mv ALL.chr1.10000.vcf ALL.chr1.250000.vcf

echo "Input data extracted."
echo ""

# Generate micro workflow with 5 parallel individuals jobs
echo "Generating micro workflow (1 chromosome, 5 individuals jobs)..."

docker run --rm \
    -v "$SCRIPT_DIR/data-micro.csv:/1000genome-workflow/data.csv" \
    -v "$WORKFLOW_DIR:/output" \
    hyperflowwms/1000genome-generator:1.0 \
    sh -c "cd /1000genome-workflow && ./generate_workflow.sh -i 5 && cp workflow.json /output/"

echo ""
echo "Workflow generated at: $WORKFLOW_DIR/workflow.json"

# Show workflow stats
PROC_COUNT=$(python3 -c "import json; print(len(json.load(open('$WORKFLOW_DIR/workflow.json'))['processes']))")
echo "Total processes: $PROC_COUNT"

# Count individuals in columns.txt
IND_COUNT=$(($(head -1 "$WORKFLOW_DIR/columns.txt" | tr '\t' '\n' | wc -l) - 9))
echo "Individuals to process: $IND_COUNT"
echo ""

echo "Directory contents:"
ls -la "$WORKFLOW_DIR"
echo ""

echo "To run the workflow:"
echo "  cd $SCRIPT_DIR"
echo "  ./run-workflow.sh workflow-micro"
