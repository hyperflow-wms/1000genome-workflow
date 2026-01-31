#!/bin/bash
# Run all tests for 1000genome-workflow

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOTAL_FAILED=0

echo "========================================"
echo "  1000genome-workflow Test Suite"
echo "========================================"
echo ""

# Run each test script
for test in test_worker_base.sh test_worker.sh test_generator.sh test_data.sh; do
    if [ -f "$SCRIPT_DIR/$test" ]; then
        echo ""
        bash "$SCRIPT_DIR/$test"
        RESULT=$?
        TOTAL_FAILED=$((TOTAL_FAILED + RESULT))
    fi
done

echo ""
echo "========================================"
if [ $TOTAL_FAILED -eq 0 ]; then
    echo "  All tests passed!"
else
    echo "  Total failures: $TOTAL_FAILED"
fi
echo "========================================"

exit $TOTAL_FAILED
