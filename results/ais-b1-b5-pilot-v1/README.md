# B1–B5 AIS-layout pilot

This experiment places observers at the five water locations in `sites.csv` and creates synthetic global-bearing reports from one NOAA AIS minute and charted aids to navigation. The search problem uses a 114 × 333 km region, 10 km visibility, 0.5° Gaussian bearing noise, a 0.1° angular merge threshold, a 1 km grid, a 500-iteration randomized baseline, and four hierarchical refinement levels. Candidate locations must be water, and reported objects behind land are removed using a 500 m mask classified from a rendered NOAA Electronic Navigational Chart basemap.

The simulator reports all visible objects. It has AIS coordinates for the 372 vessels, while the 427 charted aids appear as detections without AIS matches. The estimator receives only the simulated bearings, optional object labels, AIS coordinates, and map constraints. It does not receive the observer position, object ranges, chart-aid locations, or object identities. Each AIS detection is assigned a broad boat label in this pilot. The type-blind runs omit that label.

`trials.csv` contains the 1, 5, and 10 km sweep with one seeded realization per site and setting. `repeat-10km.csv` contains three paired seeds per site for the algorithm and type-label comparison. `summary-10km.csv` summarizes each site; `overall-summary-10km.csv` summarizes all 15 site-seed cases for each setting. The success rule is position error at most 250 m and at least three correct AIS associations.

Across the 15 type-aware cases, grid search had 0 successes, 51.3 km median error, and 1,187 ms median runtime. Randomized search had 0 successes, 85.8 km median error, and 37 ms median runtime. Hierarchical search had 3 successes, 18.1 km median error, and 1,035 ms median runtime. All three hierarchical successes came from B1. Removing type labels yielded 0 successes for every algorithm. These timings are search-stage measurements on this local run, not coast-wide or GPU performance measurements.

The site layout had 8, 3, 1, 1, and 9 visible AIS bearings at B1 through B5 at 10 km. The full visible report counts were 23, 4, 1, 7, and 13 after including charted aids. Thus B3 and B4 are information-poor, while B5 demonstrates that a larger count alone does not guarantee recovery. Three noise seeds at five fixed sites are too few to establish a general minimum-feature threshold.

These are simulated observations over a real historical vessel layout, not measured visual detections. The chart-derived 500 m land mask is approximate, and this is a single minute and region. Raw AIS records and the rendered map are not redistributed here. `config.json` records the source time, map service, assumptions, and processing limits.

`b1-b5-estimate-positions.csv` records the true and estimated projected coordinates, local east/north offsets, errors, association counts, and success status for the first paired 10 km type-aware trial at B1 and B5. The zoom figure uses these positions.
