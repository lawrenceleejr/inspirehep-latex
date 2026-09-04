#!/usr/bin/env python3
"""Tests for the parts of inspirehep-fetch.py that do not touch the network.

Everything here is pure: pattern matching, path walking, escaping, and the
shape of the generated file.  The API calls are deliberately not mocked --
what breaks in practice is not the HTTP, it is a pattern that stops matching
or a path that resolves somewhere unexpected, and those are exactly what these
cover.  The example build in CI exercises the network path end to end.

Run with:  python3 -m unittest discover -s test -v
"""

import datetime
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

# The helper is a script, not a package, and its name has a hyphen in it, so it
# is loaded by path rather than imported.
_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("fetch", _ROOT / "inspirehep-fetch.py")
fetch = importlib.util.module_from_spec(_spec)
sys.modules["fetch"] = fetch
_spec.loader.exec_module(fetch)


class DayNumber(unittest.TestCase):
    """The staleness check compares this against TeX's own arithmetic, so the
    formula has to stay 365*year + 31*month + day on both sides."""

    def test_matches_the_tex_formula(self):
        self.assertEqual(fetch.day_number(datetime.date(2026, 8, 29)),
                         365 * 2026 + 31 * 8 + 29)

    def test_increases_with_time(self):
        earlier = fetch.day_number(datetime.date(2026, 2, 10))
        later = fetch.day_number(datetime.date(2026, 8, 29))
        self.assertLess(earlier, later)
        # The package calls 120 days stale; the drift from uneven month lengths
        # must not swamp that.
        self.assertAlmostEqual(later - earlier, 200, delta=25)


class TexEscape(unittest.TestCase):
    """INSPIRE titles arrive with LaTeX maths already in them."""

    def test_escapes_specials_in_running_text(self):
        self.assertEqual(fetch.tex_escape("Fermi & Dirac"), r"Fermi \& Dirac")
        self.assertEqual(fetch.tex_escape("100% of b_quarks"),
                         r"100\% of b\_quarks")

    def test_leaves_maths_alone(self):
        for title in (r"at $\sqrt{s}=13$ TeV",
                      r"$H\to\tau^+\tau^-$ decays",
                      r"$p_T > 30$ GeV"):
            self.assertEqual(fetch.tex_escape(title), title)

    def test_escapes_outside_maths_only(self):
        self.assertEqual(fetch.tex_escape(r"R&D on $b\bar{b}$ & jets"),
                         r"R\&D on $b\bar{b}$ \& jets")


class Identifiers(unittest.TestCase):
    """A record is named by recid or by texkey, told apart by shape."""

    def test_recids_are_not_texkeys(self):
        for recid in ("2642414", "1071846", "0"):
            self.assertFalse(fetch.TEXKEY_RE.match(recid), recid)
            self.assertEqual(fetch.query_for(recid), f"recid {recid}")

    def test_texkeys_are_recognised(self):
        for key in ("Lee:2018pag", "ATLAS:2019abc", "Aad:2020xyz1",
                    "O'Brien:2021aaa", "van.Dyk:2022bbb"):
            self.assertTrue(fetch.TEXKEY_RE.match(key), key)
            self.assertEqual(fetch.query_for(key), f"texkey {key}")

    def test_near_misses_are_not_texkeys(self):
        # The field name is "texkey", singular; an invalid field makes INSPIRE
        # return zero results for the whole OR query rather than erroring, so
        # a malformed id must not be sent as one.
        for bad in ("Lee2018pag", ":2018pag", "Lee:18pag", "Lee:", ""):
            self.assertFalse(fetch.TEXKEY_RE.match(bad), bad)


class Patterns(unittest.TestCase):
    """Every command takes an optional [key=value], which the patterns skip."""

    def test_finds_records_with_and_without_options(self):
        text = r"""
            \inspirepub{2642414}{A title}
            \inspirepub[cites=false]{1071846}
            \inspirecites{Lee:2018pag}
            \inspireref[errata, collab]{3137624}
            \inspireyear{2690093}
            \inspiretitle{ 3103093 }
        """
        self.assertEqual(
            sorted(set(fetch.RECORD_RE.findall(text))),
            ["1071846", "2642414", "2690093", "3103093", "3137624",
             "Lee:2018pag"])

    def test_cites_is_not_mistaken_for_cite(self):
        # \inspirecites is a citation count; \inspirecite is a \cite.  Only the
        # latter needs a BibTeX entry.
        text = r"\inspirecites{2642414} \inspirecite{Lee:2018pag} \inspirekey{123}"
        self.assertEqual(sorted(set(fetch.BIB_RE.findall(text))),
                         ["123", "Lee:2018pag"])

    def test_authors_come_from_the_commands_that_name_them(self):
        text = r"""
            \inspirepapers{1071846}
            \inspirecitations[round=1000]{1071846}
            \inspirehindex{J.R.Ellis.1}
            \inspireauthorplot[kind=papers]{1234567}
        """
        self.assertEqual(sorted(set(fetch.AUTHOR_USE_RE.findall(text))),
                         ["1071846", "1234567", "J.R.Ellis.1"])

    def test_an_author_link_in_prose_is_not_an_author(self):
        # A document may discuss several people, so nothing is inferred from a
        # bare URL -- only from a command that actually asks for a figure.
        text = r"\href{https://inspirehep.net/authors/1071846}{my profile}"
        self.assertEqual(fetch.AUTHOR_USE_RE.findall(text), [])


class Series(unittest.TestCase):
    r"""Coordinate pairs, not `year: count'.

    A colon is a letter under \ExplSyntaxOn and would never match the
    catcode-12 one in a data file, so the pairs are emitted ready to use.
    """

    def test_emits_coordinate_pairs(self):
        self.assertEqual(fetch.series([("2019", 1), ("2020", 7)]),
                         "(2019,1) (2020,7)")

    def test_has_no_colons(self):
        self.assertNotIn(":", fetch.series([("2019", 1), ("2020", 7)]))

    def test_empty_is_empty(self):
        self.assertEqual(fetch.series([]), "")


class WalkInputs(unittest.TestCase):
    r"""LaTeX resolves \input relative to the main document, not the includer."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, name, text):
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_follows_input_include_and_subfile(self):
        root = self.write("main.tex", r"""
            \input{one}
            \include{two}
            \subfile{three.tex}
        """)
        for name in ("one.tex", "two.tex", "three.tex"):
            self.write(name, "")
        found = {p.name for p in fetch.walk_inputs([root])}
        self.assertEqual(found, {"main.tex", "one.tex", "two.tex", "three.tex"})

    def test_adds_the_tex_suffix_when_omitted(self):
        root = self.write("main.tex", r"\input{chapter}")
        self.write("chapter.tex", "")
        self.assertIn("chapter.tex", {p.name for p in fetch.walk_inputs([root])})

    def test_resolves_relative_to_the_root_first(self):
        # parts/body.tex says \input{shared}, and shared.tex sits beside the
        # main document -- which is where LaTeX would look.
        root = self.write("main.tex", r"\input{parts/body}")
        self.write("parts/body.tex", r"\input{shared}")
        self.write("shared.tex", "")
        self.assertIn("shared.tex", {p.name for p in fetch.walk_inputs([root])})

    def test_also_resolves_beside_the_including_file(self):
        # What the subfiles and import packages do.
        root = self.write("main.tex", r"\input{parts/body}")
        self.write("parts/body.tex", r"\input{local}")
        self.write("parts/local.tex", "")
        found = fetch.walk_inputs([root])
        self.assertIn(self.dir / "parts" / "local.tex", found)

    def test_a_cycle_terminates(self):
        root = self.write("a.tex", r"\input{b}")
        self.write("b.tex", r"\input{a}")
        found = fetch.walk_inputs([root])
        self.assertEqual(sorted(p.name for p in found), ["a.tex", "b.tex"])

    def test_a_missing_file_is_skipped(self):
        # It may be generated, or inside a conditional; neither is our business.
        root = self.write("main.tex", r"\input{nowhere} \input{here}")
        self.write("here.tex", "")
        found = {p.name for p in fetch.walk_inputs([root])}
        self.assertEqual(found, {"main.tex", "here.tex"})

    def test_each_file_is_visited_once(self):
        root = self.write("main.tex", r"\input{a} \input{a}")
        self.write("a.tex", "")
        self.assertEqual(len(fetch.walk_inputs([root])), 2)


class Discover(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_collects_each_kind_separately(self):
        path = self.dir / "doc.tex"
        path.write_text(r"""
            \inspirepub{2642414}{Title}
            \inspireplot{2642414}
            \inspirecite{Lee:2018pag}
            \inspirepapers{1071846}
        """, encoding="utf-8")
        wanted = fetch.discover([path])
        # Anything cited also needs its metadata, so it appears under records.
        self.assertEqual(wanted["records"], ["2642414", "Lee:2018pag"])
        self.assertEqual(wanted["plots"], ["2642414"])
        self.assertEqual(wanted["bibtex"], ["Lee:2018pag"])
        self.assertEqual(wanted["authors"], ["1071846"])

    def test_an_empty_document_wants_nothing(self):
        path = self.dir / "doc.tex"
        path.write_text(r"\documentclass{article}\begin{document}\end{document}",
                        encoding="utf-8")
        self.assertEqual(fetch.discover([path]),
                         {"records": [], "plots": [], "bibtex": [], "authors": [],
                          "collectionplot": False})

    def test_notices_whether_a_collection_plot_is_drawn(self):
        """The collection plot is the one command that costs an extra query,
        so it is only fetched for a document that actually draws one."""
        plain = self.dir / "plain.tex"
        plain.write_text(r"\inspirepub{2642414}", encoding="utf-8")
        self.assertFalse(fetch.discover([plain])["collectionplot"])
        plotted = self.dir / "plotted.tex"
        plotted.write_text(r"\inspirepub{2642414} \inspirecollectionplot",
                           encoding="utf-8")
        self.assertTrue(fetch.discover([plotted])["collectionplot"])


class WriteIfChanged(unittest.TestCase):
    """A rebuild that changes nothing must not show up in git status."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "data.tex"
        self.addCleanup(self._tmp.cleanup)

    def test_writes_when_absent(self):
        self.assertTrue(fetch.write_if_changed(self.path, "one"))
        self.assertEqual(self.path.read_text(), "one")

    def test_rewrites_when_different(self):
        fetch.write_if_changed(self.path, "one")
        self.assertTrue(fetch.write_if_changed(self.path, "two"))
        self.assertEqual(self.path.read_text(), "two")

    def test_leaves_an_identical_file_alone(self):
        fetch.write_if_changed(self.path, "one")
        before = self.path.stat().st_mtime_ns
        self.assertFalse(fetch.write_if_changed(self.path, "one"))
        self.assertEqual(self.path.stat().st_mtime_ns, before)

    def test_leaves_no_temporary_behind(self):
        fetch.write_if_changed(self.path, "one")
        self.assertEqual([p.name for p in self.path.parent.iterdir()],
                         ["data.tex"])


def a_record(**overrides):
    """A record shaped the way fetch_records always builds them."""
    record = {"recid": "2642414", "cites": 407, "title": "A title",
              "key": "Lee:2018pag", "ref": {}, "collab": ""}
    record.update(overrides)
    return record


class Render(unittest.TestCase):
    """The generated file is read by TeX, so its shape is part of the contract."""

    def test_declares_records_authors_and_the_fetch_date(self):
        out = fetch.render(
            records={"2642414": a_record()},
            plots={},
            authors={"1071846": {"papers": 1467, "citations": 207838,
                                 "hindex": 211}},
            author_years={},
            primary=None)
        self.assertIn(r"\inspiresetcites{2642414}{407}", out)
        self.assertIn(r"\inspiresettitle{2642414}{A title}", out)
        self.assertIn(r"\inspiresetkey{2642414}{Lee:2018pag}", out)
        self.assertIn(r"\inspiresetauthorstat{1071846}{papers}{1467}", out)
        self.assertIn(r"\inspiresetfetched{", out)
        self.assertTrue(out.startswith("%%"), "must say it is generated")

    def test_the_primary_author_also_gets_the_short_form(self):
        stats = {"1071846": {"papers": 1467, "citations": 207838, "hindex": 211}}
        with_primary = fetch.render({}, {}, stats, {}, primary="1071846")
        without = fetch.render({}, {}, stats, {}, primary=None)
        self.assertIn(r"\inspiresetstat{papers}{1467}", with_primary)
        self.assertNotIn(r"\inspiresetstat{papers}{1467}", without)

    def test_nothing_fetched_still_records_the_date(self):
        out = fetch.render({}, {}, {}, {}, primary=None)
        self.assertIn(r"\inspiresetfetched{", out)


class BibTargets(unittest.TestCase):
    """One BibTeX entry per record, however the document names it.

    A repeated key makes BibTeX skip the entry outright, which silently costs
    every citation of that paper its reference.
    """

    def test_two_names_for_one_record_yield_one_entry(self):
        records = {"1701002": a_record(recid="1701002"),
                   "Lee:2018pag": a_record(recid="1701002")}
        targets = fetch.bib_targets(["1701002", "Lee:2018pag"], records)
        self.assertEqual(list(targets), ["1701002"])

    def test_the_first_name_used_is_the_one_reported(self):
        records = {"Lee:2018pag": a_record(recid="1701002"),
                   "1701002": a_record(recid="1701002")}
        targets = fetch.bib_targets(["Lee:2018pag", "1701002"], records)
        self.assertEqual(targets, {"1701002": "Lee:2018pag"})

    def test_distinct_records_are_kept_apart(self):
        records = {"1701002": a_record(recid="1701002"),
                   "2642414": a_record(recid="2642414")}
        targets = fetch.bib_targets(["1701002", "2642414"], records)
        self.assertEqual(sorted(targets), ["1701002", "2642414"])

    def test_an_unknown_identifier_stands_for_itself(self):
        # Nothing was fetched for it, so there is no record number to map to.
        self.assertEqual(fetch.bib_targets(["Nobody:2099zzz"], {}),
                         {"Nobody:2099zzz": "Nobody:2099zzz"})

    def test_nothing_wanted_is_nothing_fetched(self):
        self.assertEqual(fetch.bib_targets([], {}), {})


class CollectionQuery(unittest.TestCase):
    """The query goes into a data file that TeX reads, so its encoding is not
    a free choice."""

    def test_ors_every_record_together(self):
        self.assertEqual(fetch.collection_query(["1", "2"]),
                         "recid+1+or+recid+2")

    def test_a_texkey_is_asked_for_as_a_texkey(self):
        self.assertEqual(fetch.collection_query(["1701002", "Lee:2018pag"]),
                         "recid+1701002+or+texkey+Lee:2018pag")

    def test_one_paper_is_a_legitimate_collection(self):
        self.assertEqual(fetch.collection_query(["1701002"]), "recid+1701002")

    def test_carries_nothing_tex_would_reinterpret(self):
        """Percent-encoding would be the obvious choice and is unusable: a `%'
        in the data file comments out the rest of the line."""
        query = fetch.collection_query(["1701002", "Lee:2018pag", "2642414"])
        self.assertNotIn("%", query)
        for hostile in "&#$_^~\\{} ":
            self.assertNotIn(hostile, query,
                             f"{hostile!r} would need escaping in TeX")


class CollectionYears(unittest.TestCase):
    r"""`refersto' has to be repeated in every clause.

    Written once in front of a parenthesised list -- refersto (recid A or
    recid B) -- INSPIRE accepts it and quietly means something far wider: for
    a two-paper set it answered with 353,154 citing papers starting in 1900,
    a plot of the right shape and four orders of magnitude wrong.
    """

    def setUp(self):
        self.asked = []
        real = fetch.fetch_years
        self.addCleanup(setattr, fetch, "fetch_years", real)

    def stub(self, pairs):
        def fetch_years(query):
            self.asked.append(query)
            return pairs
        fetch.fetch_years = fetch_years

    def test_refersto_is_repeated_per_clause(self):
        self.stub([("2020", 3)])
        fetch.collection_years(["1", "2", "Lee:2018pag"])
        query = self.asked[0]
        self.assertEqual(query,
                         "refersto recid 1 or refersto recid 2 "
                         "or refersto texkey Lee:2018pag")
        self.assertNotIn("(", query, "a parenthesised group means something else")
        self.assertEqual(query.count("refersto"), 3)

    def test_a_plausible_series_is_returned(self):
        self.stub([("2019", 5), ("2020", 7)])
        self.assertEqual(fetch.collection_years(["1"], citations=100),
                         [("2019", 5), ("2020", 7)])

    def test_refuses_a_series_far_larger_than_the_citations(self):
        """A citing paper contributes at least one citation, so citing papers
        cannot greatly outnumber them.  This is what catches the query above
        having matched half the literature."""
        self.stub([(str(y), 3000) for y in range(1900, 2026)])
        with self.assertRaises(RuntimeError) as caught:
            fetch.collection_years(["1", "2"], citations=2657)
        self.assertIn("refersto", str(caught.exception))

    def test_the_margin_is_wide_enough_for_a_small_collection(self):
        """INSPIRE's stored counts and its reference index do disagree on
        individual records, so a close call must not be refused."""
        self.stub([("2020", 12)])
        fetch.collection_years(["1"], citations=2)


class GIndex(unittest.TestCase):
    """Egghe's g-index: the largest g whose top g papers carry g^2 citations.

    INSPIRE publishes no g-index, so unlike every other figure here this one is
    computed rather than fetched -- which makes it the one that can be wrong on
    its own.
    """

    def test_the_worked_example(self):
        # 9,18,...,81 against 1,4,...,81: g=9 holds, g=10 needs 100 and has 90.
        self.assertEqual(fetch.g_index([9] * 12), 9)

    def test_stops_where_the_cumulative_count_falls_behind(self):
        self.assertEqual(fetch.g_index([5, 1, 1]), 2)   # 7 citations, 3^2 = 9

    def test_order_does_not_matter(self):
        self.assertEqual(fetch.g_index([1, 5, 1]), fetch.g_index([5, 1, 1]))

    def test_saturates_on_a_short_list(self):
        """g cannot exceed the number of papers, so a CV-length list of
        well-cited papers returns its own length.  Documented, not a bug."""
        counts = [408, 319, 261, 260, 248, 152, 146, 135, 119, 108, 103, 100,
                  83, 50, 33, 28, 27, 23, 22, 13, 9, 4, 3, 2, 1, 0, 0]
        self.assertEqual(fetch.g_index(counts), len(counts))

    def test_reproduces_inspires_h_index_from_the_same_counts(self):
        """Not a g-index test as such: it is the reason these counts are
        trusted for g.  INSPIRE's facet gives h=19 for exactly this set."""
        counts = [408, 319, 261, 260, 248, 152, 146, 135, 119, 108, 103, 100,
                  83, 50, 33, 28, 27, 23, 22, 13, 9, 4, 3, 2, 1, 0, 0]
        ordered = sorted(counts, reverse=True)
        h = max((i for i, c in enumerate(ordered, 1) if c >= i), default=0)
        self.assertEqual(h, 19)

    def test_nothing_cited_is_zero(self):
        self.assertEqual(fetch.g_index([]), 0)
        self.assertEqual(fetch.g_index([0, 0]), 0)


class RenderCollection(unittest.TestCase):
    def test_declares_the_query_the_figures_and_the_series(self):
        out = fetch.render({}, {}, {}, {}, None, collection={
            "query": "recid+1+or+recid+2",
            "stats": {"papers": 27, "citations": 2657, "hindex": 19,
                      "gindex": 27},
            "years": [("2019", 5), ("2020", 7)],
        })
        self.assertIn(r"\inspiresetcollectionquery{recid+1+or+recid+2}", out)
        self.assertIn(r"\inspiresetcollection{hindex}{19}", out)
        self.assertIn(r"\inspiresetcollection{papers}{27}", out)
        self.assertIn(r"\inspiresetcollectionyears{(2019,5) (2020,7)}", out)

    def test_the_query_survives_a_failed_lookup(self):
        """It is built from what the document names, so a fetch that reached
        nothing still leaves the link working."""
        out = fetch.render({}, {}, {}, {}, None,
                           collection={"query": "recid+1"})
        self.assertIn(r"\inspiresetcollectionquery{recid+1}", out)
        self.assertNotIn(r"\inspiresetcollection{", out)

    def test_no_collection_declares_nothing(self):
        out = fetch.render({}, {}, {}, {}, None)
        self.assertNotIn("inspiresetcollection", out)


if __name__ == "__main__":
    unittest.main()
