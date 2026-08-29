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

RECORD_RE = re.compile(r"\\inspire(?:pub|cites)\s*\{\s*(\d+)\s*\}")
AUTHOR_RE = re.compile(r"\\usepackage\s*\[([^\]]*)\]\s*\{\s*inspirehep\s*\}", re.S)
AUTHORKEY_RE = re.compile(r"\bauthor\s*=\s*\{?\s*([A-Za-z0-9.\-]+)")
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


def fetch_citation_counts(recids: list[str]) -> dict[str, int]:
    """{record: citation count}, in a single query however many records."""
    if not recids:
        return {}
    result = api_get("literature", {
        "q": " or ".join(f"recid {r}" for r in recids),
        "fields": "control_number,citation_count",
        "size": str(max(len(recids), 1)),
    })
    counts = {str(h["metadata"]["control_number"]): int(h["metadata"].get("citation_count") or 0)
              for h in result["hits"]["hits"]}
    missing = sorted(set(recids) - set(counts))
    if missing:
        print(f"  ! no INSPIRE record for: {', '.join(missing)}", file=sys.stderr)
    return counts


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


def discover(sources: list[Path]) -> tuple[list[str], str | None]:
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in sources)
    recids = sorted(set(RECORD_RE.findall(text)), key=int)
    author = None
    for options in AUTHOR_RE.findall(text):
        if (m := AUTHORKEY_RE.search(options)):
            author = m.group(1)
            break
    if author is None:
        urls = set(URL_AUTHOR_RE.findall(text))
        if len(urls) == 1:
            author = urls.pop()
    return recids, author


def render(counts: dict[str, int], summary: dict[str, int] | None) -> str:
    today = datetime.date.today()
    lines = ["%% GENERATED by inspirehep-fetch.py -- do not edit."]
    if summary:
        for key in ("papers", "citations", "hindex"):
            lines.append(f"\\inspiresetstat{{{key}}}{{{summary[key]}}}")
    for recid in sorted(counts, key=int, reverse=True):
        lines.append(f"\\inspiresetcites{{{recid}}}{{{counts[recid]}}}")
    lines.append(f"\\inspiresetfetched{{{today.isoformat()}}}{{{day_number(today)}}}")
    return "\n".join(lines) + "\n"


def write_if_changed(path: Path, content: str) -> bool:
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
    parser.add_argument("--author", default=None,
                        help="INSPIRE author recid or BAI (default: the author= option in your sources)")
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

    recids, author = discover(sources)
    author = args.author or author
    print(f"Scanning {len(sources)} file(s): {len(recids)} record(s)"
          + (f", author {author}" if author else ", no author (summary figures skipped)"))

    try:
        counts = fetch_citation_counts(recids)
        summary = fetch_summary(author) if author else None
    except Exception as exc:  # noqa: BLE001 - every failure degrades the same way
        print(f"INSPIRE-HEP lookup failed: {exc}", file=sys.stderr)
        if args.strict:
            return 1
        print("Keeping the existing figures; the document will still build.", file=sys.stderr)
        return 0

    content = render(counts, summary)
    if args.to_stdout:
        sys.stdout.write(content)
        return 0
    changed = write_if_changed(args.output, content)
    if summary:
        print(f"  {summary['papers']:,} papers, {summary['citations']:,} citations, "
              f"h-index {summary['hindex']}")
    print(f"  {len(counts)} per-paper count(s)")
    print(f"{'Updated' if changed else 'Unchanged'}: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
