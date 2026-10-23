# Experiment design and data provenance

## Current synthetic baseline

The checked-in baseline was generated on 2026-10-23 with seed `20261023`. It uses ten Monte Carlo trials. Each trial has 350 stationary objects drawn uniformly over a 40 km by 40 km square. Seventy percent of the objects are assigned AIS coordinates. The scene reports global bearings clockwise from north with 0.5° Gaussian noise. Visibility radii are 10 km, 5 km, and 1 km. Search uses a 1 km position grid and up to eight least-squares refinement starts. The success criterion is position error no greater than 250 m and at least three correctly associated AIS objects.

This is a controlled synthetic baseline, not a real-waterway evaluation. The map has no coastline or navigability mask, and its random point distribution does not reproduce shipping lanes, vessel-type frequencies, receiver outages, or charted navigation aids. The ten-trial result is exploratory and estimates neither deployment reliability nor the final minimum number of features.

The exact command is in `tools/run_release_benchmark.sh`. Trial-level outputs, grouped summaries, feature-count results, backend profiling, and the configuration record are in `results/synthetic-baseline-v0.1.0/`. The randomized failure example in `figures/data/` is selected from the largest position error among the 10 km, type-aware refined-search trials and is recreated by `tools/export_failure_case.py`.

## How visibility range enters the search

For an observer candidate $\mathbf{p}$ and an AIS landmark $\mathbf{q}_j$, the predicted global bearing is

$$
\hat{\theta}_j(\mathbf{p}) = \operatorname{atan2}(q_{j,E}-p_E, q_{j,N}-p_N).
$$

When the sensor has a maximum visibility distance $R$, an AIS record can match an observation only when $\|\mathbf{q}_j-\mathbf{p}\| \le R$. The range limit therefore removes distant candidate landmarks at each candidate observer location. It does not constrain the reported angle to a smaller angular interval. Bearings still span the full 360°.

With a finite range, the search can gate AIS candidates by distance and compare only the nearby angular hypotheses. With an infinite range, every AIS record remains eligible at every observer candidate, so more identities compete for each observation and, under the complete-report simulation assumption, the observer also receives more detections. The effect on association quality and runtime must be measured together because more distant objects can add useful geometry while also increasing assignment cost and ambiguity.

## Land-aware visibility and angular resolution

Range alone does not determine whether a landmark is visible. A hypothetical observer position must lie in water, and the line segment from that position to a landmark must not cross intervening land. A shoreline feature can remain visible when the sight line reaches the feature itself, while a vessel or aid on the far side of a peninsula or island is occluded. The same candidate-specific visibility rule must be used when generating observations and when deciding which AIS records can match them. An AIS record hidden by land must not be treated as a missed detection.

Land therefore affects both the search domain and the observation model. The position search should exclude land cells before scoring candidates, and each candidate should use its own range and line-of-sight gates before data association. A navigable-water mask is preferable to a generic water mask because lakes, marshes, and narrow or obstructed channels are not necessarily valid boat positions. Shoreline geometry should come from vector hydrography or nautical chart features; cartographic basemap colors are suitable for visual placement checks but not as the authoritative visibility mask.

The sensor model must also account for angular resolution. When two or more visible objects fall within a configurable angular-resolution interval, the simulator should emit one detection for the group rather than separate bearings. The nearest visible object should be treated as the front object in the initial model, including when the objects are ships. Bearing groups must wrap correctly across north, and the truth record should retain the hidden or merged object identities for evaluation while the estimator receives only the single detection. The angular-resolution threshold should be varied as an experiment parameter until a sensor specification supplies a fixed value.

Land intersection and angular merging must be included in end-to-end runtime measurements. A rasterized land mask can make water-cell filtering and batched ray checks efficient, while vector segment-intersection checks can provide a higher-resolution reference for validating mask resolution. Any coast-wide 10 FPS result must include mask preparation, line-of-sight evaluation, and candidate filtering rather than timing bearing association alone.

## Historical AIS evaluation plan

Marine Cadastre publishes U.S. Coast Guard Nationwide Automatic Identification System (NAIS) broadcast points from 2009 onward. Data from 2015 onward are available as compressed daily CSV files. The 2023 national point archive contains about 3.0 billion records and is listed at 110.4 GB compressed, so the first real-data experiment should use a short period and a coastal region rather than mirror an entire year. NOAA's AccessAIS service is designed to clip data to a selected area and time window, although its ordering service is currently listed as unavailable. Daily bulk files remain available.

The first real-data scene should select a UTC minute and a Mid-Atlantic region, extract the nearest usable broadcast point per vessel within a defined time tolerance, and project longitude and latitude into a local metric coordinate system. The hypothetical observer should be sampled from water-only positions, not uniformly over a rectangular bounding box. A shoreline or navigable-water mask is therefore required.

### Chesapeake pilot

A first extraction was completed from NOAA's daily archive for July 15, 2023. It read 10,068,463 archive rows and selected the vessel report nearest 12:00:30 UTC for each MMSI with a report in the 12:00:00–12:01:00 UTC window and the WGS 84 bounding box from 36.5° to 39.5° north and 76.8° to 75.5° west. The selected set contains 372 unique vessels from 373 reports. The reusable extractor is `tools/extract_ais_snapshot.py`.

The full national ZIP archive is approximately 395 MB compressed and 1.08 GB uncompressed. Neither that archive nor the extracted vessel records are checked into Git. The pilot's extracted CSV and provenance manifest are stored locally at `work/ais-pilot/chesapeake_2023-07-15_1200Z.csv` and `work/ais-pilot/chesapeake_2023-07-15_1200Z.manifest.json`. The CSV drops MMSI, vessel name, and call sign, but its time and position values still describe individual AIS transmissions. The pilot remains local until the terms for sharing derived records are reviewed.

This slice provides vessel positions for a real AIS traffic layout, not measured visual detections. It contains no charted non-AIS buoys, shore structures, or observer ground truth. It can support the first integration pass by converting the vessel coordinates into a local metric projection and generating synthetic global bearings at hypothetical water locations. A shoreline or navigable-water mask and charted features are still required before testing scenes that include all visible-object types.

Broadcast points are not a complete catalogue of visually detectable objects. NOAA states that these archive points come from land-based receiving stations rather than satellite AIS, and coverage generally does not extend more than 40–50 miles offshore. The archive also documents gaps and excludes some law-enforcement and military vessels. The simulation should record those coverage limitations and avoid treating missing AIS reports as proof that no vessel was present.

To model aids to navigation and other static objects, join the vessel points with charted features from NOAA Electronic Navigational Charts (ENCs). The ENC source supplies charted aids such as buoys, beacons, and lights. The scene can then distinguish AIS-transmitting objects, charted non-AIS objects, and uncharted or undetected clutter. A historical comparison must use chart versions appropriate to the AIS date where available; otherwise, chart-feature status should be identified as a modern approximation.

Raw AIS records should not be committed to this repository. Keep source data outside Git, record its source, date, geographic extent, processing steps, and checksum, and check applicable use terms before sharing derived records or vessel tracks. The repository should contain acquisition and preprocessing instructions plus small synthetic fixtures, not a redistribution of the national archive.

## Scaling and comparison plan

The present exhaustive search evaluates every position-grid cell. For a search area of size $A$ and grid spacing $h$, the number of positions scales approximately as $A/h^2$. Reducing grid spacing by a factor of ten therefore creates about one hundred times as many position candidates, before data-association work is counted. Each candidate also incurs bearing residual and assignment calculations over observations and AIS objects.

The current comparison contains an unrefined grid and a grid with multi-start least-squares refinement. The next algorithm comparison should add geometry-based AIS-pair hypotheses, hierarchical coarse-to-fine search, and a prior-guided local tracker. The coast-wide 10 FPS objective corresponds to 100 ms per update, including data loading and sensor processing; current benchmark search times include assignment but exclude those system-level costs. GPU profiling currently covers only a batched angular-residual microkernel and is not an end-to-end GPU result.

## Sources

NOAA Office for Coastal Management. “Automatic Identification System Frequently Asked Questions.” [Data coverage, sampling, formats, and access](https://coast.noaa.gov/data/marinecadastre/ais/faq.pdf).

NOAA Office for Coastal Management. “AIS Point Data Summary.” [Annual record counts and file sizes](https://coast.noaa.gov/data/marinecadastre/ais/point-data-summary.pdf).

NOAA Office for Coastal Management. “Nationwide Automatic Identification System 2023.” [Dataset metadata and daily archive index](https://www.fisheries.noaa.gov/inport/item/70162).

NOAA Office of Coast Survey. “ENC Direct to GIS.” [Charted coastal features and geographic extracts](https://www.nauticalcharts.noaa.gov/learn/encdirect/).

U.S. Coast Guard Navigation Center. “AIS Data Sharing Categories and Requirements.” [Historical data conditions](https://navcen.uscg.gov/ais-data-sharing-categories-requirements).
