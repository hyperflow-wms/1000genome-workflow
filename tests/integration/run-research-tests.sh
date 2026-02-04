#!/bin/bash
#
# End-to-End Integration Test Runner
#
# Runs integration tests from natural language research questions through
# to workflow execution. See RFC-002 for architecture details.
#
# Usage:
#   ./run-research-tests.sh [OPTIONS] [TEST_ID...]
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CASES_YAML="$SCRIPT_DIR/cases.yaml"

# Load .env file if present (for API keys)
if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    source "$REPO_ROOT/.env"
    set +a
fi
FRAMEWORK_PY="$SCRIPT_DIR/lib/test_framework.py"

# Container images
TABIX_IMAGE="broadinstitute/gatk:4.4.0.0"
DATA_IMAGE="hyperflowwms/1000genome-data:1.0"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_phase() {
    echo -e "\n${CYAN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}  $1${NC}"
    echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}\n"
}
log_test() {
    echo -e "\n${MAGENTA}┌─────────────────────────────────────────────────────────────────┐${NC}"
    echo -e "${MAGENTA}│  TEST: $1${NC}"
    echo -e "${MAGENTA}└─────────────────────────────────────────────────────────────────┘${NC}\n"
}

# Default options
MOCK_LLM=false
MODEL=""
STOP_BEFORE=""
NONINTERACTIVE=false
PARALLELISM=""
VCPUS=""
IND_JOBS=""
VERBOSE=false
LIST_ONLY=false
TEST_IDS=()

usage() {
    cat << EOF
Usage: $0 [OPTIONS] [TEST_ID...]

Run end-to-end integration tests from natural language research questions.

Arguments:
  TEST_ID              Test case ID(s) to run (default: all suitable tests)

Options:
  --list               List available test cases and exit
  --mock-llm           Use mock intents instead of calling LLM (for CI)
  --model MODEL        LLM model to use
  --stop-before-extract    Stop after PLAN phase
  --stop-before-execute    Stop after GENERATE phase
  -y, --yes            Non-interactive mode, proceed through all phases

Parallelism Options (mutually exclusive):
  -p, --parallelism PRESET   Preset: small(10), medium(50), large(250)
  --vcpus N                  Compute optimal parallelism for N vCPUs
  --ind-jobs N               Explicit ind_jobs value

General Options:
  -v, --verbose        Verbose output
  -h, --help           Show this help message

Examples:
  $0 --list
  $0 --mock-llm -y micro
  $0 --mock-llm --vcpus 16 eas-hla-autoimmune
  $0 --mock-llm -y

EOF
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --list)
            LIST_ONLY=true
            shift
            ;;
        --mock-llm)
            MOCK_LLM=true
            shift
            ;;
        --model)
            MODEL="$2"
            shift 2
            ;;
        --stop-before-extract)
            STOP_BEFORE="extract"
            shift
            ;;
        --stop-before-execute)
            STOP_BEFORE="execute"
            shift
            ;;
        -y|--yes)
            NONINTERACTIVE=true
            shift
            ;;
        -p|--parallelism)
            PARALLELISM="$2"
            shift 2
            ;;
        --vcpus)
            VCPUS="$2"
            shift 2
            ;;
        --ind-jobs)
            IND_JOBS="$2"
            shift 2
            ;;
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            log_error "Unknown option: $1"
            usage
            exit 1
            ;;
        *)
            TEST_IDS+=("$1")
            shift
            ;;
    esac
done

# Validate mutually exclusive parallelism options
PARALLELISM_COUNT=0
[ -n "$PARALLELISM" ] && PARALLELISM_COUNT=$((PARALLELISM_COUNT + 1))
[ -n "$VCPUS" ] && PARALLELISM_COUNT=$((PARALLELISM_COUNT + 1))
[ -n "$IND_JOBS" ] && PARALLELISM_COUNT=$((PARALLELISM_COUNT + 1))

if [ $PARALLELISM_COUNT -gt 1 ]; then
    log_error "Options --parallelism, --vcpus, and --ind-jobs are mutually exclusive"
    exit 1
fi

# Default parallelism if none specified
if [ $PARALLELISM_COUNT -eq 0 ]; then
    PARALLELISM="small"
fi

# Ensure workflow-composer is installed
cd "$REPO_ROOT/workflow-composer"
if ! python3 -c "import workflow_composer" 2>/dev/null; then
    log_info "Installing workflow-composer..."
    pip install -e . -q 2>/dev/null || pip install -e . --break-system-packages -q
fi

# Ensure PyYAML is available
if ! python3 -c "import yaml" 2>/dev/null; then
    log_info "Installing PyYAML..."
    pip install pyyaml -q 2>/dev/null || pip install pyyaml --break-system-packages -q
fi

# List mode
if [ "$LIST_ONLY" = true ]; then
    echo ""
    echo "Available Test Cases:"
    echo "====================="
    echo ""
    printf "%-20s %s\n" "ID" "NAME"
    printf "%-20s %s\n" "--------------------" "--------------------------------------------"
    python3 "$FRAMEWORK_PY" list --yaml "$CASES_YAML" | while IFS=$'\t' read -r id name; do
        printf "%-20s %s\n" "$id" "$name"
    done
    echo ""
    exit 0
fi

# If no test IDs specified, show help
if [ ${#TEST_IDS[@]} -eq 0 ]; then
    echo ""
    echo "No test IDs specified. Please specify which tests to run."
    echo ""
    echo "Available test cases:"
    echo ""
    printf "  %-20s %s\n" "ID" "NAME"
    printf "  %-20s %s\n" "--------------------" "--------------------------------------------"
    python3 "$FRAMEWORK_PY" list --yaml "$CASES_YAML" | while IFS=$'\t' read -r id name; do
        printf "  %-20s %s\n" "$id" "$name"
    done
    echo ""
    echo "Examples:"
    echo "  $0 micro                      # Run micro smoke test"
    echo "  $0 --mock-llm -y micro        # Run micro test with mock LLM, non-interactive"
    echo "  $0 --mock-llm eas-hla-autoimmune  # Run HLA region test"
    echo "  $0 --list                     # List all test cases"
    echo "  $0 --help                     # Show full help"
    echo ""
    exit 0
fi

# Build parallelism flags for Python calls
PARALLELISM_FLAGS=""
[ -n "$PARALLELISM" ] && PARALLELISM_FLAGS="--parallelism $PARALLELISM"
[ -n "$VCPUS" ] && PARALLELISM_FLAGS="--vcpus $VCPUS"
[ -n "$IND_JOBS" ] && PARALLELISM_FLAGS="--ind-jobs $IND_JOBS"

echo ""
echo "╔═══════════════════════════════════════════════════════════════════════╗"
echo "║                    END-TO-END INTEGRATION TEST                        ║"
echo "╠═══════════════════════════════════════════════════════════════════════╣"
echo "║  Tests: ${TEST_IDS[*]}"
echo "║  Mode: $([ "$MOCK_LLM" = true ] && echo "Mock LLM" || echo "Real LLM")"
if [ -n "$VCPUS" ]; then
    echo "║  Parallelism: adaptive (${VCPUS} vCPUs)"
elif [ -n "$IND_JOBS" ]; then
    echo "║  Parallelism: explicit (${IND_JOBS} ind_jobs)"
else
    echo "║  Parallelism: ${PARALLELISM}"
fi
echo "╚═══════════════════════════════════════════════════════════════════════╝"
echo ""

# Track results
PASSED=0
FAILED=0
SKIPPED=0
declare -A RESULTS

# Run each test
for TEST_ID in "${TEST_IDS[@]}"; do
    log_test "$TEST_ID"

    WORKFLOW_DIR="$SCRIPT_DIR/workflow-$TEST_ID"
    rm -rf "$WORKFLOW_DIR"
    mkdir -p "$WORKFLOW_DIR"

    # ========================================================================
    # PHASE 1: INTERPRET
    # ========================================================================
    log_phase "Phase 1: INTERPRET (NL → ResearchIntent)"

    MOCK_FLAG=""
    MODEL_FLAG=""
    [ "$MOCK_LLM" = true ] && MOCK_FLAG="--mock"
    [ -n "$MODEL" ] && MODEL_FLAG="--model $MODEL"

    if ! INTENT_JSON=$(python3 "$FRAMEWORK_PY" interpret \
        --yaml "$CASES_YAML" \
        --test-id "$TEST_ID" \
        $MOCK_FLAG $MODEL_FLAG 2>&1); then
        log_error "Interpretation failed for $TEST_ID"
        log_error "$INTENT_JSON"
        FAILED=$((FAILED + 1))
        RESULTS[$TEST_ID]="FAILED (interpret)"
        continue
    fi

    ANALYSIS_TYPE=$(echo "$INTENT_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['analysis_type'])")
    POPULATIONS=$(echo "$INTENT_JSON" | python3 -c "import sys,json; print(', '.join(json.load(sys.stdin)['populations']))")

    log_success "Intent parsed: $ANALYSIS_TYPE"
    log_info "  Populations: $POPULATIONS"

    if [ "$VERBOSE" = true ]; then
        echo "$INTENT_JSON" | python3 -m json.tool
    fi

    # Validate against expected
    VALIDATION=$(python3 "$FRAMEWORK_PY" validate \
        --actual-json "$INTENT_JSON" \
        --yaml "$CASES_YAML" \
        --test-id "$TEST_ID")

    IS_VALID=$(echo "$VALIDATION" | python3 -c "import sys,json; print(json.load(sys.stdin)['valid'])")
    if [ "$IS_VALID" = "False" ]; then
        DIFFS=$(echo "$VALIDATION" | python3 -c "import sys,json; print('\n  '.join(json.load(sys.stdin)['differences']))")
        log_warning "Intent differs from expected:"
        echo "  $DIFFS"
    fi

    # ========================================================================
    # PHASE 2: PLAN
    # ========================================================================
    log_phase "Phase 2: PLAN (Advisory plan + estimated workflow)"

    if ! PLAN_JSON=$(python3 "$FRAMEWORK_PY" plan \
        --intent-json "$INTENT_JSON" \
        --compute-env local 2>&1); then
        log_error "Planning failed: $PLAN_JSON"
        FAILED=$((FAILED + 1))
        RESULTS[$TEST_ID]="FAILED (plan)"
        continue
    fi

    TRANSFER_MB=$(echo "$PLAN_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['data_preparation']['estimated_transfer_mb'])")
    TASK_COUNT=$(echo "$PLAN_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['task_count'])")
    FILE_COUNT=$(echo "$PLAN_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['file_count'])")

    log_success "Plan created"
    log_info "  Tasks: $TASK_COUNT"
    log_info "  Files: $FILE_COUNT"
    log_info "  Estimated transfer: ${TRANSFER_MB} MB"

    # Show human-readable plan summary
    DESCRIPTION=$(echo "$PLAN_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['description'])")
    RATIONALE=$(echo "$PLAN_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['rationale'])")
    echo ""
    echo -e "${CYAN}  Description:${NC} $DESCRIPTION"
    echo -e "${CYAN}  Rationale:${NC} $RATIONALE"
    echo ""

    # Show adaptive parallelism calculation if using --vcpus
    if [ -n "$VCPUS" ]; then
        ADAPTIVE_INFO=$(python3 "$FRAMEWORK_PY" adaptive-parallelism \
            --intent-json "$INTENT_JSON" \
            --vcpus "$VCPUS")
        COMPUTED_IND_JOBS=$(echo "$ADAPTIVE_INFO" | python3 -c "import sys,json; print(json.load(sys.stdin)['ind_jobs'])")
        ESTIMATED_VARIANTS=$(echo "$ADAPTIVE_INFO" | python3 -c "import sys,json; print(json.load(sys.stdin)['estimated_variants'])")
        log_info "  Adaptive parallelism: $COMPUTED_IND_JOBS ind_jobs (for $VCPUS vCPUs, ~$ESTIMATED_VARIANTS variants)"
    fi

    # Save formatted JSON files
    echo "$INTENT_JSON" | python3 -m json.tool > "$WORKFLOW_DIR/intent.json"
    echo "$PLAN_JSON" | python3 -m json.tool > "$WORKFLOW_DIR/plan.json"

    # Generate estimated workflow
    if ! ESTIMATED_WORKFLOW=$(python3 "$FRAMEWORK_PY" estimate \
        --intent-json "$INTENT_JSON" \
        $PARALLELISM_FLAGS 2>&1); then
        log_error "Estimate generation failed: $ESTIMATED_WORKFLOW"
        FAILED=$((FAILED + 1))
        RESULTS[$TEST_ID]="FAILED (plan)"
        continue
    fi

    echo "$ESTIMATED_WORKFLOW" | python3 -m json.tool > "$WORKFLOW_DIR/workflow-estimated.json"

    EST_TASKS=$(echo "$ESTIMATED_WORKFLOW" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['processes']))")
    EST_FILES=$(echo "$ESTIMATED_WORKFLOW" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['signals']))")

    log_success "Estimated workflow generated"
    log_info "  Tasks: $EST_TASKS"
    log_info "  Files: $EST_FILES"

    # Determine stop point
    STOP_FLAG=""
    [ -n "$STOP_BEFORE" ] && STOP_FLAG="--explicit-stop $STOP_BEFORE"
    [ "$NONINTERACTIVE" = true ] && STOP_FLAG="$STOP_FLAG --force-yes"

    STOP_POINT=$(python3 "$FRAMEWORK_PY" stop-point \
        --transfer-mb "$TRANSFER_MB" \
        --yaml "$CASES_YAML" \
        $STOP_FLAG)

    if [ "$STOP_POINT" != "never" ]; then
        log_warning "Auto-stop: $STOP_POINT (transfer: ${TRANSFER_MB} MB)"
    fi

    if [ "$STOP_POINT" = "plan" ]; then
        log_warning "Stopping after PLAN phase (volume threshold exceeded)"
        echo ""
        echo "To proceed: $0 -y $TEST_ID"
        SKIPPED=$((SKIPPED + 1))
        RESULTS[$TEST_ID]="SKIPPED (volume)"
        continue
    fi

    # ========================================================================
    # PHASE 3: EXTRACT
    # ========================================================================
    log_phase "Phase 3: EXTRACT (Acquire data via tabix)"

    SKIP_EXTRACT=$(python3 "$FRAMEWORK_PY" test-info \
        --yaml "$CASES_YAML" \
        --test-id "$TEST_ID" \
        --field skip_extract)

    if [ "$SKIP_EXTRACT" = "true" ]; then
        log_info "Test configured to skip extract phase"

        if [ "$TEST_ID" = "micro" ]; then
            log_info "Setting up micro test data from Docker image..."

            docker run --rm -v "$WORKFLOW_DIR:/output" "$DATA_IMAGE" \
                sh -c "
                    cp /data/20130502/ALL.chr1.250000.vcf.gz /output/ && gunzip -f /output/ALL.chr1.250000.vcf.gz
                    cp /data/20130502/columns.txt /output/
                    cp /data/populations/* /output/
                    cp /data/20130502/ALL.chr1.phase3_shapeit2_mvncall_integrated_v5.20130502.sites.annotation.vcf.gz /output/ && gunzip -f /output/ALL.chr1*.annotation.vcf.gz
                " 2>/dev/null

            # Fix ownership (Docker creates files as root)
            sudo chown -R "$(id -u):$(id -g)" "$WORKFLOW_DIR"/* 2>/dev/null || chown -R "$(id -u):$(id -g)" "$WORKFLOW_DIR"/* 2>/dev/null || true

            cd "$WORKFLOW_DIR"
            cut -f1-39 columns.txt > columns_30.txt && mv columns_30.txt columns.txt
            head -n 10001 ALL.chr1.250000.vcf > ALL.chr1.10000.vcf && mv ALL.chr1.10000.vcf ALL.chr1.250000.vcf

            log_success "Micro test data prepared"
        fi
    else
        # Full tabix extraction
        log_info "Extracting data via tabix..."

        TABIX_COMMANDS=$(python3 "$FRAMEWORK_PY" tabix-commands \
            --intent-json "$INTENT_JSON" \
            --output-dir "$WORKFLOW_DIR")

        EXTRACT_FAILED=false

        echo "$TABIX_COMMANDS" | python3 -c "
import sys, json
commands = json.load(sys.stdin)
for cmd in commands:
    print(f\"CHROM={cmd['chromosome']}\")
    print(f\"REGION={cmd.get('region', '')}\")
    print(f\"VCF_URL={cmd['vcf_url']}\")
    print(f\"ANNOTATION_URL={cmd['annotation_url']}\")
    print(f\"OUTPUT_VCF={cmd['output_vcf']}\")
    print(f\"OUTPUT_ANNOTATION={cmd['output_annotation']}\")
    print('---')
" | while IFS= read -r line; do
            if [[ "$line" == "---" ]]; then
                # Execute the tabix extraction
                if [ -n "$CHROM" ]; then
                    log_info "Extracting chromosome $CHROM..."

                    TABIX_REGION_ARG=""
                    if [ -n "$REGION" ]; then
                        TABIX_REGION_ARG="$REGION"
                    fi

                    docker run --rm \
                        -v "$WORKFLOW_DIR:/output" \
                        "$TABIX_IMAGE" \
                        sh -c "
                            set -e
                            cd /output
                            if [ -n '$TABIX_REGION_ARG' ]; then
                                tabix -h '$VCF_URL' $TABIX_REGION_ARG > '$OUTPUT_VCF'
                                tabix -h '$ANNOTATION_URL' $TABIX_REGION_ARG > '$OUTPUT_ANNOTATION'
                            else
                                # Full chromosome - download directly
                                curl -sL '$VCF_URL' | gunzip > '$OUTPUT_VCF'
                                curl -sL '$ANNOTATION_URL' | gunzip > '$OUTPUT_ANNOTATION'
                            fi
                        " 2>/dev/null || EXTRACT_FAILED=true

                    if [ "$EXTRACT_FAILED" = true ]; then
                        log_error "Failed to extract data for chromosome $CHROM"
                    else
                        VARIANT_COUNT=$(grep -v '^#' "$WORKFLOW_DIR/$OUTPUT_VCF" 2>/dev/null | wc -l || echo "0")
                        log_success "Extracted $VARIANT_COUNT variants for chr$CHROM"
                    fi
                fi
                # Reset variables
                CHROM=""
                REGION=""
                VCF_URL=""
                ANNOTATION_URL=""
                OUTPUT_VCF=""
                OUTPUT_ANNOTATION=""
            elif [[ "$line" == CHROM=* ]]; then
                CHROM="${line#CHROM=}"
            elif [[ "$line" == REGION=* ]]; then
                REGION="${line#REGION=}"
            elif [[ "$line" == VCF_URL=* ]]; then
                VCF_URL="${line#VCF_URL=}"
            elif [[ "$line" == ANNOTATION_URL=* ]]; then
                ANNOTATION_URL="${line#ANNOTATION_URL=}"
            elif [[ "$line" == OUTPUT_VCF=* ]]; then
                OUTPUT_VCF="${line#OUTPUT_VCF=}"
            elif [[ "$line" == OUTPUT_ANNOTATION=* ]]; then
                OUTPUT_ANNOTATION="${line#OUTPUT_ANNOTATION=}"
            fi
        done

        # Fix ownership of extracted files (Docker creates files as root)
        sudo chown -R "$(id -u):$(id -g)" "$WORKFLOW_DIR"/* 2>/dev/null || chown -R "$(id -u):$(id -g)" "$WORKFLOW_DIR"/* 2>/dev/null || true

        # Copy supporting files
        log_info "Copying supporting files..."
        docker run --rm -v "$WORKFLOW_DIR:/output" "$DATA_IMAGE" \
            sh -c "
                cp /data/20130502/columns.txt /output/
                cp /data/populations/* /output/
            " 2>/dev/null

        # Fix ownership (Docker creates files as root)
        sudo chown -R "$(id -u):$(id -g)" "$WORKFLOW_DIR"/* 2>/dev/null || chown -R "$(id -u):$(id -g)" "$WORKFLOW_DIR"/* 2>/dev/null || true

        # Trim to 30 individuals for faster testing
        cd "$WORKFLOW_DIR"
        if [ -f columns.txt ]; then
            cut -f1-39 columns.txt > columns_30.txt && mv columns_30.txt columns.txt
            log_success "Supporting files ready (trimmed to 30 individuals)"
        fi
    fi

    if [ "$STOP_POINT" = "extract" ]; then
        log_warning "Stopping after EXTRACT phase"
        SKIPPED=$((SKIPPED + 1))
        RESULTS[$TEST_ID]="SKIPPED (stop-before-execute)"
        continue
    fi

    # ========================================================================
    # PHASE 4: GENERATE
    # ========================================================================
    log_phase "Phase 4: GENERATE (Create final workflow with actual counts)"

    cd "$WORKFLOW_DIR"

    # Build data.csv from actual downloaded files
    DATA_CSV_CONTENT=""
    for vcf_file in ALL.chr*.vcf; do
        if [ -f "$vcf_file" ] && [[ ! "$vcf_file" == *annotation* ]]; then
            VARIANT_COUNT=$(grep -v '^#' "$vcf_file" 2>/dev/null | wc -l || echo "0")

            # Find corresponding annotation file
            # Extract chromosome from vcf filename
            CHROM=$(echo "$vcf_file" | sed -n 's/.*chr\([0-9XY]*\).*/\1/p')
            ANNOTATION_FILE=$(ls ALL.chr${CHROM}*.annotation.vcf 2>/dev/null | head -1)

            if [ -n "$ANNOTATION_FILE" ] && [ "$VARIANT_COUNT" -gt 0 ]; then
                DATA_CSV_CONTENT="${DATA_CSV_CONTENT}${vcf_file},${VARIANT_COUNT},${ANNOTATION_FILE}\n"
            fi
        fi
    done

    if [ -z "$DATA_CSV_CONTENT" ]; then
        log_warning "No VCF files found - skipping workflow generation"
        SKIPPED=$((SKIPPED + 1))
        RESULTS[$TEST_ID]="SKIPPED (no data)"
        continue
    fi

    echo -e "$DATA_CSV_CONTENT" | head -n -1 > data.csv
    log_info "Created data.csv:"
    cat data.csv

    # Determine parallelism for CLI
    CLI_PARALLELISM=""
    if [ -n "$IND_JOBS" ]; then
        CLI_PARALLELISM="--ind-jobs $IND_JOBS"
    elif [ -n "$VCPUS" ]; then
        # Compute adaptive parallelism using the framework
        ADAPTIVE_RESULT=$(python3 "$FRAMEWORK_PY" adaptive-parallelism \
            --intent-json "$INTENT_JSON" \
            --vcpus "$VCPUS")
        ADAPTIVE_JOBS=$(echo "$ADAPTIVE_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['ind_jobs'])")
        CLI_PARALLELISM="--ind-jobs $ADAPTIVE_JOBS"
        log_info "Adaptive parallelism: $ADAPTIVE_JOBS ind_jobs"
    elif [ -n "$PARALLELISM" ]; then
        CLI_PARALLELISM="--parallelism $PARALLELISM"
    fi

    cd "$REPO_ROOT/workflow-composer"
    python3 -m workflow_composer.cli generate \
        --data-csv "$WORKFLOW_DIR/data.csv" \
        --populations-dir "$REPO_ROOT/workflow-generator/data/populations" \
        $CLI_PARALLELISM \
        --output "$WORKFLOW_DIR/workflow.json" 2>/dev/null

    GEN_TASKS=$(python3 -c "import json; print(len(json.load(open('$WORKFLOW_DIR/workflow.json'))['processes']))")
    GEN_FILES=$(python3 -c "import json; print(len(json.load(open('$WORKFLOW_DIR/workflow.json'))['signals']))")

    log_success "Final workflow generated"
    log_info "  Tasks: $GEN_TASKS"
    log_info "  Files: $GEN_FILES"

    if [ "$EST_TASKS" != "$GEN_TASKS" ]; then
        log_info "  Task difference from estimate: $((GEN_TASKS - EST_TASKS))"
    fi

    if [ "$STOP_POINT" = "generate" ]; then
        log_warning "Stopping after GENERATE phase"
        SKIPPED=$((SKIPPED + 1))
        RESULTS[$TEST_ID]="SKIPPED (stop-before-execute)"
        continue
    fi

    # ========================================================================
    # PHASE 5: EXECUTE
    # ========================================================================
    log_phase "Phase 5: EXECUTE (Run workflow)"

    if [ ! -f "$WORKFLOW_DIR/workflow.json" ]; then
        log_warning "No workflow.json - skipping execution"
        SKIPPED=$((SKIPPED + 1))
        RESULTS[$TEST_ID]="SKIPPED (no workflow)"
        continue
    fi

    if [ "$NONINTERACTIVE" = false ]; then
        read -p "Execute workflow? [Y/n] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]] && [[ ! -z $REPLY ]]; then
            log_info "Execution cancelled"
            SKIPPED=$((SKIPPED + 1))
            RESULTS[$TEST_ID]="SKIPPED (user)"
            continue
        fi
    fi

    log_info "Starting HyperFlow workflow execution..."

    export WORKFLOW_DIR
    export USER_ID=$(id -u)
    export USER_GID=$(id -g)
    export MAX_PARALLELISM=20

    cd "$SCRIPT_DIR"

    if docker-compose up 2>&1; then
        log_success "Workflow execution completed"

        # Fix ownership of output files (Docker containers run as root)
        sudo chown -R "$(id -u):$(id -g)" "$WORKFLOW_DIR"/* 2>/dev/null || chown -R "$(id -u):$(id -g)" "$WORKFLOW_DIR"/* 2>/dev/null || true

        # Verify outputs
        log_info "Verifying outputs..."
        VERIFY_RESULT=$(python3 "$FRAMEWORK_PY" verify-outputs \
            --workflow-dir "$WORKFLOW_DIR" \
            --yaml "$CASES_YAML" \
            --test-id "$TEST_ID")

        MISSING_COUNT=$(echo "$VERIFY_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['missing_count'])")
        EXPECTED_COUNT=$(echo "$VERIFY_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['expected_count'])")

        if [ "$MISSING_COUNT" -eq 0 ]; then
            log_success "All $EXPECTED_COUNT expected outputs present"
            PASSED=$((PASSED + 1))
            RESULTS[$TEST_ID]="PASSED"
        else
            MISSING_FILES=$(echo "$VERIFY_RESULT" | python3 -c "import sys,json; print(', '.join(json.load(sys.stdin)['missing_files']))")
            log_warning "Missing $MISSING_COUNT of $EXPECTED_COUNT outputs: $MISSING_FILES"
            PASSED=$((PASSED + 1))
            RESULTS[$TEST_ID]="PASSED (partial outputs)"
        fi
    else
        log_error "Workflow execution failed"
        FAILED=$((FAILED + 1))
        RESULTS[$TEST_ID]="FAILED (execute)"
    fi

    if [ "$NONINTERACTIVE" = true ]; then
        docker-compose down 2>/dev/null || true
    fi
done

# ============================================================================
# Summary
# ============================================================================
log_phase "TEST SUMMARY"

echo ""
printf "%-25s %s\n" "TEST" "RESULT"
printf "%-25s %s\n" "-------------------------" "--------------------"
for TEST_ID in "${TEST_IDS[@]}"; do
    RESULT="${RESULTS[$TEST_ID]:-UNKNOWN}"
    case $RESULT in
        PASSED*)
            printf "%-25s ${GREEN}%s${NC}\n" "$TEST_ID" "$RESULT"
            ;;
        FAILED*)
            printf "%-25s ${RED}%s${NC}\n" "$TEST_ID" "$RESULT"
            ;;
        SKIPPED*)
            printf "%-25s ${YELLOW}%s${NC}\n" "$TEST_ID" "$RESULT"
            ;;
        *)
            printf "%-25s %s\n" "$TEST_ID" "$RESULT"
            ;;
    esac
done

echo ""
echo "────────────────────────────────────────────────"
echo "  Passed:  $PASSED"
echo "  Failed:  $FAILED"
echo "  Skipped: $SKIPPED"
echo "────────────────────────────────────────────────"
echo ""

[ $FAILED -eq 0 ] && exit 0 || exit 1
