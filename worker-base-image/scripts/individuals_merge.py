#!/usr/bin/env python3

import sys
import time

import archive


def merging(c, tar_files):
    print('= Merging chromosome {}...'.format(c))
    tic = time.perf_counter()

    # The inputs are streamed in lockstep and the result written as it goes, so
    # only one individual's merged content is ever held. Accumulating the whole
    # merge first does not fit: chr6's fifteen inputs are 109MB compressed and
    # several times that expanded, against the 1GiB a container gets here.
    #
    # This also needs no staging directory, so the chromosome-specific one that
    # used to keep parallel merges apart is gone, and with it anything to
    # collide over. See archive.py.
    outputfile = "chr{}n.tar.gz".format(c)
    print("== Merging {} archives into {}.".format(len(tar_files), outputfile))

    archive.merge_archives(outputfile, tar_files)

    print("= Chromosome {} merged in {:0.2f} seconds.".format(
        c, time.perf_counter() - tic))


if __name__ == "__main__":
    merging(c=sys.argv[1], tar_files=sys.argv[2:])
