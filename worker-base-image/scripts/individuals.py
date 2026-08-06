#!/usr/bin/env python3

import sys
import itertools
import time

import archive


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

    # This job handles the VARIANT range [counter, ending), 0-based and half
    # open, counting only data lines. When total == -1 the range runs to EOF.
    #
    # The range must never be applied to raw file lines. A VCF carries a header
    # (253 lines in the 20130502 release) that would occupy the front of the
    # window, so the tail of the region would silently go unprocessed -- and a
    # region with fewer variants than the header would process nothing at all
    # while still exiting 0 and writing its archive. See RFC-005.
    ending = stop if total == -1 else min(stop, total)

    print("== Processing {} from line {} to {}".format(unzipped, counter, stop), flush=True)

    columndata = readfile(columfile)[0].rstrip('\n').split('\t')

    start_data = 9  # where the real data start, the first 0|1, 1|1, 1|0 or 0|0
    # position of the last element (normally equals to len(data[0].split(' '))
    #end_data = 2504
    end_data = len(columndata) - start_data
    print("== Number of columns {}".format(end_data), flush=True)

    # Stream the input VCF over this job's line range [counter, ending), filling
    # per-individual output buffers in a single pass. Each line is read, split
    # once, used to append its row to the matching individuals' buffers, then
    # discarded. Memory stays proportional to the (small) output rather than to
    # the whole VCF, which lets many jobs run in parallel without exhausting RAM.
    tic_fill = time.perf_counter()
    buffers = [[] for _ in range(end_data)]
    n_lines = 0
    with open(inputfile) as f:
        # Drop headers before slicing, so [counter, ending) indexes variants.
        data_lines = (line for line in f if not line.startswith('#'))
        for line in itertools.islice(data_lines, counter, ending):
            fields = line.rstrip('\n').split('\t')
            try:
                af_value = fields[7].split(';')[8].split('=')[1]
                # Keep only the first value if more than one (matches the awk logic)
                af_f = float(af_value.split(',')[0]) if ',' in af_value else float(af_value)
            except (ValueError, IndexError):
                continue
            row = "{0}        {1}    {2}    {3}    {4}\n".format(
                fields[1], fields[2], fields[3], fields[4], af_value)
            want = '0' if af_f >= 0.5 else '1'
            for i in range(end_data):
                # We keep the mutation for an individual depending on the allele and AF
                if fields[start_data + i].split('|')[0] == want:
                    buffers[i].append(row)
            n_lines += 1

    print("== Streamed {} lines, filled {} individuals in {:0.2f} sec".format(
        n_lines, end_data, time.perf_counter() - tic_fill), flush=True)

    # The buffers already hold every byte of the output, so the archive is built
    # straight from them. Staging one file per individual and tarring the
    # directory cost three filesystem operations each, which dominates the stage
    # on a shared network volume. See archive.py for the layout this preserves.
    contents = {
        "chr{}.{}".format(c, columndata[i + start_data]): ''.join(buffers[i])
        for i in range(end_data)
    }

    outputfile = "chr{}n-{}-{}.tar.gz".format(c, counter, stop)
    print("== Done. Zipping {} files into {}.".format(end_data, outputfile), flush=True)

    archive.write_archive(outputfile, contents)

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
