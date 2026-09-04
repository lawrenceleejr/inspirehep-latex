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
    # A collection knows nothing either, and must say so rather than draw a
    # link to a search with no records in it.
    echo "$text" | grep -qE '\[\?[[:space:]]*collection' \
        || fail 'expected a [? collection ...] marker with no data file'
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

# Links must point at INSPIRE records and nowhere else.  hyperref decides
# between a URL and a local file by looking for a protocol, and a `:' that
# reached it at the wrong catcode once made every link a filename -- which
# hyperref silently completed with its default .pdf extension, so each link
# went to a page that does not exist.  pdftohtml reads the annotations back.
if command -v pdftohtml >/dev/null 2>&1; then
    links=$(pdftohtml -i -stdout -noframes "$pdf" 2>/dev/null \
            | grep -oE 'href="[^"]*"' | sed 's/href="//; s/"$//' | sort -u)
    echo "$links" | grep -q 'inspirehep\.net' \
        || fail 'no INSPIRE links in the output'
    stray=$(echo "$links" | grep -v '^https://inspirehep\.net/' || true)
    [ -z "$stray" ] || { echo "FAIL: a link that is not an INSPIRE record:" >&2
                         echo "$stray" >&2; exit 1; }
    bad=$(echo "$links" | grep -E 'inspirehep\.net/literature/[^/]*[^0-9]$' || true)
    [ -z "$bad" ] || { echo "FAIL: malformed INSPIRE record link:" >&2
                       echo "$bad" >&2; exit 1; }
    # The collection link: one search that is the OR of every paper listed.
    # It is the only link here that is a query rather than a record, and the
    # only one carrying an ampersand -- which is why it is worth asserting
    # separately from the record links above.
    collection=$(echo "$links" | grep -E 'inspirehep\.net/literature\?q=' || true)
    [ -n "$collection" ] || fail 'no collection search link'
    echo "$collection" | grep -q '+or+' \
        || fail "the collection link is not an OR of several records: $collection"
    echo "$collection" | grep -q 'ui-citation-summary=true' \
        || fail "the collection link does not open the citation summary: $collection"
fi

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
