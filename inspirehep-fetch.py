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
RECORD_RE = re.compile(r"\\inspire(?:pub|cites|title|ref|year)\s*" + _OPT + r"\s*\{\s*(\d+)\s*\}")
PLOT_RE = re.compile(r"\\inspireplot\s*" + _OPT + r"\s*\{\s*(\d+)\s*\}")
# \inspirecite / \inspirekey, but not \inspirecites: the \s*{ guard sees to that.
BIB_RE = re.compile(r"\\inspire(?:cite|key)\s*\{\s*(\d+)\s*\}")
AUTHOR_USE_RE = re.compile(
    r"\\inspire(?:papers|citations|hindex|authorstat|authorplot)\s*"
    + _OPT + r"\s*\{\s*([A-Za-z0-9.\-]+)\s*\}")
URL_AUTHOR_RE = re.compile(r"inspirehep\.net/authors/(\d+)")


def day_number(d: datetime.date) -> int:
    """A day count TeX can also compute, as 365*year + 31*month + day.

    inspirehep.sty works out today's value the same way from \\year, \\month and
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


def fetch_formatted(recid: str, style: str) -> str:
    """The reference as INSPIRE itself renders it, in latex-eu or latex-us.

    Using INSPIRE's own formatting rather than assembling one from the metadata
    means the result matches what everyone else in the field cites, errata and
    all, and there is no author-list or journal-abbreviation logic to get wrong.

    The response is a \\bibitem block: the \\cite and \\bibitem lines are
    dropped, the title -- which INSPIRE comments out -- is restored, and the
    trailing "N citations counted in INSPIRE" note is left off because the
    package tracks that itself and it would otherwise go stale in the file.
    """
    url = f"{API}/literature/{recid}?format={style}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read().decode("utf-8", "replace")

    parts = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("\\bibitem") or line.startswith("%\\cite"):
            continue
        if re.match(r"^%\s*\d+\s+citations?\s+counted", line):
            continue
        if line.startswith("%"):                    # the commented-out title
            title = line.lstrip("%").strip()
            title = title.strip("`").rstrip(",").rstrip("'").strip()
            if title:
                parts.append("``" + title + "''")
            continue
        parts.append(line)
    return " ".join(parts).strip()


def fetch_records(recids: list[str], style: str = "latex-eu") -> dict[str, dict]:
    """Everything the package can say about each record.

    Counts, titles and keys come from one batched metadata query; the reference
    line comes from INSPIRE's own renderer, one call per record.
    """
    if not recids:
        return {}
    result = api_get("literature", {
        "q": " or ".join(f"recid {r}" for r in recids),
        "fields": RECORD_FIELDS,
        "size": str(max(len(recids), 1)),
    })
    records = {}
    for hit in result["hits"]["hits"]:
        meta = hit["metadata"]
        recid = str(meta["control_number"])
        records[recid] = {
            "cites": int(meta.get("citation_count") or 0),
            "title": tex_escape(meta["titles"][0]["title"]) if meta.get("titles") else "",
            "key": (meta.get("texkeys") or [""])[0],
            "ref": "",
        }
    for recid in records:
        try:
            records[recid]["ref"] = fetch_formatted(recid, style)
        except Exception as exc:  # noqa: BLE001 - a missing reference is not fatal
            print(f"  ! no {style} reference for {recid}: {exc}", file=sys.stderr)
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
    """Papers, citations and h-index, from the same aggregation INSPIRE's own
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


def discover(sources: list[Path]) -> tuple[list[str], str | None]:
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in sources)
    # There is no author= package option any more -- a document may discuss
    # several people -- so the authors are exactly the ones its commands name.
    author = None
    wanted = {
        "records": sorted(set(RECORD_RE.findall(text)) | set(BIB_RE.findall(text)), key=int),
        "plots":   sorted(set(PLOT_RE.findall(text)), key=int),
        "bibtex":  sorted(set(BIB_RE.findall(text)), key=int),
        "authors": sorted(set(AUTHOR_USE_RE.findall(text)) | ({author} if author else set())),
    }
    return wanted, author


def render(records: dict, plots: dict, authors: dict, author_years: dict,
           primary: str | None) -> str:
    """The generated file the package reads: one declaration per fact."""
    today = datetime.date.today()
    out = ["%% GENERATED by inspirehep-fetch.py -- do not edit."]

    for recid in sorted(records, key=int, reverse=True):
        r = records[recid]
        out.append(f"\\inspiresetcites{{{recid}}}{{{r['cites']}}}")
        if r["title"]:
            out.append(f"\\inspiresettitle{{{recid}}}{{{r['title']}}}")
        if r["key"]:
            out.append(f"\\inspiresetkey{{{recid}}}{{{r['key']}}}")
        if r["ref"]:
            out.append(f"\\inspiresetref{{{recid}}}{{{r['ref']}}}")
    for recid in sorted(plots, key=int, reverse=True):
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
                        help="BibTeX file for records used with \\inspirecite (default: %(default)s)")
    parser.add_argument("--author", default=None,
                        help="INSPIRE author recid or BAI (default: the author= option in your sources)")
    parser.add_argument("--style", default="latex-eu", choices=["latex-eu", "latex-us"],
                        help="INSPIRE reference format (default: %(default)s)")
    parser.add_argument("--strict", action="store_true",
                        help="exit non-zero if the API cannot be reached")
    parser.add_argument("--print", dest="to_stdout", action="store_true",
                        help="write to stdout instead of a file")
    args = parser.parse_args(argv)

    sources = args.sources or sorted(args.output.resolve().parent.glob("*.tex"))
    sources = [p for p in sources if p.resolve() != args.output.resolve()]
    if not sources:
        print("no LaTeX sources to scan", file=sys.stderr)
        return 1

    wanted, found_author = discover(sources)
    primary = args.author or found_author
    if args.author and found_author != args.author:
        wanted["authors"] = sorted(set(wanted["authors"]) | {args.author})
    print(f"Scanning {len(sources)} file(s): {len(wanted['records'])} record(s), "
          f"{len(wanted['plots'])} plot(s), {len(wanted['bibtex'])} citation(s), "
          f"{len(wanted['authors'])} author(s)")

    try:
        records = fetch_records(wanted["records"], args.style)
        plots = {recid: fetch_years(f"refersto recid {recid}") for recid in wanted["plots"]}
        authors, author_years = {}, {}
        for aid in wanted["authors"]:
            bai = resolve_bai(aid)
            authors[aid] = author_summary(bai)
            author_years[aid] = {
                "papers": fetch_years(f"a {bai}"),
                "citations": fetch_years(f"refersto a {bai}"),
            }
        bibs = {recid: fetch_bibtex(recid) for recid in wanted["bibtex"]}
    except Exception as exc:  # noqa: BLE001 - every failure degrades the same way
        print(f"INSPIRE-HEP lookup failed: {exc}", file=sys.stderr)
        if args.strict:
            return 1
        print("Keeping the existing figures; the document will still build.", file=sys.stderr)
        return 0

    content = render(records, plots, authors, author_years, primary)
    if args.to_stdout:
        sys.stdout.write(content)
        return 0

    changed = write_if_changed(args.output, content)
    print(f"  {len(records)} record(s), {len(plots)} plot series, {len(authors)} author(s)")
    for aid, stat in authors.items():
        print(f"    {aid}: {stat['papers']:,} papers, {stat['citations']:,} citations, "
              f"h-index {stat['hindex']}")
    print(f"{'Updated' if changed else 'Unchanged'}: {args.output}")

    if bibs:
        header = "%% GENERATED by inspirehep-fetch.py -- do not edit.\n"
        bib_changed = write_if_changed(args.bib, header + "\n\n".join(
            bibs[r] for r in sorted(bibs, key=int, reverse=True)) + "\n")
        print(f"{'Updated' if bib_changed else 'Unchanged'}: {args.bib} "
              f"({len(bibs)} entr{'y' if len(bibs) == 1 else 'ies'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
