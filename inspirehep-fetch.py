#!/usr/bin/env python3
"""Write the data file that inspirehep.sty reads, without needing shell escape.

inspirehep.sty can refresh its own figures through TeX's shell escape, but that
is off by default and unavailable on Overleaf.  This script does the same job
from outside the compile, producing a byte-compatible file, for anyone who
cannot or would rather not enable it:

    \\inspiresetstat{citations}{207838}
    \\inspiresetcites{2642414}{407}
    \\inspiresetfetched{2026-08-29}{739767}

Which records to look up, and whose author profile to summarise, are discovered
by scanning the LaTeX sources for \\inspirepub{<record>} and for the author= key
of \\usepackage[...]{inspirehep}; both can be overridden on the command line.

Only the Python standard library is used, so there is nothing to install.

    inspirehep-fetch.py                 # refresh inspirehep-data.tex
    inspirehep-fetch.py --print         # write to stdout instead
    inspirehep-fetch.py --strict        # fail loudly if the API is unreachable

On any network failure the existing file is left alone and the exit status is 0,
so a flaky connection costs slightly stale figures rather than a broken build.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://inspirehep.net/api"
USER_AGENT = "inspirehep-latex/1.0 (+https://github.com/lawrenceleejr/inspirehep-latex)"

# Each command needs a different slice of the API, so they are discovered
# separately: metadata for anything that typesets a record, the earliest_date
# facet only for what is plotted, BibTeX only for what is cited.  Every command
# takes an optional [key=value] argument, which the patterns have to skip.
_OPT = r"(?:\[[^\]]*\])?"
# A record is named either by its recid (2642414) or by its INSPIRE texkey
# (Lee:2018pag).  The two are told apart by shape, here and in the package.
_ID = r"(\d+|[A-Za-z][\w.'-]*:\d{4}[A-Za-z0-9]+)"
TEXKEY_RE = re.compile(r"^[A-Za-z][\w.'-]*:\d{4}[A-Za-z0-9]+$")
# \input{f}, \include{f}, and \subfile{f}, so a multi-file document can be
# scanned from its root without listing every chapter.
INPUT_RE = re.compile(r"\\(?:input|include|subfile)\s*\{\s*([^}]+?)\s*\}")

RECORD_RE = re.compile(r"\\inspire(?:pub|cites|title|ref|year)\s*"
                       + _OPT + r"\s*\{\s*" + _ID + r"\s*\}")
PLOT_RE = re.compile(r"\\inspireplot\s*" + _OPT + r"\s*\{\s*" + _ID + r"\s*\}")
# \inspirecite / \inspirekey, but not \inspirecites: the \s*{ guard sees to that.
BIB_RE = re.compile(r"\\inspire(?:cite|key)\s*\{\s*" + _ID + r"\s*\}")
# \inspirecollectionplot is the only collection command that costs an extra
# query, so it is looked for by name and the facet fetched only if it is used.
COLLECTION_PLOT_RE = re.compile(r"\\inspirecollectionplot\b")
AUTHOR_USE_RE = re.compile(
    r"\\inspire(?:papers|citations|hindex|authorstat|authorplot)\s*"
    + _OPT + r"\s*\{\s*([A-Za-z0-9.\-]+)\s*\}")


def day_number(d: datetime.date) -> int:
    """A day count TeX can also compute, as 365*year + 31*month + day.

    inspirehep.sty works out today's value the same way from \\year, \\month, and
    \\day so the two can be compared without a date library on the TeX side.
    Uneven month lengths make it drift a few units over a quarter, which is
    fine for the package's soft "these figures are getting old" check.
    """
    return 365 * d.year + 31 * d.month + d.day


def api_get(endpoint: str, params: dict, retries: int = 3, timeout: int = 60) -> dict:
    url = f"{API}/{endpoint}?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and 400 <= exc.code < 500:
                raise
            if attempt == retries:
                raise
            backoff = 2 ** attempt
            print(f"  ! {exc} -- retrying in {backoff}s", file=sys.stderr)
            time.sleep(backoff)
    raise AssertionError("unreachable")


RECORD_FIELDS = ("control_number,citation_count,titles,authors,collaborations,"
                 "publication_info,arxiv_eprints,texkeys")


def tex_escape(text: str) -> str:
    """Make INSPIRE text safe to typeset without disturbing its maths.

    INSPIRE titles already contain LaTeX maths ($\\sqrt{s}$, $\\to$), so escaping
    must leave anything between dollar signs alone and only protect what would
    otherwise misfire in running text.
    """
    out, in_math = [], False
    for ch in text:
        if ch == "$":
            in_math = not in_math
            out.append(ch)
        elif not in_math and ch in "&%#_":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def fetch_formatted(recid: str, style: str) -> dict[str, str]:
    """INSPIRE's own rendered reference, split into the parts a document may
    want to recombine.

    Using INSPIRE's formatting rather than assembling one from the metadata
    means the result matches what everyone else in the field cites, and there
    is no author-list or journal-abbreviation logic here to get wrong.  But a
    single pre-formatted string would make `errata' and `collab' choices that
    could only be made when fetching, so the block is taken apart instead and
    the package puts it back together at typeset time.

    The response is always shaped the same way::

        %\\cite{key}
        \\bibitem{key}
        <author line(s)>
        %``<title>,''
        <publication line(s), possibly including [erratum: ...]>
        %<n> citations counted in INSPIRE as of <date>
    """
    url = f"{API}/literature/{recid}?format={style}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read().decode("utf-8", "replace")

    authors, pub, errata = [], [], []
    seen_title = False
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith(("\\bibitem", "%\\cite")):
            continue
        if re.match(r"^%\s*\d+\s+citations?\s+counted", line):
            continue
        if line.startswith("%"):          # the commented-out title separates the two halves
            seen_title = True
            continue
        if not seen_title:
            authors.append(line)
        elif line.startswith("[erratum:"):
            errata.append(line)
        else:
            pub.append(line)

    return {
        # The trailing comma is INSPIRE's, joining authors to title; the
        # package supplies its own punctuation, so drop it.
        "authors": " ".join(authors).strip().rstrip(","),
        "pub": " ".join(pub).strip(),
        "errata": " ".join(errata).strip(),
    }


def query_for(identifier: str) -> str:
    """An INSPIRE query clause for a recid or a texkey, whichever this is."""
    return (f"texkey {identifier}" if TEXKEY_RE.match(identifier)
            else f"recid {identifier}")


def fetch_records(recids: list[str], style: str = "latex-eu") -> dict[str, dict]:
    """Everything the package can say about each record.

    Counts, titles, and keys come from one batched metadata query; the reference
    line comes from INSPIRE's own renderer, one call per record.
    """
    if not recids:
        return {}
    result = api_get("literature", {
        "q": " or ".join(query_for(r) for r in recids),
        "fields": RECORD_FIELDS,
        "size": str(max(len(recids), 1)),
    })
    # Results come back keyed by control_number, but the document named each
    # record its own way -- and a record can carry several texkeys -- so map
    # each hit back to whichever identifier was asked for.
    records = {}
    for hit in result["hits"]["hits"]:
        meta = hit["metadata"]
        control = str(meta["control_number"])
        keys = set(meta.get("texkeys") or [])
        for wanted in recids:
            if wanted != control and wanted not in keys:
                continue
            records[wanted] = {
                "recid": control,
                "cites": int(meta.get("citation_count") or 0),
                "title": tex_escape(meta["titles"][0]["title"]) if meta.get("titles") else "",
                "key": (meta.get("texkeys") or [""])[0],
                "ref": {},
                "collab": (meta.get("collaborations") or [{}])[0].get("value", ""),
            }
    for recid in records:
        try:
            records[recid]["ref"] = fetch_formatted(records[recid]["recid"], style)
        except Exception as exc:  # noqa: BLE001 - a missing reference is not fatal
            print(f"  ! no {style} reference for {recid}: {exc}", file=sys.stderr)
            records[recid]["ref"] = {}
    missing = sorted(set(recids) - set(records))
    if missing:
        print(f"  ! no INSPIRE record for: {', '.join(missing)}", file=sys.stderr)
    return records


def fetch_years(query: str) -> list[tuple[str, int]]:
    """(year, count) from the earliest_date facet of any literature query.

    With q="a <BAI>" that is papers per year; with q="refersto ..." it is the
    citing papers per year, which is what a citations-per-year plot shows.
    """
    facets = api_get("literature/facets", {"q": query})
    buckets = facets.get("aggregations", {}).get("earliest_date", {}).get("buckets", [])
    return [(b.get("key_as_string") or str(b.get("key")), int(b["doc_count"])) for b in buckets]


def fetch_bibtex(recid: str) -> str:
    """The record's BibTeX entry, exactly as INSPIRE renders it."""
    url = f"{API}/literature/{recid}?format=bibtex"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", "replace").strip()


def fetch_summary(author: str) -> dict[str, int]:
    """Papers, citations, and h-index, from the same aggregation INSPIRE's own
    profile pages use, so the numbers match what a reader sees there."""
    if author.isdigit():
        profile = api_get(f"authors/{author}", {"fields": "ids"})
        bais = [i["value"] for i in profile["metadata"].get("ids", [])
                if i.get("schema") == "INSPIRE BAI"]
        if not bais:
            raise RuntimeError(f"author {author} has no INSPIRE BAI")
        author = bais[0]
    facets = api_get("literature/facets", {"q": f"a {author}", "facet_name": "citation-summary"})
    summary = facets["aggregations"]["citation_summary"]
    return {
        "papers": int(facets["hits"]["total"]["value"]),
        "citations": int(summary["citations"]["buckets"]["all"]["citations_count"]["value"]),
        "hindex": int(summary["h-index"]["value"]["all"]),
    }


def resolve_bai(author: str) -> str:
    """INSPIRE's author search takes a BAI, not the recid in a profile URL."""
    if not author.isdigit():
        return author
    profile = api_get(f"authors/{author}", {"fields": "ids"})
    bais = [i["value"] for i in profile["metadata"].get("ids", [])
            if i.get("schema") == "INSPIRE BAI"]
    if not bais:
        raise RuntimeError(f"author {author} has no INSPIRE BAI")
    return bais[0]


def author_summary(bai: str) -> dict[str, int]:
    facets = api_get("literature/facets", {"q": f"a {bai}", "facet_name": "citation-summary"})
    summary = facets["aggregations"]["citation_summary"]
    return {
        "papers": int(facets["hits"]["total"]["value"]),
        "citations": int(summary["citations"]["buckets"]["all"]["citations_count"]["value"]),
        "hindex": int(summary["h-index"]["value"]["all"]),
    }


def series(pairs: list[tuple[str, int]]) -> str:
    """A year series as coordinate pairs: "(2019,1) (2020,7) ...".

    Already in the form a plot wants, so the package hands it straight over and
    never has to parse it -- which matters more than it sounds, because a `:'
    is a letter under \\ExplSyntaxOn and would not match the catcode-12 one in
    a data file.
    """
    return " ".join(f"({year},{count})" for year, count in pairs)


def collection_query(ids: list[str]) -> str:
    """The INSPIRE search query for a whole set of records, as a query string:
    "recid+1+or+recid+2+or+texkey+Lee:2018pag".

    Plus-encoded rather than percent-encoded on purpose.  This ends up in a
    data file that TeX reads, and a `%' there would comment out the rest of the
    line; `+' for the spaces avoids every character TeX would reinterpret.  A
    `:' from a texkey is legal in a query string and is dealt with on the
    package side, where it is a letter rather than an ordinary character.
    """
    return "+or+".join(query_for(i).replace(" ", "+") for i in ids)


def collection_summary(ids: list[str]) -> dict[str, int]:
    """Papers, citations, and h-index across a set of records.

    The citation-summary facet is not an author-only feature: INSPIRE computes
    it over whatever query it is handed, so the h-index of an arbitrary subset
    of somebody's papers comes back from a single call -- worked out the way
    the website works it out, rather than by us from citation counts.
    """
    facets = api_get("literature/facets", {
        "q": " or ".join(query_for(i) for i in ids),
        "facet_name": "citation-summary",
    })
    summary = facets["aggregations"]["citation_summary"]
    matched = int(facets["hits"]["total"]["value"])
    if matched != len(ids):
        print(f"  ! the collection names {len(ids)} record(s) but INSPIRE matched "
              f"{matched}; the figures cover those {matched}", file=sys.stderr)
    return {
        "papers": matched,
        "citations": int(summary["citations"]["buckets"]["all"]["citations_count"]["value"]),
        "hindex": int(summary["h-index"]["value"]["all"]),
    }


def g_index(counts: list[int]) -> int:
    """Egghe's g-index: the largest g such that the top g papers have at least
    g^2 citations between them.

    INSPIRE does not publish one -- its citation summary carries only an
    h-index -- so this is worked out here, from the per-record counts the
    fetcher already has.  Those are the same numbers INSPIRE's own h-index
    comes from: recomputing h from them reproduces what the facet returns.

    Note that g cannot exceed the number of papers, so on a short list it
    saturates and simply reports the list's length.  That is the definition
    behaving as intended on a truncated set, not a bug, but it does mean a
    g-index says little until a list is long enough for the cumulative count
    to stop keeping up with g^2.
    """
    running = 0
    best = 0
    for rank, count in enumerate(sorted(counts, reverse=True), 1):
        running += count
        if running >= rank * rank:
            best = rank
    return best


def collection_years(ids: list[str], citations: int | None = None) -> list[tuple[str, int]]:
    """Citing papers per year, across a whole set of records.

    `refersto' must be repeated in every clause.  Writing it once in front of a
    parenthesised list -- refersto (recid A or recid B) -- is accepted by
    INSPIRE and quietly means something else: for a two-paper set it returned
    353,154 citing papers starting in 1900, a plot of entirely the right shape
    and four orders of magnitude wrong.  Nothing about that fails, so the
    check below earns its keep -- a citing paper contributes at least one
    citation, so citing papers can never greatly outnumber citations.  The
    margin is wide because INSPIRE's stored counts and its reference index do
    disagree on individual records; the failure it is guarding against is off
    by more than a hundredfold.
    """
    pairs = fetch_years(" or ".join(f"refersto {query_for(i)}" for i in ids))
    total = sum(count for _, count in pairs)
    if citations is not None and total > 2 * citations + 500:
        raise RuntimeError(
            f"{total} citing papers against {citations} citations -- the "
            f"refersto query has matched far more than this collection, so no "
            f"plot is written")
    return pairs


def walk_inputs(roots: list[Path], base: Path | None = None,
                seen: set[Path] | None = None) -> list[Path]:
    """Every file reachable from `roots` through \\input, \\include, or \\subfile.

    LaTeX resolves \\input relative to the directory the compile was started
    in -- the main document's -- not relative to the file doing the including,
    so that is tried first.  Resolving relative to the including file is tried
    second, because the `subfiles' and `import' packages do work that way and
    it costs nothing to support both.

    A name that resolves to nothing is skipped: it may be generated, or inside
    a conditional, and neither is this script's business.  `seen' keeps a cycle
    from looping forever.
    """
    if seen is None:
        seen = set()
    found = []
    for root in roots:
        root = root.resolve()
        if root in seen or not root.is_file():
            continue
        seen.add(root)
        found.append(root)
        here = base if base is not None else root.parent

        children = []
        for name in INPUT_RE.findall(root.read_text(encoding="utf-8", errors="replace")):
            for candidate in (here / name, root.parent / name):
                for path in (candidate, candidate.with_suffix(".tex")):
                    if path.is_file():
                        children.append(path)
                        break
                else:
                    continue
                break
        found.extend(walk_inputs(children, here, seen))
    return found


def discover(sources: list[Path]) -> dict[str, list[str]]:
    """What the sources ask for, by the commands they use.

    There is no author= package option -- a document may discuss several people
    -- so the authors are exactly the ones its commands name, and nothing is
    inferred from, say, an INSPIRE author link in the prose.
    """
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in sources)
    return {
        "records": sorted(set(RECORD_RE.findall(text)) | set(BIB_RE.findall(text))),
        "plots":   sorted(set(PLOT_RE.findall(text))),
        "bibtex":  sorted(set(BIB_RE.findall(text))),
        "authors": sorted(set(AUTHOR_USE_RE.findall(text))),
        "collectionplot": bool(COLLECTION_PLOT_RE.search(text)),
    }


def bib_targets(wanted: list[str], records: dict) -> dict[str, str]:
    """Which records need a BibTeX entry, one per record.

    One paper can be cited two ways -- \\inspirecite{1701002} and
    \\inspirecite{Lee:2018pag} are the same entry -- and INSPIRE returns
    identical BibTeX for both.  Writing it twice puts a repeated key in the
    .bib, which BibTeX refuses outright ("Repeated entry ... I'm skipping
    whatever remains of this entry"), losing the entry and every citation of
    it.  So this is keyed by record number, mapped to whichever identifier the
    document used first, which is the one messages should name.

    Repeating a single identifier was never the problem: those collapse when
    the sources are scanned.  This is the case where the two differ.
    """
    targets: dict[str, str] = {}
    for identifier in wanted:
        numeric = records.get(identifier, {}).get("recid", identifier)
        targets.setdefault(numeric, identifier)
    return targets


def render(records: dict, plots: dict, authors: dict, author_years: dict,
           primary: str | None, collection: dict | None = None) -> str:
    """The generated file the package reads: one declaration per fact."""
    today = datetime.date.today()
    out = ["%% GENERATED by inspirehep-fetch.py -- do not edit."]

    for recid in sorted(records, reverse=True):
        r = records[recid]
        if r.get("recid") and r["recid"] != recid:
            out.append(f"\\inspiresetrecid{{{recid}}}{{{r['recid']}}}")
        out.append(f"\\inspiresetcites{{{recid}}}{{{r['cites']}}}")
        if r["title"]:
            out.append(f"\\inspiresettitle{{{recid}}}{{{r['title']}}}")
        if r["key"]:
            out.append(f"\\inspiresetkey{{{recid}}}{{{r['key']}}}")
        if r.get("collab"):
            out.append(f"\\inspiresetrefcollab{{{recid}}}{{{r['collab']}}}")
        for part in ("authors", "pub", "errata"):
            if r["ref"].get(part):
                out.append(f"\\inspiresetref{part}{{{recid}}}{{{r['ref'][part]}}}")
    for recid in sorted(plots, reverse=True):
        out.append(f"\\inspiresetyears{{{recid}}}{{{series(plots[recid])}}}")

    for aid in sorted(authors):
        for key, value in authors[aid].items():
            out.append(f"\\inspiresetauthorstat{{{aid}}}{{{key}}}{{{value}}}")
            # The package-level author also answers to the plain \inspirestat.
            if aid == primary:
                out.append(f"\\inspiresetstat{{{key}}}{{{value}}}")
    for aid in sorted(author_years):
        for kind, pairs in author_years[aid].items():
            out.append(f"\\inspiresetauthoryears{{{aid}}}{{{kind}}}{{{series(pairs)}}}")

    if collection:
        # The query needs no network -- it is built from what the document
        # itself names -- so the link keeps working through an INSPIRE outage
        # that leaves every count below unwritten.
        if collection.get("query"):
            out.append(f"\\inspiresetcollectionquery{{{collection['query']}}}")
        for key, value in sorted(collection.get("stats", {}).items()):
            out.append(f"\\inspiresetcollection{{{key}}}{{{value}}}")
        if collection.get("years"):
            out.append(f"\\inspiresetcollectionyears{{{series(collection['years'])}}}")

    out.append(f"\\inspiresetfetched{{{today.isoformat()}}}{{{day_number(today)}}}")
    return "\n".join(out) + "\n"


def write_if_changed(path: Path, content: str) -> bool:
    """Write only when the content really differs, so a rebuild that changes
    nothing does not show up in git status."""
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sources", nargs="*", type=Path,
                        help="LaTeX files to scan (default: *.tex beside the output)")
    parser.add_argument("--output", type=Path, default=Path("inspirehep-data.tex"),
                        help="file to write (default: %(default)s)")
    parser.add_argument("--bib", type=Path, default=Path("inspirehep-refs.bib"),
                        help="BibTeX file for records used with \\inspirecite "
                             "(default: %(default)s)")
    parser.add_argument("--author", default=None,
                        help="INSPIRE author recid or BAI, also given the short "
                             "\\inspiresetstat form (default: only the people "
                             "the sources name)")
    parser.add_argument("--collection", choices=["pubs", "all", "none"],
                        default="pubs",
                        help="which papers the \\inspirecollection... commands "
                             "cover: those the document presents as its own "
                             "(default), every record it mentions including "
                             "works it merely cites, or nothing")
    parser.add_argument("--no-follow", dest="follow", action="store_false",
                        help="do not follow \\input/\\include from the files given")
    parser.add_argument("--style", default="latex-eu", choices=["latex-eu", "latex-us"],
                        help="INSPIRE reference format (default: %(default)s)")
    parser.add_argument("--strict", action="store_true",
                        help="exit non-zero if the API cannot be reached")
    parser.add_argument("--print", dest="to_stdout", action="store_true",
                        help="write to stdout instead of a file")
    args = parser.parse_args(argv)

    roots = args.sources or sorted(args.output.resolve().parent.glob("*.tex"))
    sources = walk_inputs([Path(p) for p in roots]) if args.follow else \
        [Path(p).resolve() for p in roots if Path(p).is_file()]
    generated = {args.output.resolve(), args.bib.resolve()}
    sources = [p for p in sources if p not in generated]
    if not sources:
        print("no LaTeX sources to scan", file=sys.stderr)
        return 1

    wanted = discover(sources)
    primary = args.author
    if primary:
        wanted["authors"] = sorted(set(wanted["authors"]) | {primary})
    print(f"Scanning {len(sources)} file(s): {len(wanted['records'])} record(s), "
          f"{len(wanted['plots'])} plot(s), {len(wanted['bibtex'])} citation(s), "
          f"{len(wanted['authors'])} author(s)")

    # One unknown id must not cost the rest of the document its refresh, so
    # each item degrades on its own: a warning, and the file simply carries no
    # entry for it -- which the package typesets as a visible [? ...] marker.
    problems = 0

    def attempt(what, fn, *args_):
        nonlocal problems
        try:
            return fn(*args_)
        except Exception as exc:  # noqa: BLE001 - warn and carry on
            problems += 1
            print(f"  ! {what}: {exc}", file=sys.stderr)
            return None

    records = attempt("records", fetch_records, wanted["records"], args.style)
    if records is None:
        # The batched record query is the one lookup nothing works without;
        # treat its failure as the network being down.
        print("INSPIRE-HEP lookup failed.", file=sys.stderr)
        if args.strict:
            return 1
        print("Keeping the existing figures; the document will still build.", file=sys.stderr)
        return 0

    plots, authors, author_years, bibs = {}, {}, {}, {}
    for recid in wanted["plots"]:
        numeric = records.get(recid, {}).get("recid", recid)
        years = attempt(f"plot {recid}", fetch_years, f"refersto recid {numeric}")
        if years is not None:
            plots[recid] = years
    for aid in wanted["authors"]:
        if (bai := attempt(f"author {aid}", resolve_bai, aid)) is None:
            continue
        if (summary := attempt(f"author {aid}", author_summary, bai)) is not None:
            authors[aid] = summary
        author_years[aid] = {}
        for kind, query in (("papers", f"a {bai}"), ("citations", f"refersto a {bai}")):
            if (years := attempt(f"author {aid} {kind}", fetch_years, query)) is not None:
                author_years[aid][kind] = years
    # Papers the document presents as its own are the ones it typesets --
    # \inspirepub and friends.  A \inspirecite is somebody else's work and has
    # no business in your h-index, so `pubs' leaves those out; `all' is there
    # for a document where every record mentioned really is in the collection.
    if args.collection == "none":
        collection_ids = []
    elif args.collection == "all":
        collection_ids = wanted["records"]
    else:
        collection_ids = sorted(set(wanted["records"]) - set(wanted["bibtex"]))

    collection: dict = {}
    if collection_ids:
        collection["query"] = collection_query(collection_ids)
        stats = attempt("collection", collection_summary, collection_ids)
        if stats is not None:
            # h comes from INSPIRE; g is not on offer there, so it is computed
            # from the counts already fetched for these same records.  A record
            # INSPIRE did not match is absent from `records' and so drops out of
            # both, which is what the `papers' figure already reports.
            stats["gindex"] = g_index([records[i]["cites"] for i in collection_ids
                                       if i in records])
            collection["stats"] = stats
        if wanted["collectionplot"]:
            years = attempt("collection plot", collection_years, collection_ids,
                            (stats or {}).get("citations"))
            if years is not None:
                collection["years"] = years

    for numeric, named in bib_targets(wanted["bibtex"], records).items():
        if (bib := attempt(f"bibtex {named}", fetch_bibtex, numeric)) is not None:
            bibs[numeric] = bib

    if problems and args.strict:
        print(f"{problems} lookup(s) failed and --strict is set.", file=sys.stderr)
        return 1

    content = render(records, plots, authors, author_years, primary, collection)
    if args.to_stdout:
        sys.stdout.write(content)
        return 0

    changed = write_if_changed(args.output, content)
    print(f"  {len(records)} record(s), {len(plots)} plot series, {len(authors)} author(s)")
    for aid, stat in authors.items():
        print(f"    {aid}: {stat['papers']:,} papers, {stat['citations']:,} citations, "
              f"h-index {stat['hindex']}")
    if stats := collection.get("stats"):
        print(f"    collection of {len(collection_ids)}: {stats['papers']:,} papers, "
              f"{stats['citations']:,} citations, h-index {stats['hindex']}, "
              f"g-index {stats['gindex']}"
              + (" (saturated: g cannot exceed the number of papers)"
                 if stats["gindex"] >= stats["papers"] > 0 else ""))
    print(f"{'Updated' if changed else 'Unchanged'}: {args.output}")

    if bibs:
        header = "%% GENERATED by inspirehep-fetch.py -- do not edit.\n"
        bib_changed = write_if_changed(args.bib, header + "\n\n".join(
            bibs[r] for r in sorted(bibs, reverse=True)) + "\n")
        print(f"{'Updated' if bib_changed else 'Unchanged'}: {args.bib} "
              f"({len(bibs)} entr{'y' if len(bibs) == 1 else 'ies'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
