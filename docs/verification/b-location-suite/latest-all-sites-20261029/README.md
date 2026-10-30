# Latest available results across all B1–B24 locations.

J.C. Vaught.

The dataset covers all 24 authoritative observer locations under the established more-than-15-detected-aids filter. Eight corrected 20 cm catalog reruns replace the corresponding earlier no-estimate rows. The other 16 rows retain their saved original-suite values and provenance. This produces 19 certified estimates, one timeout, and four filtered skips. All completed points use one visual style. Catalog provenance remains available in `catalog_cohort`. No new search, simulation, scoring pass, or runtime measurement was performed for this aggregation.

The three requested figures are in `figures`. All three plots contain only the 19 completed, certified estimates. The timeout is excluded from every plot. Philadelphia has no error because no complete estimate was frozen. B1, B3, B10, and B23 are explicitly identified as filtered skips rather than being assigned zero time or error.

`results.csv` and `results.json` retain all 24 rows, including status, aid count, exact saved search time, post-freeze WGS84 error, attempt directory and catalog cohort. `figures/data.json` preserves plotting values and both source CSV hashes. The untouched tabular timeout record retains its original recorded last-batch value and status, while the timeout is not plotted.

Times exclude preparation, simulation and selection. Error is measured in meters, with lower values better. The first two figures provide both complete views and labeled detailed ranges. Near-overlapping B14/B17 and B19/B22 points share labels in the time-versus-error detail. Figures use Typst, CeTZ, and Lilaq, with vector PDFs and PNG exports. `figures/relationships.pdf` contains all three pages. To rebuild, run `typst compile --input plot=NAME figures/relationships.typ figures/NAME.pdf` for `time-error`, `time-aids`, and `error-aids`. Add `--ppi 152.4` and select a PNG output to regenerate the images.
