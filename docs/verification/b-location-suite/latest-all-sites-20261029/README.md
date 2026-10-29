# Latest available results across all B1–B24 locations.

J.C. Vaught.

The dataset covers all 24 authoritative observer locations under the established more-than-15-detected-aids filter. Eight corrected 20 cm catalog reruns replace the corresponding earlier no-estimate rows. The other 16 rows retain their saved original-suite values and provenance. This produces 19 certified estimates, one timeout, and four filtered skips. Earlier-catalog and corrected-catalog points are distinguished visually and in `catalog_cohort`. No new search, simulation, scoring pass, or runtime measurement was performed for this aggregation.

The three requested figures are in `figures`. Search time versus detected-aid count contains all 20 searched sites, with B11 marked at the 600 s timeout. Its last completed batch timestamp is not substituted for the watchdog duration. The two error plots contain all 19 certified estimates. Philadelphia has no error because no complete estimate was frozen. B1, B3, B10, and B23 are explicitly identified as filtered skips rather than being assigned zero time or error.

`results.csv` and `results.json` retain all 24 rows, including status, aid count, exact saved search time, post-freeze WGS84 error, attempt directory and catalog cohort. `figures/data.json` preserves plotting values and both source CSV hashes. For the timeout only, the plotted lower bound is 600 s while the untouched tabular search field retains the original recorded last-batch value.

Times exclude preparation, simulation and selection. Error is measured in meters, with lower values better. The first two figures provide both complete views and labeled detailed ranges. Near-overlapping B14/B17 and B19/B22 points share labels in the time-versus-error detail. Figures use Typst, CeTZ, and Lilaq, with vector PDFs and PNG exports. `figures/relationships.pdf` contains all three pages. To rebuild, run `typst compile --input plot=NAME figures/relationships.typ figures/NAME.pdf` for `time-error`, `time-aids`, and `error-aids`. Add `--ppi 152.4` and select a PNG output to regenerate the images.
