# East Coast Chart Aid and Search Experiment

## Data coverage

The chart-aid snapshot contains 64,400 point features returned from NOAA ENC Direct to GIS across the study rectangle, from Florida through Maine and nearby waters. It includes all six available ENC scale bands. The script is `tools/download_eastern_seaboard_chart_aids.py`; its saved CSV and manifest are under `data/chart-aids/`. The raw catalog preserves scale band, source service layer, source chart cell, object identifier, coordinates, and returned attributes. The benchmark makes a second, deduplicated view by grouping on service-layer class and coordinates rounded to five decimal degrees, preferring the largest available scale band. That yields 51,416 aids across all bands and 11,248 aids in the Approach band.

NOAA documents the scale-band duplication and extraction approach in [ENC Direct to GIS](https://nauticalcharts.noaa.gov/learn/encdirect/). NOAA also describes its [GIS data and services](https://www.nauticalcharts.noaa.gov/data/gis-data-and-services.html). These GIS products support this experiment and are not certified navigation products.

## Search setup

This is a seeded diagnostic, not a coast-wide field validation. The simulator generated 23 bearings at B1 in Chesapeake. The estimator then searched the full rectangular study area using 2,268 AIS positions from 2023-07-15 12:00 UTC and the chart-aid snapshot retrieved on 2026-10-24. It received no simulated truth. The comparison includes a 100 km grid, a multilevel hierarchical search, a 2,000-iteration randomized search, pairwise geometric hypotheses with a 10 km visibility limit and spatially pruned target pairs, and a global hierarchical pass followed by local catalog and domain pruning. Each method runs with object-type labels enabled and disabled. The CSV contains all combinations, including an Approach-only catalog sensitivity check.

The success rule is position error no greater than 250 m and at least three correct AIS associations. Search currently uses a local equirectangular projection across the East Coast rectangle. The search domain is not constrained to water, and search does not perform land ray tracing. Therefore these measurements cannot establish performance for real coast-wide localization.

## All-band results with type labels enabled

| Method | Position error | Correct AIS matches | Elapsed time |
| --- | ---: | ---: | ---: |
| Grid, 100 km initial spacing | 272.785 km | 0 | 416 ms |
| Hierarchical, 100 km coarse spacing | 4.077 km | 0 | 395 ms |
| Randomized, 2,000 iterations | 25.767 km | 0 | 2,470 ms |
| Pairwise hypotheses, range-pruned | 593.949 km | 0 | 26,335 ms |
| Global then local hierarchical | 6.853 km | 0 | 583 ms |

No method met the success rule. The hierarchical method is the strongest current result in this setup, but its 4.1 km position error is far outside the target. Pairwise search is much slower and its current hypothesis sampling does not recover a useful estimate. The second-stage local search prunes the catalog and bounds after a coarse estimate, but did not improve the result in this case. Type-blind results and Approach-only sensitivity results are recorded in `algorithm-comparison.csv`.

An earlier pairwise run reported a 20 m estimate. Reordering the catalog changed that result dramatically, exposing input-order dependence in hypothesis sampling. That result is excluded; the current downloader writes a deterministic ordering, and the benchmark has been rerun against those sorted inputs.

## Reproduction

Run `uv run python tools/download_eastern_seaboard_chart_aids.py` to refresh the live NOAA snapshot, then `uv run python tools/benchmark_eastern_seaboard_search.py` to regenerate the CSV and `config.json`. The configuration stores input SHA-256 hashes and records the search settings and known limitations. The live service may change, so refreshing the source can change both the checksum and benchmark results.
