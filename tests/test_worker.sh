#!/bin/bash
# Test worker-image functionality

IMAGE="hyperflowwms/1000genome-worker:1.0-je1.3.4"
FAILED=0

echo "=== Testing worker-image ==="
echo ""

# Test 1: Inherits from worker-base (has Python)
echo -n "Test: Python 3 inherited... "
if docker run --rm --entrypoint "" $IMAGE python3 --version > /dev/null 2>&1; then
    echo "PASS"
else
    echo "FAIL"
    FAILED=$((FAILED + 1))
fi

# Test 2: Analysis scripts inherited
echo -n "Test: Analysis scripts inherited... "
if docker run --rm --entrypoint "" $IMAGE test -f /1000genome/scripts/individuals.py; then
    echo "PASS"
else
    echo "FAIL"
    FAILED=$((FAILED + 1))
fi

# Test 3: job-executor is installed
echo -n "Test: @hyperflow/job-executor installed... "
if docker run --rm --entrypoint "" $IMAGE npm list -g @hyperflow/job-executor > /dev/null 2>&1; then
    echo "PASS"
else
    echo "FAIL"
    FAILED=$((FAILED + 1))
fi

# Test 4: dumb-init is installed
echo -n "Test: dumb-init installed... "
if docker run --rm --entrypoint "" $IMAGE test -x /usr/local/bin/dumb-init; then
    echo "PASS"
else
    echo "FAIL"
    FAILED=$((FAILED + 1))
fi

# Test 5: hflow-job-execute command available
echo -n "Test: hflow-job-execute command available... "
if docker run --rm --entrypoint "" $IMAGE which hflow-job-execute > /dev/null 2>&1; then
    echo "PASS"
else
    echo "FAIL"
    FAILED=$((FAILED + 1))
fi

# Test 6: ENTRYPOINT is dumb-init
echo -n "Test: ENTRYPOINT is dumb-init... "
ENTRYPOINT=$(docker inspect --format='{{json .Config.Entrypoint}}' $IMAGE 2>/dev/null)
if echo "$ENTRYPOINT" | grep -q "dumb-init"; then
    echo "PASS"
else
    echo "FAIL (got: $ENTRYPOINT)"
    FAILED=$((FAILED + 1))
fi

echo ""
echo "=== Results: $FAILED failed ==="
exit $FAILED
