#!/bin/bash
# Test worker-base-image functionality

IMAGE="hyperflowwms/1000genome-worker-base:1.0"
FAILED=0

echo "=== Testing worker-base-image ==="
echo ""

# Test 1: Python 3 is installed
echo -n "Test: Python 3 installed... "
if docker run --rm $IMAGE python3 --version > /dev/null 2>&1; then
    echo "PASS"
else
    echo "FAIL"
    FAILED=$((FAILED + 1))
fi

# Test 2: numpy is available
echo -n "Test: numpy available... "
if docker run --rm $IMAGE python3 -c "import numpy; print(numpy.__version__)" > /dev/null 2>&1; then
    echo "PASS"
else
    echo "FAIL"
    FAILED=$((FAILED + 1))
fi

# Test 3: matplotlib is available
echo -n "Test: matplotlib available... "
if docker run --rm $IMAGE python3 -c "import matplotlib; print(matplotlib.__version__)" > /dev/null 2>&1; then
    echo "PASS"
else
    echo "FAIL"
    FAILED=$((FAILED + 1))
fi

# Test 4: Analysis scripts exist
SCRIPTS="individuals.py individuals_merge.py sifting.py frequency.py mutation_overlap.py"
for script in $SCRIPTS; do
    echo -n "Test: $script exists... "
    if docker run --rm $IMAGE test -f /1000genome/scripts/$script; then
        echo "PASS"
    else
        echo "FAIL"
        FAILED=$((FAILED + 1))
    fi
done

# Test 5: Scripts are importable (no syntax errors)
echo -n "Test: individuals.py importable... "
if docker run --rm $IMAGE python3 -c "import sys; sys.path.insert(0, '/1000genome/scripts'); import individuals" 2>/dev/null; then
    echo "PASS"
else
    echo "FAIL"
    FAILED=$((FAILED + 1))
fi

echo ""
echo "=== Results: $FAILED failed ==="
exit $FAILED
