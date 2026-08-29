# inspirehep-latex

Live [INSPIRE-HEP](https://inspirehep.net) citation counts in a LaTeX CV.

```latex
\usepackage[author=1071846]{inspirehep}
...
\inspirepub{2642414}{Towards a Muon Collider}, \emph{Eur. Phys. J. C} 83, 864
```

> Towards a Muon Collider [407 citations], *Eur. Phys. J. C* 83, 864

and, for the summary line people put at the top of a publication list:

```latex
Over \inspirestatround{papers}{100} papers with over
\inspirestatround{citations}{1000} citations and an
$h$-index of \inspirestat{hindex}.
```

> Over 1,400 papers with over 207,000 citations and an *h*-index of 211.

`\inspirestatround` rounds **down**, so a claim of "over N" stays true as the
real figure grows.

The numbers live in a generated file, so **the document always compiles** —
offline, on a stranger's machine, on Overleaf — whether or not it can reach the
network. Refreshing that file is the only part that needs anything.

## Install

Drop `inspirehep.sty` next to your `.tex` file, or anywhere in your `TEXINPUTS`.
That is the whole package; everything else in this repository is documentation,
an example, and the optional helper described below.

## Getting the numbers

The figures come from `inspirehep-data.tex`. There are two ways to produce it,
and they write the same file — use whichever suits your setup.

**Let the package do it.** With unrestricted shell escape, the package fetches
with `curl` at the end of the run and writes the file itself:

```sh
pdflatex -shell-escape cv.tex     # fetches, writes inspirehep-data.tex
pdflatex -shell-escape cv.tex     # numbers appear
```

Two passes the first time, like a cross-reference. After that it only refetches
when there is a reason: a publication you just added, or figures older than
`maxage`. Commit `inspirehep-data.tex` and everyone else can compile normally.

**Or run the helper.** If you cannot enable shell escape — a locked-down
machine, a CI policy, Overleaf — `inspirehep-fetch.py` does the same job from
outside the compile. It needs only the Python standard library:

```sh
python3 inspirehep-fetch.py       # writes inspirehep-data.tex
```

It finds your records and your author id by reading your sources, so it takes no
arguments in the common case.

## Overleaf

Overleaf disables shell escape and its compile containers have no network, so
**nothing can fetch there** — but the CV renders correct numbers, because they
are just an `\input` file. Two ways to keep that file current:

1. **Linked file.** Upload `inspirehep.sty`, then add `inspirehep-data.tex`
   with *Add file → From external URL*, pointed at the raw URL of the file in
   your CV's repository. Overleaf gives linked files a **Refresh** button, so
   you can pull fresh numbers without leaving the editor. Keep the repository
   copy current from CI or a local run, and refreshing is one click.
2. **GitHub sync.** If your project is linked to a repository, pull as usual.
   Note that Overleaf's sync does not fetch git submodules, so include
   `inspirehep.sty` as a file rather than a submodule.

Setting `fetch=off` on Overleaf silences the machinery entirely, though `auto`
already degrades to exactly the same behaviour.

## Commands

| Command | Result |
| --- | --- |
| `\inspirepub{<record>}{<title>}` | the title, linked to its INSPIRE record, followed by the citation count |
| `\inspirecites{<record>}` | just the count, for a title you want to set yourself |
| `\inspirestat{papers\|citations\|hindex}` | an author-level figure |
| `\inspirestatround{<key>}{<step>}` | the same, rounded **down** to a multiple of `<step>` and digit-grouped: `\inspirestatround{citations}{1000}` gives `207,000` |
| `\inspiredefaultstat{<key>}{<value>}` | a fallback used only until the figures have been fetched once |

The record number is the one in the INSPIRE URL:
`inspirehep.net/literature/`**`2642414`**.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `author` | *(none)* | your INSPIRE author recid or BAI, for `\inspirestat`. Omit it and only per-paper counts work. |
| `fetch` | `auto` | `auto` fetches when shell escape allows, `on` demands it and warns when it is unavailable, `off` never fetches |
| `data` | `inspirehep-data` | basename of the generated file |
| `maxage` | `120` | days before the figures are called stale; `0` never warns |
| `mincites` | `1` | counts below this print nothing, so a brand-new paper is left unannotated rather than advertising a zero |

Your author id is the number in your INSPIRE profile URL,
`inspirehep.net/authors/`**`1071846`**, or your BAI (`J.Smith.1`).

## Changing how it looks

Three hooks, each redefinable:

```latex
\renewcommand{\inspiretitleformat}[1]{\textbf{#1}}          % the title
\renewcommand{\inspirecitestext}[1]{#1~cites}               % the words
\renewcommand{\inspirecitesformat}[1]{\nobreakspace{\small[#1]}}  % the wrapper
\renewcommand{\inspirenumsep}{\,}                           % digit grouping
```

To set the annotation in a muted grey, for instance:

```latex
\usepackage{xcolor}
\definecolor{citegrey}{gray}{0.40}
\renewcommand{\inspirecitesformat}[1]{\nobreakspace{\small\color{citegrey}[#1]}}
```

`\inspirepub` links through `hyperref` when your document loads it, and falls
back to plain text when it does not, so load order does not matter.

## How it works, and what it does not do

The package asks INSPIRE for two things: the `citation-summary` aggregation for
your author profile — the same one INSPIRE's own profile pages use, so the
figures match what a reader sees there — and one batched query for the citation
counts of every record your document mentions.

The LaTeX-side fetch has three sharp edges worth knowing about if you read the
source: a `%` anywhere in a shell command is a TeX comment, so the query goes
through `curl -G --data-urlencode` rather than a query string; a `~` opening a
continuation line is swallowed, silently running arguments together; and the
JSON must be read with every catcode set to *other*, or the `%`, `&` and `_` in
an INSPIRE response are read as comment, alignment and subscript and no pattern
ever matches.

Known limits:

- Fetching needs `curl` on the path and unrestricted shell escape. The Python
  helper is the way round both.
- The package pairs records with counts by reading two parallel lists out of the
  response. If they ever come back different lengths it writes nothing and says
  so, rather than writing something wrong. The helper parses the JSON properly
  and has no such limit.
- Counts are whatever INSPIRE reports, including self-citations.

## Licence

LPPL 1.3c. See `LICENSE`.
