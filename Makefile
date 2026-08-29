# Build the manual, and the archive CTAN wants.
PKG     = inspirehep
VERSION = 2.0

.PHONY: all doc example test unit lint check clean ctan

# What CI runs, and what to run before pushing.
check: lint unit test doc

doc: $(PKG).pdf

$(PKG).pdf: $(PKG)-doc.tex $(PKG).sty
	-python3 $(PKG)-fetch.py $(PKG)-doc.tex --output $(PKG)-doc-data.tex
	pdflatex -interaction=nonstopmode $(PKG)-doc.tex
	pdflatex -interaction=nonstopmode $(PKG)-doc.tex
	mv $(PKG)-doc.pdf $(PKG).pdf

example:
	cd example && cp ../$(PKG).sty . && \
	  python3 ../$(PKG)-fetch.py example.tex && \
	  pdflatex -interaction=nonstopmode example.tex

# The same assertions CI makes, from the same script, so the two cannot drift.
test: example
	sh test/check_example.sh example/example.pdf

# The pure parts of the helper: patterns, path walking, escaping, file shape.
# No network, so this is the check to run while editing.
unit:
	python3 -m unittest discover -s test -v

# ruff for the helper; lint_package.py for the things a compiler cannot see --
# a command that never reached the manual, a version that disagrees with the
# Makefile, an expl3 block left open.
lint:
	ruff check .
	python3 test/lint_package.py

# CTAN takes a single archive whose top level is one directory named for the
# package, holding the sources, the built documentation, the README, and the
# licence.
ctan: doc example/inspirehep-data.tex
	rm -rf ctan/$(PKG) $(PKG).zip
	mkdir -p ctan/$(PKG)/example
	cp $(PKG).sty $(PKG)-doc.tex $(PKG)-doc-data.tex $(PKG).pdf $(PKG)-fetch.py \
	   README.md LICENSE ctan/$(PKG)/
	cp example/example.tex example/inspirehep-data.tex ctan/$(PKG)/example/
	cd ctan && zip -qr ../$(PKG).zip $(PKG)
	@echo "wrote $(PKG).zip"

# Tracked, so a fresh clone already has it; this is for a clone that does not,
# which would otherwise fail the copy below with a confusing message.
example/inspirehep-data.tex:
	cd example && python3 ../$(PKG)-fetch.py example.tex

clean:
	rm -f *.aux *.log *.out *.toc *.vrb $(PKG)-doc.pdf
	rm -rf ctan
	cd example && rm -f *.aux *.log *.out *.pdf $(PKG).sty *.json
