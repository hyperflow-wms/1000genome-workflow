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

# --- analysis bundles: everything except the unseeded random draws -----------
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
    n_tot=$(find "$TMP/a/$name" -type f | wc -l)
    n_rnd=$(find "$TMP/a/$name" -type f -name 'random_indiv*' | wc -l)
    if [ "$n_tot" -eq 0 ]; then
        echo "EMPTY      $name"
        failures=$((failures + 1))
        continue
    fi
    if diff -r -x 'random_indiv*' "$TMP/a/$name" "$TMP/b/$name" > "$TMP/diff.$name" 2>&1; then
        echo "IDENTICAL  $name ($((n_tot - n_rnd))/$n_tot files; $n_rnd unseeded draws excluded)"
        compared=$((compared + 1))
        skipped=$((skipped + n_rnd))
    else
        echo "DIFFERS    $name (outside the unseeded draws)"
        head -20 "$TMP/diff.$name"
        failures=$((failures + 1))
    fi
done

echo
echo "bundles compared: $compared   unseeded files excluded: $skipped   failures: $failures"
[ "$failures" -eq 0 ] || exit 1
echo "EQUIVALENT"
