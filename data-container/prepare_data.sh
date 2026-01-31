#!/bin/sh
# Prepare 1000genome input data
# Decompresses VCF and annotation files from /data to target directory

TARGET_DIR="${1:-/mnt/data}"

echo "Preparing 1000genome data in $TARGET_DIR..."

# Copy compressed files to target
cp -r /data/20130502 "$TARGET_DIR/"

# Copy population files
cp -r /data/populations "$TARGET_DIR/"

# Copy workflow definition
cp /data/workflow.json "$TARGET_DIR/"

cd "$TARGET_DIR/20130502"

# Decompress VCF files
echo "Decompressing VCF files..."
for f in ALL.chr*.250000.vcf.gz; do
    if [ -f "$f" ]; then
        echo "  $f"
        gunzip -kf "$f"
    fi
done

# Decompress annotation files
echo "Decompressing annotation files..."
for f in ALL.chr*.annotation.vcf.gz; do
    if [ -f "$f" ]; then
        echo "  $f"
        gunzip -kf "$f"
    fi
done

echo ""
echo "Done. Contents:"
echo "=== $TARGET_DIR ==="
ls -lh "$TARGET_DIR/"
echo ""
echo "=== $TARGET_DIR/20130502 ==="
ls -lh "$TARGET_DIR/20130502/"
echo ""
echo "=== $TARGET_DIR/populations ==="
ls -lh "$TARGET_DIR/populations/"
