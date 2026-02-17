#!/bin/bash
#
# extract-data.sh — Extract genomic data based on a workflow plan
#
# Takes plan.json from the workflow composer and:
# 1. Runs tabix extraction commands
# 2. Counts variants in each VCF
# 3. Builds data.csv
# 4. Prints the g1kwf generate command to run next
#
# Usage:
#   extract-data.sh --plan plan.json --output-dir /path/to/workdir [--docker-image IMAGE]
#
# Dependencies: tabix (or Docker with tabix), python3 (for JSON parsing)
# Does NOT require g1kwf (workflow-composer) installed.

set -euo pipefail

# ============================================================================
# Helpers
# ============================================================================
log_info()  { echo -e "\033[0;34m[INFO]\033[0m $*"; }
log_ok()    { echo -e "\033[0;32m[OK]\033[0m $*"; }
log_error() { echo -e "\033[0;31m[ERROR]\033[0m $*" >&2; }

usage() {
    cat <<EOF
Usage: $(basename "$0") --plan PLAN_JSON --output-dir DIR [OPTIONS]

Extract genomic data based on a workflow composer plan.

Required:
  --plan FILE          Path to plan.json from workflow composer
  --output-dir DIR     Directory to extract data into

Options:
  --docker-image IMAGE   Run extraction commands inside this Docker image
  -h, --help             Show this help message
EOF
}

# ============================================================================
# Parse arguments
# ============================================================================
PLAN_JSON=""
OUTPUT_DIR=""
DOCKER_IMAGE=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --plan)        PLAN_JSON="$2"; shift 2 ;;
        --output-dir)  OUTPUT_DIR="$2"; shift 2 ;;
        --docker-image) DOCKER_IMAGE="$2"; shift 2 ;;
        -h|--help)     usage; exit 0 ;;
        *) log_error "Unknown option: $1"; usage; exit 1 ;;
    esac
done

if [ -z "$PLAN_JSON" ] || [ -z "$OUTPUT_DIR" ]; then
    log_error "Missing required arguments"
    usage
    exit 1
fi

if [ ! -f "$PLAN_JSON" ]; then
    log_error "Plan file not found: $PLAN_JSON"
    exit 1
fi

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR=$(cd "$OUTPUT_DIR" && pwd)
PLAN_JSON=$(cd "$(dirname "$PLAN_JSON")" && pwd)/$(basename "$PLAN_JSON")

# ============================================================================
# Step 1: Parse plan.json
# ============================================================================
log_info "Parsing plan: $PLAN_JSON"

PLAN_INFO=$(python3 -c "
import json, sys
with open('$PLAN_JSON') as f:
    plan = json.load(f)

params = plan.get('parameters_used', {})
pops = params.get('populations', [])
ind_jobs = params.get('ind_jobs', 10)

steps = plan.get('data_preparation', {}).get('steps', [])
extract_steps = [s for s in steps if s.get('action') in ('extract_region', 'download')]

print(json.dumps({
    'populations': pops,
    'ind_jobs': ind_jobs,
    'steps': extract_steps
}))
")

POPULATIONS=$(echo "$PLAN_INFO" | python3 -c "import sys,json; print(','.join(json.load(sys.stdin)['populations']))")
IND_JOBS=$(echo "$PLAN_INFO" | python3 -c "import sys,json; print(json.load(sys.stdin)['ind_jobs'])")
NUM_STEPS=$(echo "$PLAN_INFO" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['steps']))")

log_info "Populations: $POPULATIONS"
log_info "Parallelism: $IND_JOBS ind_jobs"
log_info "Extraction steps: $NUM_STEPS"

# ============================================================================
# Step 2: Run extraction commands
# ============================================================================
log_info "Extracting data..."

COMMANDS=$(echo "$PLAN_INFO" | python3 -c "
import sys, json
info = json.load(sys.stdin)
for step in info['steps']:
    for cmd in step.get('commands', []):
        print(cmd)
")

run_command() {
    local cmd="$1"
    if [ -n "$DOCKER_IMAGE" ]; then
        docker run --rm \
            -u "$(id -u):$(id -g)" \
            -v "$OUTPUT_DIR:/work" \
            -w /work \
            "$DOCKER_IMAGE" \
            sh -c "$cmd"
    else
        (cd "$OUTPUT_DIR" && eval "$cmd")
    fi
}

EXTRACT_COUNT=0
while IFS= read -r cmd; do
    [ -z "$cmd" ] && continue
    log_info "Running: ${cmd:0:80}..."
    if run_command "$cmd"; then
        EXTRACT_COUNT=$((EXTRACT_COUNT + 1))
    else
        log_error "Command failed: $cmd"
        exit 1
    fi
done <<< "$COMMANDS"

log_ok "Executed $EXTRACT_COUNT extraction commands"

# ============================================================================
# Step 3: Build data.csv
# ============================================================================
log_info "Building data.csv..."

cd "$OUTPUT_DIR"
DATA_CSV=""
for vcf_file in ALL.chr*.vcf; do
    [ -f "$vcf_file" ] || continue
    [[ "$vcf_file" == *annotation* ]] && continue

    VARIANT_COUNT=$(grep -vc '^#' "$vcf_file" || echo "0")
    CHROM=$(echo "$vcf_file" | sed -n 's/.*chr\([0-9XY]*\).*/\1/p')
    ANNOTATION_FILE=$(ls ALL.chr${CHROM}*.annotation.vcf 2>/dev/null | head -1)

    if [ -n "$ANNOTATION_FILE" ] && [ "$VARIANT_COUNT" -gt 0 ]; then
        DATA_CSV="${DATA_CSV}${vcf_file},${VARIANT_COUNT},${ANNOTATION_FILE}\n"
        VCF_SIZE=$(du -h "$vcf_file" | cut -f1)
        log_info "  chr$CHROM: $VARIANT_COUNT variants ($VCF_SIZE)"
    fi
done

if [ -z "$DATA_CSV" ]; then
    log_error "No VCF files found in $OUTPUT_DIR"
    exit 1
fi

echo -e "$DATA_CSV" | head -n -1 > data.csv
log_ok "Created data.csv:"
cat data.csv

# ============================================================================
# Step 4: Summary and generate command
# ============================================================================
TOTAL_SIZE=$(du -sh "$OUTPUT_DIR" | cut -f1)
log_ok "Extraction complete. Total size: $TOTAL_SIZE"

echo ""
echo "========================================================================"
echo "  Next step: run g1kwf generate"
echo "========================================================================"
echo ""
echo "  cd $OUTPUT_DIR"
echo "  g1kwf generate \\"
echo "      --data-csv data.csv \\"
echo "      --populations $POPULATIONS \\"
echo "      --ind-jobs $IND_JOBS \\"
echo "      -o workflow.json"
echo ""
