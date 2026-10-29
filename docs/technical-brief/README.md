# Coast-wide bearing-only localization research paper

The six-page IEEE conference-format paper is `main.pdf`. The source is `main.tex`, and the centralized bibliography is `references.bib`. Authorship is J.C. Vaught.

The paper explains the observation model, physical-object identity, conservative geodesic bounds, distinct-object matching, land visibility, terminal certification, reusable indexing, and parallel execution. The evaluation uses the existing corrected Charleston record. No experiment, search, replay, or benchmark was launched to produce the paper.

## Build

A TeX distribution with `latexmk`, `pdflatex`, and BibTeX is required. The IEEE class and bibliography style are vendored from the official CTAN distribution. Typst and CeTZ 0.5.2 regenerate the figures.

```sh
typst compile figures/pruning.typ figures/pruning.pdf
typst compile figures/terminal.typ figures/terminal.pdf
latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex
```

The figures import `figures/stages.json` and `figures/centers.json`, extracted from saved trace and terminal arrays. They contain no newly generated localization measurements. Figures use Typst and CeTZ, the standard plotting palette, square marks, and filled stealth axis arrows.

## Evidence and verification

`evidence/fact-checks.md` identifies authoritative records, timing boundaries, domain coordinate ordering, assumptions, numerical constants, and limitations. `evidence/recorded-result-summary.json` preserves a compact result extract, while `evidence/trace-counts.json` contains counts read from the saved trace. `evidence/review.md` records the independent method review. `evidence/reference-checks.md` lists primary sources and their relevance.

The final PDF compiled without undefined citations or references and without overfull boxes. Every rendered page was inspected. Page images and the compile transcript remain in `qa/`. Build intermediates are ignored; authored sources and visual outputs are retained.

## Scope

The measured result is one corrected simulated frame with known true-north heading, trusted aid classifications, bounded angular error, and explicit chart, position, identity, and numerical premises. The completed traversal, selected sampled estimate, and unresolved local candidates are reported separately. The timing ratio compares two saved searches with identical inputs and settings. It is not a population accuracy result or an isolated ablation of one pruning rule.
