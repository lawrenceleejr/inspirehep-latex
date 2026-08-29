#!/bin/sh
#
# What the built example must show.  Shared by `make test` and by CI, because
# when they were written separately they drifted: CI asserted "h-index of N"
# long after the example had settled on "h-index N", so it failed on every run
# while `make test` -- which checked only the citation counts -- stayed green.
#
# Usage: test/check_example.sh [example.pdf] [--no-data]
#
# With --no-data the opposite is asserted: no figures at all, and a visible
# marker for each, which is what a compile with no data file must still produce.

set -eu

pdf=${1:-example/example.pdf}
mode=${2:-normal}

[ -f "$pdf" ] || { echo "no such file: $pdf" >&2; exit 1; }
text=$(pdftotext "$pdf" -)

fail() {
    echo "FAIL: $1" >&2
    echo '--- the document says ---' >&2
    echo "$text" >&2
    exit 1
}

if [ "$mode" = --no-data ]; then
    # Every figure is unknown, and the package must say so rather than stop or
    # print something silently wrong.
    echo "$text" | grep -q '\[?' \
        || fail 'expected a [? ...] marker for every figure with no data file'
    echo "$text" | grep -qE '\[[0-9]+ citations?\]' \
        && fail 'a citation count appeared with no data file'
    echo 'OK: still a PDF, and it says what it does not know'
    exit 0
fi

# A record's citation count, as \inspirepub appends it.
echo "$text" | grep -qE '\[[0-9]+ citations?\]' \
    || fail 'no citation annotation'

# A title that came from INSPIRE rather than from the document.
echo "$text" | grep -q 'Towards a muon collider' \
    || fail 'no INSPIRE-supplied title'

# The author-level figures.  Matched loosely on purpose: the assertion is that
# a number reached the page, not that the example still uses this wording.
echo "$text" | grep -qE 'h-index[^0-9]*[0-9]' \
    || fail 'no h-index'
echo "$text" | grep -qE '[0-9][0-9,.]* papers' \
    || fail 'no paper count'
echo "$text" | grep -qE '[0-9][0-9,.]* citations' \
    || fail 'no citation total'

# A full reference, as INSPIRE renders it.
echo "$text" | grep -q 'Prog. Part. Nucl. Phys.' \
    || fail 'no formatted reference'

# Nothing should be missing: every id in the example is real.
if echo "$text" | grep -q '\[?'; then
    echo 'FAIL: a figure the example expects is missing' >&2
    echo "$text" | grep -o '\[?[^]]*\]' | sort -u >&2
    exit 1
fi

echo 'OK'
