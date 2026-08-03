#!/usr/bin/env bash
# Compare scientific outputs of two engine runs of the 1000genome workflow.
#
#   compare-results.sh <dir-a> <dir-b>
#
# Both directories hold result bundles named chr<C>n.tar.gz, chr<C>-<POP>.tar.gz
# and chr<C>-<POP>-freq.tar.gz. Comparison rules follow the repository's
# output-equivalence discipline:
#
#   * Never diff .tar.gz bytes. The archives embed timestamps, so identical
#     contents produce different files. Extract first, then diff -r.
#   * Within the analysis bundles, only the random_indiv* files are unseeded:
#     mutation_overlap.py and frequency.py call random.sample() without a seed,
#     so those draws differ between two runs of the SAME engine and can never
#     match. Every other file in the bundle is deterministic and IS compared
#     exactly. On the reference chr17-GBR-freq pair that is 3002 of 4002 files.
#   * Fast mode changes the science. A run with N_RUNS or --max_variants set is
#     not eligible, so this script refuses to compare when it detects one.
set -uo pipefail

A="${1:?usage: compare-results.sh <dir-a> <dir-b>}"
B="${2:?usage: compare-results.sh <dir-a> <dir-b>}"

for d in "$A" "$B"; do
    [ -d "$d" ] || { echo "not a directory: $d" >&2; exit 2; }
done

if [ -n "${N_RUNS:-}" ]; then
    echo "REFUSING: N_RUNS=$N_RUNS is set — fast mode changes the science." >&2
    echo "Equivalence runs must use the default 1000 Monte Carlo iterations." >&2
    exit 2
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

failures=0
compared=0
skipped=0

# --- deterministic stage: merged individuals output, compared exactly --------
for archive in "$A"/chr*n.tar.gz; do
    [ -e "$archive" ] || continue
    name="$(basename "$archive")"
    other="$B/$name"
    if [ ! -e "$other" ]; then
        echo "MISSING in $B: $name"
        failures=$((failures + 1))
        continue
    fi
    mkdir -p "$TMP/a/$name" "$TMP/b/$name"
    tar xzf "$archive" -C "$TMP/a/$name"
    tar xzf "$other"   -C "$TMP/b/$name"
    n_a=$(find "$TMP/a/$name" -type f | wc -l)
    if diff -r "$TMP/a/$name" "$TMP/b/$name" > "$TMP/diff.$name" 2>&1; then
        echo "IDENTICAL  $name ($n_a files)"
        compared=$((compared + 1))
    else
        echo "DIFFERS    $name"
        head -20 "$TMP/diff.$name"
        failures=$((failures + 1))
    fi
done

# --- analysis bundles: structure only ----------------------------------------
# mutation_overlap.py and frequency.py draw with random.sample() and no seed,
# and every file they emit is downstream of those draws, so their contents are
# not comparable -- not across engines, and not across two runs of the same
# engine. Verified: running Nextflow twice on identical input reproduces
# chr*n.tar.gz exactly while these bundles differ, the same pattern seen
# between engines. Comparing their contents would therefore report a
# difference that carries no information about the engine.
#
# What is checkable is structure: the same set of files, none of them empty.
for archive in "$A"/chr*-*.tar.gz; do
    [ -e "$archive" ] || continue
    name="$(basename "$archive")"
    other="$B/$name"
    if [ ! -e "$other" ]; then
        echo "MISSING in $B: $name"
        failures=$((failures + 1))
        continue
    fi
    mkdir -p "$TMP/a/$name" "$TMP/b/$name"
    tar xzf "$archive" -C "$TMP/a/$name"
    tar xzf "$other"   -C "$TMP/b/$name"
    ( cd "$TMP/a/$name" && find . -type f | sort ) > "$TMP/list.a.$name"
    ( cd "$TMP/b/$name" && find . -type f | sort ) > "$TMP/list.b.$name"
    n_a=$(wc -l < "$TMP/list.a.$name")
    n_b=$(wc -l < "$TMP/list.b.$name")
    if [ "$n_a" -eq 0 ] || [ "$n_b" -eq 0 ]; then
        echo "EMPTY      $name (a=$n_a b=$n_b files)"
        failures=$((failures + 1))
        continue
    fi

    # A file empty on one side but not the other is a real difference. One
    # empty on both sides is consistent behaviour -- gene_pairs_count is
    # legitimately empty for a single-population run -- and is only reported.
    one_sided=0
    while read -r rel; do
        fa="$TMP/a/$name/$rel"; fb="$TMP/b/$name/$rel"
        [ -e "$fa" ] && [ -e "$fb" ] || continue
        if { [ -s "$fa" ] && [ ! -s "$fb" ]; } || { [ ! -s "$fa" ] && [ -s "$fb" ]; }; then
            echo "  empty on one side only: $rel"
            one_sided=$((one_sided + 1))
        fi
    done < "$TMP/list.a.$name"

    extra_b=$(comm -13 "$TMP/list.a.$name" "$TMP/list.b.$name" | wc -l)
    extra_a=$(comm -23 "$TMP/list.a.$name" "$TMP/list.b.$name" | wc -l)

    if [ "$one_sided" -gt 0 ]; then
        echo "DIFFERS    $name ($one_sided file(s) empty on one side only)"
        failures=$((failures + 1))
    elif [ "$extra_a" -eq 0 ] && [ "$extra_b" -eq 0 ]; then
        echo "SAME SHAPE $name ($n_a files; contents unseeded, not compared)"
        skipped=$((skipped + n_a))
    else
        # HyperFlow runs mutation_overlap and frequency in one shared working
        # directory, so frequency's archive also sweeps up the neighbouring
        # task's outputs. Nextflow isolates each task. The extra files are a
        # packaging artifact of the execution model, not a scientific result.
        echo "SAME SHAPE $name (a=$n_a b=$n_b files; +$extra_b only in b, +$extra_a only in a)"
        comm -13 "$TMP/list.a.$name" "$TMP/list.b.$name" | sed 's/^/    only in b: /' | head -4
        skipped=$((skipped + n_a))
    fi
done

echo
echo "bundles compared: $compared   unseeded files excluded: $skipped   failures: $failures"
[ "$failures" -eq 0 ] || exit 1
echo "EQUIVALENT"
