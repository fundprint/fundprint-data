#!/bin/sh
# Render paper.md to paper.pdf. Needs xelatex (MiKTeX/TeX Live) and pandoc
# (`pip install pypandoc_binary` bundles one, found via python below).
cd "$(dirname "$0")"
PANDOC="$(command -v pandoc || python -c 'import pypandoc;print(pypandoc.get_pandoc_path())')"
"$PANDOC" -f markdown+autolink_bare_uris paper.md -o paper.pdf --pdf-engine=xelatex -H pdf-header.tex \
  -V geometry:margin=1in -V mainfont=Cambria -V fontsize=11pt -V linestretch=1.15 \
  -V colorlinks=true -V urlcolor=blue
