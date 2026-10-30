# Runtime, position error, and detected-aid relationships.

J.C. Vaught.

The three scatterplots use only the eight completed 20 cm catalog reruns in the adjacent results CSV. Position error is the post-freeze WGS84 distance in meters. Lower error is better. Runtime is search-only seconds, excluding shared preparation, simulation, and selection. The orange point identifies B18 Savannah. B19 Brunswick and B22 Palm Beach nearly coincide in the time-versus-error plot and share one label; both points are drawn.

`time-error` plots search time against position error. `time-aids` plots search time against detected charting-aid count. `error-aids` plots position error against detected charting-aid count. Each has a PNG and PDF. `relationships.pdf` combines the three pages. `data.json` preserves exact values and the source CSV hash.

All figures are authored with Typst, CeTZ, and Lilaq. Rebuild each figure with `typst compile --input plot=NAME relationships.typ NAME.pdf` and `typst compile --ppi 152.4 --input plot=NAME relationships.typ NAME.png`, using the three names above. No localization, simulation, or scoring was run to produce these plots.
