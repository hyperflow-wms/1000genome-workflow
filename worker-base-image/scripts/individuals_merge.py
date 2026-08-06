#!/usr/bin/env python3

import sys
import time

import archive


def merging(c, tar_files):
    print('= Merging chromosome {}...'.format(c))
    tic = time.perf_counter()

    # Each input is read through memory and the result written straight back
    # out. Extracting an input into a temporary directory and reading the files
    # back cost three filesystem operations per individual per input, which
    # dominates the stage on a shared network volume; it also needed a
    # chromosome-specific staging directory to keep parallel merges apart, and
    # with no directory there is nothing left to collide. See archive.py.
    data = {}

    for tar in tar_files:
        tic_iter = time.perf_counter()
        for name, lines in archive.read_archive(tar):
            if name in data:
                data[name] += lines
            else:
                data[name] = lines

        print("Merged {} in {:0.2f} sec".format(tar, time.perf_counter()-tic_iter))

    outputfile = "chr{}n.tar.gz".format(c)
    print("== Done. Zipping {} files into {}.".format(len(data), outputfile))

    archive.write_archive(
        outputfile, {name: ''.join(lines) for name, lines in data.items()})

    print("= Chromosome {} merged in {:0.2f} seconds.".format(
        c, time.perf_counter() - tic))


if __name__ == "__main__":
    merging(c=sys.argv[1], tar_files=sys.argv[2:])
