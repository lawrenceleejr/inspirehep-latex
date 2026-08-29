# Build the manual, and the archive CTAN wants.
PKG     = inspirehep
VERSION = 2.0

.PHONY: doc example test clean ctan

doc: $(PKG).pdf

$(PKG).pdf: $(PKG)-doc.tex $(PKG).sty
	pdflatex -interaction=nonstopmode $(PKG)-doc.tex
	pdflatex -interaction=nonstopmode $(PKG)-doc.tex
	mv $(PKG)-doc.pdf $(PKG).pdf

example:
	cd example && cp ../$(PKG).sty . && \
	  python3 ../$(PKG)-fetch.py example.tex && \
	  pdflatex -interaction=nonstopmode example.tex

test: example
	cd example && pdftotext example.pdf - | grep -qE '\[[0-9]+ citations?\]'
	@echo "OK"

# CTAN takes a single archive whose top level is one directory named for the
# package, holding the sources, the built documentation, the README and the
# licence.
ctan: doc
	rm -rf ctan/$(PKG) $(PKG).zip
	mkdir -p ctan/$(PKG)/example
	cp $(PKG).sty $(PKG)-doc.tex $(PKG).pdf $(PKG)-fetch.py \
	   README.md LICENSE ctan/$(PKG)/
	cp example/example.tex ctan/$(PKG)/example/
	cd ctan && zip -qr ../$(PKG).zip $(PKG)
	@echo "wrote $(PKG).zip"

clean:
	rm -f *.aux *.log *.out *.toc $(PKG)-doc.pdf
	rm -rf ctan
	cd example && rm -f *.aux *.log *.out *.pdf $(PKG).sty *.json
