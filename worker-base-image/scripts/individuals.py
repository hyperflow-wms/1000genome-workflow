#!/usr/bin/env python3

import os
import sys
import re
import time
import tarfile
import shutil


def compress(output, input_dir):
    with tarfile.open(output, "w:gz") as file:
        file.add(input_dir, arcname=os.path.basename(input_dir))

def readfile(file):
    with open(file, 'r') as f:
        content = f.readlines()
    return content

def processing(inputfile, columfile, c, counter, stop, total):
    print('= Now processing chromosome: {}'.format(c), flush=True)
    tic = time.perf_counter()

    counter = int(counter)
    stop = int(stop)
    total = int(total)

    ### step 0
    unzipped = 'ALL.chr{}.individuals.vcf'.format(c)
    # unzipped = os.path.splitext(inputfile)[0]  # Remove .gz

    # shutil.move(inputfile, unzipped)

    # if not os.path.exists(unzipped):
    #     decompress(inputfile, unzipped)

    rawdata = readfile(inputfile)

    # Self-discover total if -1 sentinel is passed
    if total == -1:
        total = len(rawdata)
        print("== Auto-discovered total lines: {}".format(total), flush=True)

    ending = min(stop, total)

    ### step 2
    ## Giving a different directory name (chromosome no-counter) for each individuals job
    ndir = 'chr{}n-{}/'.format(c, counter)
    os.makedirs(ndir, exist_ok=True)

    ### step 3
    # In the bash version, counter started at 1 but in Python we start at 0. 
    # counter = max(0, counter - 1)  # The max ensure that we don't do -1 if the user set counter 0 directly
    print("== Total number of lines: {}".format(total), flush=True)
    print("== Processing {} from line {} to {}".format(unzipped, counter, stop), flush=True)

    # We consider the line from counter to stop and we don't over total, then we remove lines starting with '#'
    #sed -n "$counter"','"$stop"'p;'"$total"'q' $unzipped | grep -ve "#" > cc
    regex = re.compile('(?!#)')
    data = list(filter(regex.match, rawdata[counter:ending]))
    data = [x.rstrip('\n') for x in data] # Remove \n from words 

    columndata = readfile(columfile)[0].rstrip('\n').split('\t')

    start_data = 9  # where the real data start, the first 0|1, 1|1, 1|0 or 0|0
    # position of the last element (normally equals to len(data[0].split(' '))
    #end_data = 2504
    end_data = len(columndata) - start_data
    print("== Number of columns {}".format(end_data), flush=True)

    # Precompute per-line invariants once, then fill per-individual buffers in a
    # single pass. Each line is split a single time and its POS/ID/REF/ALT/AF are
    # computed once, instead of re-splitting and re-parsing every line once per
    # individual.
    kept = []  # (row_text, af_hi, genotype_columns) per line that parses
    for line in data:
        fields = line.split('\t')
        try:
            af_value = fields[7].split(';')[8].split('=')[1]
            # Keep only the first value if more than one (matches the awk logic)
            af_f = float(af_value.split(',')[0]) if ',' in af_value else float(af_value)
        except (ValueError, IndexError):
            continue
        row = "{0}        {1}    {2}    {3}    {4}\n".format(
            fields[1], fields[2], fields[3], fields[4], af_value)
        kept.append((row, af_f >= 0.5, fields[start_data:]))

    print("== Precomputed {} lines, filling {} individuals".format(len(kept), end_data), flush=True)
    tic_fill = time.perf_counter()

    buffers = [[] for _ in range(end_data)]
    for row, hi, gts in kept:
        for i in range(end_data):
            # We keep the mutation for an individual depending on the allele and AF
            allele = gts[i].split('|')[0]
            if (hi and allele == '0') or (not hi and allele == '1'):
                buffers[i].append(row)

    for i in range(0, end_data):
        name = columndata[i + start_data]
        filename = "{}/chr{}.{}".format(ndir, c, name)
        with open(filename, 'w') as f:
            f.write(''.join(buffers[i]))

    print("== Filled {} files in {:0.2f} sec".format(end_data, time.perf_counter()-tic_fill), flush=True)

    outputfile = "chr{}n-{}-{}.tar.gz".format(c, counter, stop)
    print("== Done. Zipping {} files into {}.".format(end_data, outputfile), flush=True)

    # tar -zcf .. /$outputfile .
    compress(outputfile, ndir)

    # Cleaning temporary files
    try:
        shutil.rmtree(ndir)
    except OSError as e:
        print("Error: %s : %s" % (ndir, e.strerror), flush=True)

    print("= Chromosome {} processed in {:0.2f} seconds.".format(c, time.perf_counter() - tic), flush=True)

if __name__ == "__main__":
    inputfile = sys.argv[1]
    c = sys.argv[2]
    counter = sys.argv[3]
    stop = sys.argv[4]
    total = sys.argv[5]
    columfile = 'columns.txt'

    processing(inputfile=inputfile, 
            columfile=columfile, 
            c=c, 
            counter=counter, 
            stop=stop,
            total=total)
