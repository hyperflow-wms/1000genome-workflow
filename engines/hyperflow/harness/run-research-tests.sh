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
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
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
# ============================================================================
# Timing
# ============================================================================
# Phases are timed by hooking log_phase: announcing a phase closes the previous
# one and opens the next. A test that stops early leaves its last phase open, so
# the loop closes it when the following test starts and once more after the loop.

SUITE_START=$(date +%s)
PHASE_LABEL=""
PHASE_START=0
CURRENT_TEST=""
declare -A TEST_STARTED_AT
declare -A TEST_ELAPSED
declare -A TEST_PHASES

fmt_duration() {
    local s=${1:-0}
    if [ "$s" -ge 3600 ]; then
        printf "%dh %02dm %02ds" $((s / 3600)) $(((s % 3600) / 60)) $((s % 60))
    elif [ "$s" -ge 60 ]; then
        printf "%dm %02ds" $((s / 60)) $((s % 60))
    else
        printf "%ds" "$s"
    fi
}

close_phase() {
    [ -z "$PHASE_LABEL" ] && return 0
    local elapsed=$(( $(date +%s) - PHASE_START ))
    if [ -n "$CURRENT_TEST" ]; then
        TEST_PHASES[$CURRENT_TEST]+="${PHASE_LABEL}:${elapsed} "
    fi
    PHASE_LABEL=""
}

close_test() {
    [ -z "$CURRENT_TEST" ] && return 0
    TEST_ELAPSED[$CURRENT_TEST]=$(( $(date +%s) - ${TEST_STARTED_AT[$CURRENT_TEST]:-$(date +%s)} ))
}

log_phase() {
    close_phase
    # Keep just the phase name: "Phase 3: EXTRACT (...)" -> "EXTRACT". The
    # summary banner is not a phase and must not be timed.
    case "$1" in
        "Phase "*) PHASE_LABEL=$(echo "$1" | sed -E 's/^Phase [0-9]+: ([A-Za-z-]+).*/\1/') ;;
        "EXECUTE-ONLY"*) PHASE_LABEL="SETUP" ;;
        *) PHASE_LABEL="" ;;
    esac
    PHASE_START=$(date +%s)

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
EXECUTE_ONLY=false
DEV_IMAGES=false
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
  --execute-only       Re-run EXECUTE against an existing workflow directory.
                       Regenerates workflow.json from the data.csv already
                       there, clears previous execution outputs, and keeps the
                       extracted VCFs -- so no download and no LLM call.
  -y, --yes            Non-interactive mode, proceed through all phases

Parallelism Options (mutually exclusive):
  -p, --parallelism PRESET   Preset: small(10), medium(50), large(250)
  --vcpus N                  Compute optimal parallelism for N vCPUs
  --ind-jobs N               Explicit ind_jobs value

Sampling Options:
  --max-samples-per-pop N    Cap individuals per population in columns.txt

General Options:
  --dev-images         Use locally built engine/worker images
                       (hyperflowwms/hyperflow:dev and
                       hyperflowwms/1000genome-worker:dev; build them with
                       ./build-dev-images.sh)
  -v, --verbose        Verbose output (also sets workflow console log
                       level to debug)
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
        --execute-only)
            EXECUTE_ONLY=true
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
        --max-samples-per-pop)
            MAX_SAMPLES_PER_POP="$2"
            shift 2
            ;;
        --dev-images)
            DEV_IMAGES=true
            shift
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
    # Settle the previous test's timers before starting this one: whichever
    # phase it stopped in is still open if it exited through a `continue`.
    close_phase
    close_test

    log_test "$TEST_ID"

    CURRENT_TEST="$TEST_ID"
    TEST_STARTED_AT[$TEST_ID]=$(date +%s)

    WORKFLOW_DIR="$SCRIPT_DIR/workflow-$TEST_ID"

    if [ "$EXECUTE_ONLY" = true ]; then
        # Reuse the extracted data already in the directory: regenerate
        # workflow.json from it, clear only what a previous execution wrote,
        # and leave the VCFs, columns.txt, and intent.json in place.
        if [ ! -f "$WORKFLOW_DIR/data.csv" ] || [ ! -f "$WORKFLOW_DIR/intent.json" ]; then
            log_error "--execute-only needs data.csv and intent.json in $WORKFLOW_DIR"
            log_info "Run the test without --execute-only first to extract the data"
            FAILED=$((FAILED + 1))
            RESULTS[$TEST_ID]="FAILED (no extracted data)"
            continue
        fi

        log_phase "EXECUTE-ONLY: reusing extracted data in workflow-$TEST_ID"

        INTENT_JSON=$(cat "$WORKFLOW_DIR/intent.json")
        INTENT_POPULATIONS=$(echo "$INTENT_JSON" | python3 -c "import sys,json; print(','.join(json.load(sys.stdin)['populations']))")

        rm -rf "$WORKFLOW_DIR"/chr*n-*/ "$WORKFLOW_DIR/logs-hf" "$WORKFLOW_DIR/plots_no_sift"
        rm -f "$WORKFLOW_DIR"/chr*n*.tar.gz "$WORKFLOW_DIR"/chr*-*.tar.gz \
              "$WORKFLOW_DIR"/sifted.SIFT.*.txt "$WORKFLOW_DIR"/wftrace-*.log

        # Same flag precedence the full GENERATE phase uses: an explicit
        # ind_jobs wins, otherwise a memory-budget preset, plus an optional
        # vCPU override. With none of them the recommendation decides.
        GEN_ENV_FLAG=""
        if [ -n "$IND_JOBS" ]; then
            GEN_ENV_FLAG="--ind-jobs $IND_JOBS"
        elif [ -n "$PARALLELISM" ]; then
            GEN_ENV_FLAG="--parallelism $PARALLELISM"
        fi
        [ -n "$VCPUS" ] && GEN_ENV_FLAG="$GEN_ENV_FLAG --vcpus $VCPUS"

        if ! (cd "$WORKFLOW_DIR" && python3 -m workflow_composer.cli generate \
                --data-csv data.csv \
                --populations "$INTENT_POPULATIONS" \
                $GEN_ENV_FLAG \
                -o workflow.json); then
            log_error "Workflow regeneration failed"
            FAILED=$((FAILED + 1))
            RESULTS[$TEST_ID]="FAILED (generate)"
            continue
        fi

        # Same tool the full path uses, so both agree on the concurrency dial.
        MAX_PARALLELISM_VALUE=$(python3 "$FRAMEWORK_PY" adaptive-parallelism \
            --intent-json "$INTENT_JSON" \
            --vcpus "${VCPUS:-$(nproc)}" \
            | python3 -c "import sys,json; print(json.load(sys.stdin)['max_parallelism'])")
        log_info "max_parallelism=$MAX_PARALLELISM_VALUE"
    else
        rm -rf "$WORKFLOW_DIR"
        mkdir -p "$WORKFLOW_DIR"
    fi

    if [ "$EXECUTE_ONLY" = false ]; then

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

    TRANSFER_MB=$(echo "$PLAN_JSON" | python3 -c "import sys,json; print(round(json.load(sys.stdin)['data_preparation']['estimated_transfer_mb'], 2))")
    DISK_MB=$(echo "$PLAN_JSON" | python3 -c "import sys,json; print(round(json.load(sys.stdin)['data_preparation'].get('estimated_disk_mb', 0), 1))")
    TASK_COUNT=$(echo "$PLAN_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['task_count'])")
    FILE_COUNT=$(echo "$PLAN_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['file_count'])")

    log_success "Plan created"
    log_info "  Tasks: $TASK_COUNT"
    log_info "  Files: $FILE_COUNT"
    log_info "  Estimated: ~${TRANSFER_MB} MB transfer, ~${DISK_MB} MB on disk"

    # Show human-readable plan summary
    DESCRIPTION=$(echo "$PLAN_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['description'])")
    RATIONALE=$(echo "$PLAN_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['rationale'])")
    echo ""
    echo -e "${CYAN}  Description:${NC} $DESCRIPTION"
    echo -e "${CYAN}  Rationale:${NC} $RATIONALE"
    echo ""

    # Show both parallelism dials and the reason if using --vcpus,
    # computed by the same recommend_parallelism tool the composer uses
    #.
    if [ -n "$VCPUS" ]; then
        ADAPTIVE_INFO=$(python3 "$FRAMEWORK_PY" adaptive-parallelism \
            --intent-json "$INTENT_JSON" \
            --vcpus "$VCPUS")
        COMPUTED_IND_JOBS=$(echo "$ADAPTIVE_INFO" | python3 -c "import sys,json; print(json.load(sys.stdin)['ind_jobs'])")
        COMPUTED_MAX_PARALLELISM=$(echo "$ADAPTIVE_INFO" | python3 -c "import sys,json; print(json.load(sys.stdin)['max_parallelism'])")
        PARALLELISM_REASON=$(echo "$ADAPTIVE_INFO" | python3 -c "import sys,json; print(json.load(sys.stdin)['reason'])")
        log_info "  Adaptive parallelism: $PARALLELISM_REASON"
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

            docker run --rm -u "$(id -u):$(id -g)" -v "$WORKFLOW_DIR:/output" "$DATA_IMAGE" \
                sh -c "
                    cp /data/20130502/ALL.chr1.250000.vcf.gz /output/ && gunzip -f /output/ALL.chr1.250000.vcf.gz
                    cp /data/20130502/columns.txt /output/
                    cp /data/populations/* /output/
                    cp /data/20130502/ALL.chr1.phase3_shapeit2_mvncall_integrated_v5.20130502.sites.annotation.vcf.gz /output/ && gunzip -f /output/ALL.chr1*.annotation.vcf.gz
                " 2>/dev/null

            cd "$WORKFLOW_DIR"
            cut -f1-39 columns.txt > columns_30.txt && mv columns_30.txt columns.txt
            head -n 10001 ALL.chr1.250000.vcf > ALL.chr1.10000.vcf && mv ALL.chr1.10000.vcf ALL.chr1.250000.vcf

            # Stage the data manifest where GENERATE expects it. skip_extract
            # bypasses extraction (which would otherwise produce data.csv), so
            # copy the micro manifest the case ships as data-micro.csv.
            cp "$SCRIPT_DIR/data-micro.csv" "$WORKFLOW_DIR/data.csv"

            log_success "Micro test data prepared"
        fi
    else
        # Full extraction via extract-data.sh
        EXTRACT_SCRIPT="$REPO_ROOT/workflow-composer/src/workflow_composer/scripts/extract-data.sh"
        if ! bash "$EXTRACT_SCRIPT" \
            --plan "$WORKFLOW_DIR/plan.json" \
            --output-dir "$WORKFLOW_DIR" \
            --docker-image "$TABIX_IMAGE"; then
            log_error "Data extraction failed"
            FAILED=$((FAILED + 1))
            RESULTS[$TEST_ID]="FAILED (extract)"
            continue
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

    if [ ! -f "$WORKFLOW_DIR/data.csv" ]; then
        log_warning "No data.csv found - skipping workflow generation"
        SKIPPED=$((SKIPPED + 1))
        RESULTS[$TEST_ID]="SKIPPED (no data)"
        continue
    fi

    # Determine parallelism for CLI, and resolve MAX_PARALLELISM (the
    # HF_VAR_REDIS_CMD_MAX_PARALLELISM concurrency dial)
    # from the same recommend_parallelism tool instead of a hardcoded
    # constant.
    CLI_PARALLELISM=""
    MAX_PARALLELISM_VALUE=""
    if [ -n "$IND_JOBS" ]; then
        CLI_PARALLELISM="--ind-jobs $IND_JOBS"
    elif [ -n "$VCPUS" ]; then
        # Compute both dials using the framework
        ADAPTIVE_RESULT=$(python3 "$FRAMEWORK_PY" adaptive-parallelism \
            --intent-json "$INTENT_JSON" \
            --vcpus "$VCPUS")
        ADAPTIVE_JOBS=$(echo "$ADAPTIVE_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['ind_jobs'])")
        MAX_PARALLELISM_VALUE=$(echo "$ADAPTIVE_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['max_parallelism'])")
        ADAPTIVE_REASON=$(echo "$ADAPTIVE_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['reason'])")
        CLI_PARALLELISM="--ind-jobs $ADAPTIVE_JOBS"
        log_info "Adaptive parallelism: $ADAPTIVE_REASON"
    elif [ -n "$PARALLELISM" ]; then
        CLI_PARALLELISM="--parallelism $PARALLELISM"
    fi

    if [ -z "$MAX_PARALLELISM_VALUE" ]; then
        # --parallelism/--ind-jobs runs don't name a vCPU count. Fall back to
        # the same tool with the "aws" environment's default vCPUs
        # (workflow_composer.core.environment's "aws" profile) rather than
        # reintroducing a second, hand-picked constant.
        FALLBACK_RESULT=$(python3 "$FRAMEWORK_PY" adaptive-parallelism \
            --intent-json "$INTENT_JSON" \
            --vcpus 8)
        MAX_PARALLELISM_VALUE=$(echo "$FALLBACK_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['max_parallelism'])")
    fi

    # Pass intent populations to generator
    INTENT_POPULATIONS=$(echo "$INTENT_JSON" | python3 -c "import sys,json; print(','.join(json.load(sys.stdin)['populations']))")
    CLI_POPULATIONS="--populations $INTENT_POPULATIONS"

    CLI_MAX_SAMPLES=""
    [ -n "$MAX_SAMPLES_PER_POP" ] && CLI_MAX_SAMPLES="--max-samples-per-pop $MAX_SAMPLES_PER_POP"

    cd "$REPO_ROOT/workflow-composer"
    python3 -m workflow_composer.cli generate \
        --data-csv "$WORKFLOW_DIR/data.csv" \
        $CLI_PARALLELISM \
        $CLI_POPULATIONS \
        $CLI_MAX_SAMPLES \
        --output "$WORKFLOW_DIR/workflow.json" 2>/dev/null

    GEN_TASKS=$(python3 -c "import json; print(len(json.load(open('$WORKFLOW_DIR/workflow.json'))['processes']))")
    GEN_FILES=$(python3 -c "import json; print(len(json.load(open('$WORKFLOW_DIR/workflow.json'))['signals']))")

    log_success "Final workflow generated"
    log_info "  Tasks: $GEN_TASKS"
    log_info "  Files: $GEN_FILES"

    if [ "$EST_TASKS" != "$GEN_TASKS" ]; then
        log_info "  Task difference from estimate: $((GEN_TASKS - EST_TASKS))"
    fi

    # The estimated workflow is only a useful preview if regenerating it from
    # the exact variant count repartitions the individuals stage and changes
    # nothing else. A different population set, or a missing merge or sifting
    # step, means the reviewed workflow was not this one.
    if [ -f "$WORKFLOW_DIR/workflow-estimated.json" ]; then
        CMP_RESULT=$(python3 "$FRAMEWORK_PY" compare-workflows \
            --estimated "$WORKFLOW_DIR/workflow-estimated.json" \
            --final "$WORKFLOW_DIR/workflow.json")
        if echo "$CMP_RESULT" | python3 -c "import sys,json; sys.exit(0 if json.load(sys.stdin)['ok'] else 1)"; then
            CMP_SUMMARY=$(echo "$CMP_RESULT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
parts = [f\"individuals {d['estimated_individuals']} -> {d['final_individuals']}\"]
if d.get('variant_diff_pct') is not None:
    parts.append(
        f\"variants {d['estimated_variants']:,} -> {d['final_variants']:,} \"
        f\"({d['variant_diff_pct']:+}%)\"
    )
parts.append('other stages unchanged')
print(', '.join(parts))")
            log_success "  Estimate held: $CMP_SUMMARY"
        else
            echo "$CMP_RESULT" | python3 -c "
import sys, json
for p in json.load(sys.stdin)['problems']:
    print(f'  {p}')" >&2
            log_error "Final workflow diverges from the estimate beyond the individuals stage"
            FAILED=$((FAILED + 1))
            RESULTS[$TEST_ID]="FAILED (estimate diverged)"
            continue
        fi
    fi

    if [ "$STOP_POINT" = "generate" ]; then
        log_warning "Stopping after GENERATE phase"
        SKIPPED=$((SKIPPED + 1))
        RESULTS[$TEST_ID]="SKIPPED (stop-before-execute)"
        continue
    fi

    fi  # phases 1-4, skipped entirely under --execute-only

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
    export MAX_PARALLELISM=$MAX_PARALLELISM_VALUE

    if [ "$DEV_IMAGES" = true ]; then
        export HF_ENGINE_IMAGE=hyperflowwms/hyperflow:dev
        export HF_VAR_WORKER_CONTAINER=hyperflowwms/1000genome-worker:dev
    fi
    if [ "$VERBOSE" = true ]; then
        export HF_VAR_CONSOLE_LOG_LEVEL=debug
    else
        export HF_VAR_CONSOLE_LOG_LEVEL=${HF_VAR_CONSOLE_LOG_LEVEL:-info}
    fi

    # The engine writes to a pipe inside its container, so tell it whether the
    # far end of that pipe (this script's stdout) is a terminal
    if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
        export FORCE_COLOR=${FORCE_COLOR:-1}
    fi

    cd "$SCRIPT_DIR"

    if docker-compose up --abort-on-container-exit 2>&1; then
        log_success "Workflow execution completed"

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
# The last test leaves its final phase open, exactly as an early exit would.
close_phase
close_test

log_phase "TEST SUMMARY"

echo ""
printf "%-25s %-30s %s\n" "TEST" "RESULT" "TIME"
printf "%-25s %-30s %s\n" "-------------------------" "------------------------------" "----------"
for TEST_ID in "${TEST_IDS[@]}"; do
    RESULT="${RESULTS[$TEST_ID]:-UNKNOWN}"
    ELAPSED=$(fmt_duration "${TEST_ELAPSED[$TEST_ID]:-0}")
    case $RESULT in
        PASSED*)  COLOUR="$GREEN" ;;
        FAILED*)  COLOUR="$RED" ;;
        SKIPPED*) COLOUR="$YELLOW" ;;
        *)        COLOUR="$NC" ;;
    esac
    printf "%-25s ${COLOUR}%-30s${NC} %s\n" "$TEST_ID" "$RESULT" "$ELAPSED"

    # Per-phase breakdown, so a slow run says which phase was slow.
    if [ -n "${TEST_PHASES[$TEST_ID]:-}" ]; then
        BREAKDOWN=""
        for ENTRY in ${TEST_PHASES[$TEST_ID]}; do
            PHASE_NAME="${ENTRY%%:*}"
            PHASE_SECS="${ENTRY##*:}"
            BREAKDOWN="${BREAKDOWN}${PHASE_NAME} $(fmt_duration "$PHASE_SECS"), "
        done
        printf "%-25s %s\n" "" "${BREAKDOWN%, }"
    fi
done

SUITE_ELAPSED=$(( $(date +%s) - SUITE_START ))
echo ""
echo "────────────────────────────────────────────────"
echo "  Passed:  $PASSED"
echo "  Failed:  $FAILED"
echo "  Skipped: $SKIPPED"
echo "  Total:   $(fmt_duration "$SUITE_ELAPSED")"
echo "────────────────────────────────────────────────"
echo ""

[ $FAILED -eq 0 ] && exit 0 || exit 1
