# Submitting to CTAN

`make ctan` builds `inspirehep.zip` in the layout CTAN expects: a single
top-level directory named for the package, holding the sources, the built
documentation, the README, and the licence.

Upload it at <https://ctan.org/upload> with:

| Field | Value |
| --- | --- |
| Package name | `inspirehep` |
| Version | see `\ProvidesPackage` in `inspirehep.sty` |
| Licence | `mit` |
| Summary | Live INSPIRE-HEP citation counts, references, and plots in LaTeX |
| Suggested CTAN path | `/macros/latex/contrib/inspirehep` |
| Topics | `bibtex-supp`, `cv`, `physics` |

Before uploading:

- [ ] `make test` passes (the example builds and the numbers reach the PDF)
- [ ] `make doc` produces `inspirehep.pdf` with no errors
- [ ] the version and date in `\ProvidesPackage` are current, and match this file
- [ ] `README.md` describes the version being uploaded
- [ ] the package loads on a clean TeX Live with only `xparse` and `l3keys2e`,
      and with `pgfplots` only when `plots` is given

A note for the CTAN maintainers, if they ask: the package works without any
network access or shell escape, reading a generated data file that is committed
alongside the document. Shell escape and the bundled Python helper are two
optional ways to regenerate that file; neither is required to typeset.
