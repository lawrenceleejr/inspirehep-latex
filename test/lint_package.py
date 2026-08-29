#!/usr/bin/env python3
"""Checks on the package that a compiler cannot make.

A package whose selling point is its manual fails quietly when the two drift:
the code grows a command, the manual does not mention it, and nobody notices
until someone goes looking for it.  So this asserts that every public command
and every option is documented, that the declared version agrees everywhere,
and that a few traps this package has actually fallen into stay fixed.

Run with:  python3 test/lint_package.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STY = ROOT / "inspirehep.sty"
DOC = ROOT / "inspirehep-doc.tex"
README = ROOT / "README.md"
MAKEFILE = ROOT / "Makefile"

# Commands the package defines for its own internals rather than for users.
# \inspirehepdrawplot is called by the package at normal catcodes, because
# pgfplots keys do not survive \ExplSyntaxOn; users never write it.
INTERNAL = {"inspirehepdrawplot"}


def strip_comments(tex: str) -> str:
    """LaTeX source without its comments, so a command named in a note is not
    mistaken for one that is defined or documented."""
    return re.sub(r"(?<!\\)%.*", "", tex)

problems: list[str] = []


def fail(message: str) -> None:
    problems.append(message)


def public_commands(sty: str) -> set[str]:
    r"""Every \inspire... command a user could reasonably call.

    Both the commands proper (\NewDocumentCommand) and the formatting hooks
    (\providecommand), since overriding a hook is a documented thing to do.
    """
    found = set()
    for pattern in (r"\\(?:New|Renew|Provide)DocumentCommand\s*\\(inspire\w+)",
                    r"\\providecommand\s*\\(inspire\w+)"):
        found |= set(re.findall(pattern, sty))
    return found - INTERNAL


def declared_options(sty: str) -> set[str]:
    """Option names from the l3keys block, taken from their .initial:n lines
    so each is counted once however many properties it sets."""
    block = re.search(r"\\keys_define:nn\s*\{\s*inspirehep\s*\}(.*?)\n\s*\}\s*\n",
                      sty, re.S)
    if not block:
        fail("no \\keys_define:nn { inspirehep } block found in inspirehep.sty")
        return set()
    return set(re.findall(r"^\s*(\w+)\s+\.initial:n", block.group(1), re.M))


def main() -> int:
    sty = STY.read_text(encoding="utf-8")
    sty_code = strip_comments(sty)
    doc = DOC.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    makefile = MAKEFILE.read_text(encoding="utf-8")

    # --- every command reaches the manual -------------------------------
    commands = public_commands(sty_code)
    if not commands:
        fail("no public commands found -- the pattern above has stopped matching")
    undocumented = sorted(c for c in commands if f"\\{c}" not in doc)
    for name in undocumented:
        fail(f"\\{name} is defined in inspirehep.sty but never mentioned in "
             f"{DOC.name}")

    # The README is the shop window, so the commands a user actually writes
    # belong there too.  Two kinds are exempt: the formatting hooks, which may
    # reasonably live only in the manual, and the \inspireset... family, which
    # is the interface between the fetcher and the package -- those appear in
    # generated files, not in anybody's document.
    written_by_hand = {c for c in commands
                       if re.search(r"\\NewDocumentCommand\s*\\" + c + r"\b", sty_code)
                       and not c.startswith("inspireset")}
    for name in sorted(c for c in written_by_hand if f"\\{c}" not in readme):
        fail(f"\\{name} is a public command but is not in README.md")

    # --- every option reaches the manual --------------------------------
    options = declared_options(sty_code)
    if not options:
        fail("no options found -- the .initial:n pattern has stopped matching")
    for name in sorted(options):
        if not re.search(rf"\b{re.escape(name)}\b", doc):
            fail(f"option `{name}' is declared in inspirehep.sty but is not "
                 f"documented in {DOC.name}")

    # --- the manual does not promise what the package does not have -----
    named = set(re.findall(r"\\verb\|\\(inspire\w+)", doc))
    named |= set(re.findall(r"\\(?:re)?newcommand\{\\(inspire\w+)\}", doc))
    for name in sorted(named):
        if name not in commands and name not in INTERNAL:
            fail(f"{DOC.name} documents \\{name}, which inspirehep.sty does "
                 f"not define")

    # --- one version, in three places -----------------------------------
    provides = re.search(r"\\ProvidesPackage\s*\{inspirehep\}\s*"
                         r"\[(\d{4}/\d{2}/\d{2})\s+v([\d.]+)", sty)
    if not provides:
        fail("\\ProvidesPackage in inspirehep.sty has no date and version")
    else:
        _, version = provides.groups()
        makefile_version = re.search(r"^VERSION\s*=\s*([\d.]+)", makefile, re.M)
        if not makefile_version:
            fail("the Makefile declares no VERSION")
        elif makefile_version.group(1) != version:
            fail(f"version mismatch: inspirehep.sty says v{version}, the "
                 f"Makefile says {makefile_version.group(1)}")
        doc_version = re.search(r"\\date\{Version\s+([\d.]+)", doc)
        if not doc_version:
            fail(f"{DOC.name} has no \\date{{Version ...}} line")
        elif doc_version.group(1) != version:
            fail(f"version mismatch: inspirehep.sty says v{version}, "
                 f"{DOC.name} says {doc_version.group(1)}")

    # --- traps this package has actually fallen into --------------------
    # A `:' is a letter under \ExplSyntaxOn, so a data file must never rely on
    # one as a separator: the plot series is emitted as coordinate pairs.
    helper = (ROOT / "inspirehep-fetch.py").read_text(encoding="utf-8")
    if re.search(r'f"\{year\}\s*:', helper):
        fail("inspirehep-fetch.py emits a colon-separated series; a colon is a "
             "letter under \\ExplSyntaxOn and will not match in a data file")

    # A ~ is an ordinary space token under \ExplSyntaxOn, and TeX discards
    # spaces after a control word -- so a ~ written straight after a variable
    # vanishes.  In the shell command that self-fetches, that silently joined
    # the script name to the filename and the fetch failed with the document
    # still compiling, which is exactly the kind of thing nothing else catches.
    for block in re.findall(r"\\sys_shell_now:\w+\s*\{(.*?)\n\s*\}", sty_code, re.S):
        for swallowed in re.findall(r"(\\[A-Za-z_:]{2,})~", block):
            fail(f"`{swallowed}~' in a shell command: the ~ is swallowed as that "
                 f"control word's terminator, so no space is emitted -- use "
                 f"\\c_space_tl")

    # pgfplots is optional and must stay behind the `plots' option, because
    # loading it costs every document that does not draw anything.
    for match in re.finditer(r"\\RequirePackage\s*\{\s*pgfplots\s*\}", sty):
        context = sty[max(0, match.start() - 200):match.start()]
        if "plots" not in context:
            fail("pgfplots is required unconditionally; it belongs behind the "
                 "`plots' option")

    # Every \ExplSyntaxOn needs its Off, or everything after the package sees
    # spaces stripped and colons turned into letters.
    opened = sty_code.count(r"\ExplSyntaxOn")
    closed = sty_code.count(r"\ExplSyntaxOff")
    if opened != closed:
        fail(f"unbalanced expl3 syntax: {opened} \\ExplSyntaxOn against "
             f"{closed} \\ExplSyntaxOff")

    # --- report ---------------------------------------------------------
    print(f"{len(commands)} command(s), {len(options)} option(s) checked "
          f"against {DOC.name} and {README.name}")
    if problems:
        print()
        for problem in problems:
            print(f"  {problem}")
        print(f"\n{len(problems)} problem(s)")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
