#!/bin/bash
# Test workflow-generator functionality

IMAGE="hyperflowwms/1000genome-generator:1.0"
FAILED=0
WORKDIR=$(mktemp -d)

echo "=== Testing workflow-generator ==="
echo ""

# Test 1: Python 3 is installed
echo -n "Test: Python 3 installed... "
if docker run --rm $IMAGE python3 --version > /dev/null 2>&1; then
    echo "PASS"
else
    echo "FAIL"
    FAILED=$((FAILED + 1))
fi

# Test 2: hflow-convert-dax is available
echo -n "Test: hflow-convert-dax available... "
if docker run --rm $IMAGE which hflow-convert-dax > /dev/null 2>&1; then
    echo "PASS"
else
    echo "FAIL"
    FAILED=$((FAILED + 1))
fi

# Test 3: daxgen.py exists
echo -n "Test: daxgen.py exists... "
if docker run --rm $IMAGE test -f /1000genome-workflow/daxgen.py; then
    echo "PASS"
else
    echo "FAIL"
    FAILED=$((FAILED + 1))
fi

# Test 4: Pegasus library exists
echo -n "Test: Pegasus library exists... "
if docker run --rm $IMAGE test -d /1000genome-workflow/Pegasus; then
    echo "PASS"
else
    echo "FAIL"
    FAILED=$((FAILED + 1))
fi

# Test 5: data.csv exists
echo -n "Test: data.csv exists... "
if docker run --rm $IMAGE test -f /1000genome-workflow/data.csv; then
    echo "PASS"
else
    echo "FAIL"
    FAILED=$((FAILED + 1))
fi

# Test 6: populations directory exists
echo -n "Test: populations directory exists... "
if docker run --rm $IMAGE test -d /1000genome-workflow/data/populations; then
    echo "PASS"
else
    echo "FAIL"
    FAILED=$((FAILED + 1))
fi

# Test 7: Generate workflow
echo -n "Test: Generate workflow... "
docker run --rm -v "$WORKDIR:/output" $IMAGE sh -c "cd /1000genome-workflow && ./generate_workflow.sh && cp workflow.json /output/" > /dev/null 2>&1
if [ -f "$WORKDIR/workflow.json" ]; then
    echo "PASS"
else
    echo "FAIL"
    FAILED=$((FAILED + 1))
fi

# Test 8: Generated workflow is valid JSON
echo -n "Test: workflow.json is valid JSON... "
if python3 -c "import json; json.load(open('$WORKDIR/workflow.json'))" 2>/dev/null; then
    echo "PASS"
else
    echo "FAIL"
    FAILED=$((FAILED + 1))
fi

# Test 9: Workflow has expected structure
echo -n "Test: workflow.json has processes... "
if python3 -c "import json; wf=json.load(open('$WORKDIR/workflow.json')); assert 'processes' in wf and len(wf['processes']) > 0" 2>/dev/null; then
    echo "PASS"
else
    echo "FAIL"
    FAILED=$((FAILED + 1))
fi

echo -n "Test: workflow.json has signals... "
if python3 -c "import json; wf=json.load(open('$WORKDIR/workflow.json')); assert 'signals' in wf and len(wf['signals']) > 0" 2>/dev/null; then
    echo "PASS"
else
    echo "FAIL"
    FAILED=$((FAILED + 1))
fi

# Cleanup
rm -rf "$WORKDIR"

echo ""
echo "=== Results: $FAILED failed ==="
exit $FAILED
