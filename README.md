# Bearing-only maritime localization

This repository is a reproducible simulation study for locating a vessel from globally referenced object bearings and a catalog of AIS coordinates. Bearings are measured clockwise from north. The estimator receives object types when enabled, but it does not receive object identities, ranges, non-AIS coordinates, or the vessel's position.

Each simulated sensor report includes every object inside the selected visibility radius. Some objects have AIS coordinates available to the estimator; other detections are deliberately unmatched. Objects are stationary during a simulated instant, the map is two-dimensional, and bearing error is Gaussian. The synthetic scenes do not yet model coastlines, navigable water, receiver coverage, or realistic traffic density.

## Proposed software architecture

The target design keeps the simulator and position search as separate programs. The simulator writes sensor measurements and a separate truth file; the search program receives measurements, an AIS catalogue, and map inputs, while only the evaluator reads simulator truth. A standalone map and visibility component supplies land-water and line-of-sight queries to both programs. The figures show the proposed component boundaries and file responsibilities; these names describe the intended structure and are not all implemented in the current prototype.

The single-page mega figure reads left to right from external ingestion and the `main.py` trunk through shared utilities, the simulator, search, and evaluation to 59 file-level leaves. Each box width follows its label length, while every box shares the same height and font size. No dataset or output folders appear in the software tree.

![Single connected mega diagram of the full software package tree, from external inputs and main.py through all branches and 59 file-level leaves.](docs/figures/software-architecture.png)

The [single-page zoomable PDF](docs/figures/software-architecture.pdf) and editable [Typst source](docs/figures/software-architecture.typ) are also available.

## What a range limit changes

The maximum visibility range filters which AIS records can match an observation at each candidate vessel location. It does **not** limit the measured bearing angles, which still span 360°. With an infinite visibility range, distant AIS records remain eligible and compete in data association. The diagram shows the distinction.

![A 10 km visibility radius excludes distant AIS candidates, while infinite visibility keeps them eligible.](docs/figures/range-constraint.png)

## Initial benchmark

The checked-in baseline runs ten seeded trials with 350 random objects in a 40 km square, 70% AIS availability, and 0.5° bearing noise. It compares a 1 km position grid with and without multi-start refinement, with type labels enabled and disabled. Each run is evaluated at 10 km, 5 km, and 1 km visibility. The full configuration, trial CSV, summaries, and rerun script are under [`results/synthetic-baseline-v0.1.0/`](results/synthetic-baseline-v0.1.0/).

![Median search time and successful-fix rate for the synthetic baseline.](docs/figures/benchmark-performance.png)

At 10 km visibility, type-aware refined grid search had a 50% fix rate and 154 ms median search time. At 5 km it had a 70% fix rate and 84 ms median search time. None of the tested settings achieved the 95% success target. The 1 km scenes had too few detections to estimate a position. The figure and detailed results show why median error alone is insufficient.

The example below is the largest-error full-feature trial at 10 km. The estimator selected 35 AIS associations, but none matched the true observed vessels; its estimated position was 22.53 km from the simulated observer. This is a deliberately visible failure case from the seeded benchmark.

![A 10 km synthetic trial where all predicted AIS matches are wrong and the estimated position is 22.53 km from truth.](docs/figures/failure-case.png)

These results are an initial software baseline, not a real-waterway performance claim. Ten trials do not establish operational reliability. The current search is not yet accurate or fast enough for the coast-wide 10 FPS objective.

## Install and run

Install [uv](https://docs.astral.sh/uv/), then run:

```sh
uv sync
uv run bearing-localization
```

The command writes `trials.csv`, `summary.csv`, `minimum_features.csv`, and `backend_profile.csv` to `results/`. The backend profile measures a batched angular-residual kernel separately from full data association. It includes transfer time when an optional CuPy backend is available, but it is not an end-to-end GPU localization benchmark.

To reproduce the checked-in results and explanatory figures, run:

```sh
sh tools/run_release_benchmark.sh
```

The benchmark takes approximately half a minute on the development machine. It requires Typst to compile the Typst figure sources. The PNG outputs are committed under `docs/figures/`; the editable sources are in `figures/` and use CeTZ or Lilaq.

## Next validation stage

Historical AIS broadcasts can provide realistic vessel layouts for selected dates and coastal regions. A first Chesapeake pilot has now been extracted from NOAA's July 15, 2023 daily archive. It contains 372 unique AIS vessels in a one-minute UTC window and demonstrates the bounded extraction workflow in [`tools/extract_ais_snapshot.py`](tools/extract_ais_snapshot.py). The full national archive and the extracted vessel records remain outside this repository while data-sharing terms are reviewed. The processing details and next steps are in [`docs/experiment-design.md`](docs/experiment-design.md).

This pilot supplies a real vessel layout, but it is not a real visual-observation dataset. The next integration step will convert positions to a local metric frame, sample hypothetical observers over navigable water, and generate global-bearing observations. NOAA Electronic Navigational Chart features will be needed to add buoys and other fixed aids to navigation.

For coast-wide scale, exhaustive position-grid work grows with map area divided by grid-cell area, then incurs assignment cost at every candidate. The next algorithm comparison should add sampled AIS-pair geometry, hierarchical search, and prior-guided local tracking. Ten frames per second allows 100 ms total per update; the current 10 km median search time already exceeds that budget.

## Citation and reuse

The repository includes `CITATION.cff` for software citation. No software license has been selected yet, so the source is publicly viewable but is not currently granted an open-source reuse license. Choose and add a license before redistributing or incorporating the code into another project.
