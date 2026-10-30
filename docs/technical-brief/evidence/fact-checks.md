# Evidence audit

The authoritative checkout was inspected read-only on `comech-2080` at `/home/jvaught2/projects/bearing-only-maritime-localization`, branch `gpu-ransac-pair-search`. Experimental-record commit is `d2234278c368fb07dda8cd92e105d911f19b2378`. The run records source commit `369cc8bd81cc2b8c2de179050d65da8302b3f30f`. The pinned public repository URL returned HTTP 200. No remote files, caches, search outputs, or frame assets were changed.

## Search and accuracy

The primary record is `docs/verification/charleston-coastwide-distinct-result.json`, with narrative `docs/charleston-coastwide-distinct-result.md`. The search evaluated 64,875 regions, pruned 58,118, expanded 6,360, and retained 397 terminals. All 39,435 roots completed exactly once in 8.461935504805297 seconds on devices 0–2. Independent verification confirms 4,930 root tasks and per-device counts 13,899, 13,552, and 11,984.

The estimate is latitude 32.77001838830661 and longitude -79.90500313407134. It matches 64 reports, status 0, flags 0, residual 6.280979595813193 degrees. It was frozen before separate truth evaluation. WGS84 error is 2.060303536941018 meters without fitted alignment. Truth is latitude 32.77 and longitude -79.905. This is one simulated frame.

The preceding full-coast search took 98.49529045401141 seconds, evaluated 1,337,411 regions, and retained 36,149 terminals. Inputs, settings, devices, and every-tenth-batch profiling were identical. All 148 consistent cells, assignments, residuals, flags, and selected estimate are unchanged. Time ratio is 11.639806330133181, region reduction is 95.14920992873544 percent, and terminal reduction is approximately 98.902 percent. This is not an isolated matching ablation or a repeated-run distribution.

## Timing boundaries

Search time includes device setup, CPU coordination, synchronization, and output within the search operation. Full pipeline time is 23.449188720434904 seconds. Its input/cache-loading phase takes 7.388 seconds. Selection, separate truth evaluation, and preservation checks also belong to that pipeline. Index rebuilding was forbidden, and cache hashes, sizes, and modification times match before and after.

The trace replay duration is 8.681850305758417 seconds, including synchronous observation and recording overhead. It is not used for the performance comparison. Only saved trace counts are used for stage and category analysis.

## Domain order

The saved bounds tuple is `(east_min, east_max, north_min, north_max)`, explicitly verified in configuration validation and root seeding. Values are `[-220366.49772326474,1424309.6784374337,2699585.3102704627,5088044.413143414]` meters. East span is 1,644,676.1761606984 meters and north span is 2,388,459.1028729514 meters. Their ceiling counts at 10 kilometer scale are 165 and 239, giving 39,435 roots. Individual cell arrays instead use `(xmin,ymin,xmax,ymax)`.

Fourteen midpoint subdivisions reach nominal interior width 0.6103515625 meters under a 1 meter threshold. Approximately 1.059e13 full-quadtree leaves is a theoretical geometric count, not a measured baseline. Edge cells may be clipped.

## Saved trace and terminal statuses

The saved package `/Users/user/Downloads/maritime-pruning-video` contains `pruning/pruning-trace.npz` with all rectangles, decisions, parent identifiers, roots, and depths. States are 6,757 retained, 5,931 land, 39,011 report-without-object, 475 insufficient distinct objects, and 12,701 injective failures. The retained count comprises 6,360 expansions and 397 terminals.

Whole-cell rejections are 89.58458574181117 percent of decisions. The first level rejects 38,584 of 39,435 roots, or 97.84201851147458 percent, leaving 851. Injective failures are 21.85381465294745 percent of recorded rejections. Predicate-order categories are not independent causal effects.

Terminal statuses are 148 consistent, 67 inconsistent, and 182 unresolved. Maximum center distances from the selected estimate are 12.716463883676438 meters overall, 12.134401787730974 meters unresolved, and 8.547836765182117 meters consistent. These are sampled-center spreads. The final level also has 67 whole-cell rejections, which are different from the 67 inconsistent retained-cell centers.

Unresolved centers match 60–63 reports nominally with zero flags. The raw graph does not apply full polygon sight lines, and the universal rectangular barrier list is empty. Nominal prediction applies shoreline sight lines and angular grouping. No per-center cause is established. All old 15,441 unresolved centers were region-pruned; the 182 current unresolved centers were formerly nominally inconsistent.

## Model and numerical assumptions

The sensor is omnidirectional with known global true-north heading. Positive aid reports are true distinct physical detections from one view. Missing aids incur no penalty. Extra detections are vessels only. The frame contains 64 aid and six vessel reports, range 10 kilometers, angular resolution 0.1 degree, detection probability 0.9, no deliberate outliers or type errors, and rejection-sampled Gaussian angular noise with underlying sigma 1/6 degree truncated at ±0.5 degree. The hard bound supplies pruning tolerance.

The run asserts complete chart scope, exact supported positions, certified identities, hard bearing bounds, and accepted numerical validation. These are accepted premises. Missing certificates or unsupported bounds disable affected proofs. Chart absence and distinct matching are independent of unrelated shoreline coverage. Positive certification needs known observer water and supported selected sight lines.

`docs/resident-pruning-bounds.md` and the current envelope source establish lower projection scale 0.99, inverse-position bound 0.01 meter, numerical distance margin 0.001 meter, and primitive bearing margin 1e-6 degree. The revised predicate replaces the legacy stored 30 degree grid-angle allowance. Guards are `d_upper+S <= 100 km`, latitude at most 60 degrees, UTM zone 18 north easting [-500000,1500000] meters and northing [0,6000000] meters. The logarithmic cone needs `d_lower>S`; otherwise angular support remains full. Failed computations cannot silently reject regions. Finite tests do not provide an exhaustive floating-point certificate.

Reduced-length comparison and geodesic scale factors give endpoint sensitivity at most 1.001/r over the supported 100 kilometer range. The north-frame denominator a is conservative because the prime-vertical curvature radius N(phi) is at least a. These are derived comparison bounds, not quoted implementation guarantees.

## Hardware and map scale

Read-only host queries identified RTX 6000 Ada Generation devices 0–3, each reporting 49,140 MiB. The saved run used 0–2. CPU is Intel Xeon w9-3495X, 56 physical cores and 112 hardware threads. Numerical arrays and kernel declarations use float64/double for geometry and assignment. No workload was launched for hardware inspection.

Existing cache headers were read without rebuilding. The canonical view contains 34,436 objects, comprising 32,168 chart objects and 2,268 vessel records. Land has 11,274,092 packed boundary segments. Coverage, unknown, and chart-support layers contain 160,208, 100,913, and four segments. The universal barrier shape is (0,4). These are segment counts, not polygon counts.

`data/sources.json` identifies NOAA ENC Direct GIS aid extracts and an ENC shoreline mosaic. `docs/chart-aid-identity.md` establishes exact parsed coordinate consolidation plus preexisting physical links. The 64,400 chart components become 32,168 chart objects. Vessel traffic does not contribute a likelihood to the aid-only estimator.
