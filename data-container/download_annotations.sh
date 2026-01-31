#!/bin/sh
# Download 1000genome annotation files from FTP
# These files are required to build the data-container image

DATA_DIR="${1:-data/20130502}"

echo "Downloading annotation files to $DATA_DIR..."

cd "$DATA_DIR" || exit 1

for i in 1 2 3 4 5 6 7 8 9 10; do
    file="ALL.chr${i}.phase3_shapeit2_mvncall_integrated_v5.20130502.sites.annotation.vcf.gz"
    if [ -f "$file" ]; then
        echo "  $file already exists, skipping"
    else
        echo "  Downloading $file..."
        wget -q "ftp://ftp.1000genomes.ebi.ac.uk/vol1/ftp/release/20130502/supporting/functional_annotation/filtered/$file"
    fi
done

echo "Done."
ls -lh *.annotation.vcf.gz
