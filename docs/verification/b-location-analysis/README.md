# B-site runtime, aid congregation, and saved-output diagnosis.

J.C. Vaught.

All figures use saved observations and search outputs. No search, simulator run, scoring pass, or cache rebuild was performed. The final suite contains 20 eligible searches and four sites excluded by the strict more-than-15-aid policy. The Philadelphia timeout is shown at its 600 s limit rather than its last completed batch timestamp.

`time-versus-aids` shows all 20 eligible cases and a detailed view of the 7–18 s interval. `clustering-versus-time` compares runtime with median nearest-aid distance and noiseless angular coverage. `aid-spatial-comparison` shows actual detected representative coordinates around Portland, Boston, and Charleston on identical scales. `nearcoincident-members` compares catalog identities separated by less than 1 cm within the detected angular groups. Each figure has editable Typst source, a PDF, and a PNG export. CeTZ and Lilaq author every graphic.

Portland retains 54 saved centers matching all 67 aid reports. All 90 terminal centers have numerical-boundary bit 8; this candidate-wide warning prevents a positive location certificate. The best complete nominal assignment is 1.563769 m from saved truth, measured posthoc from existing coordinates. It is an unresolved diagnostic candidate and is not a replacement performance result.

The other seven no-estimate sites likewise retain complete nominal assignments, all carrying bit 8. Code review shows that warnings from any gathered pair, raw angular group, or report–group gate can propagate to the entire candidate, including objects or edges not selected in the final assignment. Nearly coincident retained catalog members can trigger the range-tie path. Every no-estimate site has such members, whereas the 11 successful sites and the timeout do not. The stored warning does not identify its setter; per-selected physical residuals and exactness arrays were also not saved. Consequently, this evidence does not prove that removing bit 8 alone would certify those centers.

Ordinary detected-representative spacing is comparable in Portland, Boston, and Charleston. Their median nearest-neighbor distances are 288, 262, and 331 m, respectively. The near-coincident member problem is distinct from the spacing of observed report origins. Method definitions, preserved coordinates, joins, hashes, and limitations are in `methodology.md` and the evidence JSON files. Detailed read-only code and saved-array audits are in the adjacent `b-location-failure-audit` folder.

Rebuild the plots from this directory with Typst. For each of the four figure names, run `typst compile NAME.typ NAME.pdf` and `typst compile --ppi 152.4 NAME.typ NAME.png`. The Python extraction script and project configuration reproduce saved-data metrics; they do not execute localization. Use `uv sync`, `uv run ruff check extract_aid_clustering.py`, and `uv run ty check extract_aid_clustering.py` for its environment and checks.
