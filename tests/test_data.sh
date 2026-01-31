#!/bin/bash
# Test data-container functionality

IMAGE="hyperflowwms/1000genome-data:1.0"
FAILED=0

echo "=== Testing data-container ==="
echo ""

# Test 1: VCF files exist (check a few)
for chr in 1 5 10; do
    echo -n "Test: VCF file chr$chr exists... "
    if docker run --rm $IMAGE test -f /data/20130502/ALL.chr${chr}.250000.vcf.gz; then
        echo "PASS"
    else
        echo "FAIL"
        FAILED=$((FAILED + 1))
    fi
done

# Test 2: Annotation files exist (check a few)
for chr in 1 5 10; do
    echo -n "Test: Annotation file chr$chr exists... "
    if docker run --rm $IMAGE test -f /data/20130502/ALL.chr${chr}.phase3_shapeit2_mvncall_integrated_v5.20130502.sites.annotation.vcf.gz; then
        echo "PASS"
    else
        echo "FAIL"
        FAILED=$((FAILED + 1))
    fi
done

# Test 3: All 10 VCF files exist
echo -n "Test: All 10 VCF files exist... "
COUNT=$(docker run --rm $IMAGE sh -c "ls /data/20130502/ALL.chr*.250000.vcf.gz 2>/dev/null | wc -l")
if [ "$COUNT" = "10" ]; then
    echo "PASS"
else
    echo "FAIL (found $COUNT)"
    FAILED=$((FAILED + 1))
fi

# Test 4: All 10 annotation files exist
echo -n "Test: All 10 annotation files exist... "
COUNT=$(docker run --rm $IMAGE sh -c "ls /data/20130502/*.annotation.vcf.gz 2>/dev/null | wc -l")
if [ "$COUNT" = "10" ]; then
    echo "PASS"
else
    echo "FAIL (found $COUNT)"
    FAILED=$((FAILED + 1))
fi

# Test 5: columns.txt exists
echo -n "Test: columns.txt exists... "
if docker run --rm $IMAGE test -f /data/20130502/columns.txt; then
    echo "PASS"
else
    echo "FAIL"
    FAILED=$((FAILED + 1))
fi

# Test 6: Population files exist
POPULATIONS="AFR ALL AMR EAS EUR GBR SAS"
for pop in $POPULATIONS; do
    echo -n "Test: Population file $pop exists... "
    if docker run --rm $IMAGE test -f /data/populations/$pop; then
        echo "PASS"
    else
        echo "FAIL"
        FAILED=$((FAILED + 1))
    fi
done

# Test 7: workflow.json exists
echo -n "Test: workflow.json exists... "
if docker run --rm $IMAGE test -f /data/workflow.json; then
    echo "PASS"
else
    echo "FAIL"
    FAILED=$((FAILED + 1))
fi

# Test 8: prepare_data.sh exists
echo -n "Test: prepare_data.sh exists... "
if docker run --rm $IMAGE test -f /prepare_data.sh; then
    echo "PASS"
else
    echo "FAIL"
    FAILED=$((FAILED + 1))
fi

# Test 9: prepare_data.sh works (quick test with small output dir)
echo -n "Test: prepare_data.sh runs... "
WORKDIR=$(mktemp -d)
if docker run --rm -v "$WORKDIR:/mnt/data" $IMAGE sh /prepare_data.sh > /dev/null 2>&1; then
    # Check some files were created
    if [ -f "$WORKDIR/workflow.json" ] && [ -d "$WORKDIR/20130502" ] && [ -d "$WORKDIR/populations" ]; then
        echo "PASS"
    else
        echo "FAIL (files not created)"
        FAILED=$((FAILED + 1))
    fi
else
    echo "FAIL (script error)"
    FAILED=$((FAILED + 1))
fi
rm -rf "$WORKDIR"

echo ""
echo "=== Results: $FAILED failed ==="
exit $FAILED
