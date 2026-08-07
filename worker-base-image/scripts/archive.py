"""Reading and writing the per-individual archives without staging files.

`individuals.py` and `individuals_merge.py` both hold their entire output in
memory before writing it. Materialising it as one file per individual, only to
tar the directory and delete it, costs about three filesystem operations per
individual -- roughly 7,400 for a 2,479-individual chromosome, and again for
every input a merge consumes. On a shared network filesystem each of those is a
round trip, which is what makes the two stages dominate a cluster run.

The archive layout is reproduced exactly, because `frequency.py` and
`mutation_overlap.py` extract these archives and open the extracted files by
name:

* a leading directory member whose name is empty. `tarfile.add()` was called as
  `add(input_dir, arcname=os.path.basename(input_dir))` with `input_dir`
  carrying a trailing slash, and `os.path.basename('chr7n-0/')` is `''`.
* one regular member per individual, named `chr{c}.{individual}`, in sorted
  order -- `tarfile.add()` recursed through `sorted(os.listdir())`.

Members are emitted with the modes the previous implementation produced (0755
for the directory, 0644 for the files) so that an extracted tree is
indistinguishable from one the old code wrote.
"""

import io
import os
import tarfile
import time

DIR_MODE = 0o755
FILE_MODE = 0o644


def _member(name, mode, mtime, uid, gid):
    info = tarfile.TarInfo(name)
    info.mode = mode
    info.mtime = mtime
    info.uid = uid
    info.gid = gid
    return info


def write_archive(archive, contents):
    """Write `contents` as a .tar.gz.

    Values may be a member's text or a sequence of strings to concatenate. The
    joining happens one member at a time: asking the caller to build a second,
    fully-joined mapping doubles peak memory, which is what pushed the merge of
    a large region past a 1GiB container limit.
    """
    mtime = int(time.time())
    uid, gid = os.getuid(), os.getgid()

    with tarfile.open(archive, "w:gz") as handle:
        root = _member("", DIR_MODE, mtime, uid, gid)
        root.type = tarfile.DIRTYPE
        handle.addfile(root)

        for name in sorted(contents):
            payload = contents[name]
            if not isinstance(payload, str):
                payload = ''.join(payload)
            payload = payload.encode()
            member = _member(name, FILE_MODE, mtime, uid, gid)
            member.size = len(payload)
            handle.addfile(member, io.BytesIO(payload))


def merge_archives(archive, inputs):
    """Concatenate the same-named members of `inputs` into one archive.

    Holding the merged result in memory does not scale: the fifteen individuals
    archives of a chr6 region are 109MB compressed and several times that
    expanded, well past the 1GiB a container is allowed here.

    Every input carries the same members in the same sorted order, because each
    was written from the same column list, so the inputs are walked in lockstep
    and only one individual's merged content exists at a time. Walking them once
    each is what makes this affordable -- a .tar.gz is not seekable, so looking
    members up by name would decompress the whole archive on every lookup.
    """
    mtime = int(time.time())
    uid, gid = os.getuid(), os.getgid()
    handles = [tarfile.open(path, "r:*") for path in inputs]

    def members(handle):
        # A named function, not a generator expression: an expression closes over
        # the loop variable and every stream would end up reading the last
        # archive opened.
        for member in handle:
            if member.isfile():
                yield member.name, handle.extractfile(member)

    try:
        # Each input yields its members in the same order; zip walks them together.
        streams = [members(h) for h in handles]
        with tarfile.open(archive, "w:gz") as out:
            root = _member("", DIR_MODE, mtime, uid, gid)
            root.type = tarfile.DIRTYPE
            out.addfile(root)

            for group in zip(*streams):
                names = {name for name, _ in group}
                if len(names) != 1:
                    raise ValueError(
                        "archives disagree on member order: %s" % sorted(names))
                name = names.pop()
                payload = b''.join(fh.read() for _, fh in group if fh is not None)
                member = _member(name, FILE_MODE, mtime, uid, gid)
                member.size = len(payload)
                out.addfile(member, io.BytesIO(payload))
    finally:
        for h in handles:
            h.close()


def read_archive(archive):
    """Yield `(name, lines)` for every regular member, never touching the disk.

    `lines` keeps its line endings, matching the `readlines()` the callers used
    when they read the extracted files back.
    """
    with tarfile.open(archive, "r:*") as handle:
        for member in handle.getmembers():
            if not member.isfile():
                continue
            payload = handle.extractfile(member)
            if payload is None:
                continue
            yield member.name, payload.read().decode().splitlines(keepends=True)
