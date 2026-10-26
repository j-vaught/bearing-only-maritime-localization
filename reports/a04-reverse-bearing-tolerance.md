# A04 Reverse-Bearing Solver Tolerance Audit

## Scope and frozen inputs

This bounded numerical audit reproduces the 500 source pairs that yielded 25 failed pairs in A43 commit `583e4a6`. It reads the frozen A26 measurement and catalog snapshots from `.agent-work/outputs/a26-wedge-complete-07d8036/static-inputs/`. The measurement SHA-256 is `ed1145cee93d4f06ffa983f671319dee49e9a95cb4692a2dd14a44c4a8eeceb1`, and the catalog SHA-256 is `2554a1f6e574b6b9ffdefe6bb7b96636d756c9cbd87018be8f7e078738227704`. The subset uses the five frozen bearings and the first 100 catalog records sorted lexically by key. The projection frame origin is `(-73.88006033640767, 35.18212735680013)`; it does not affect the reverse geodesic solve.

The reproduction uses the A43 generator sampling schedule. Maximum range is 10,000 m, cell size is 250 m, radial spacing is 125 m, final sample distance is 10,176.776695 m, and angular offsets are `-0.5`, `0`, and `+0.5` degrees. This produces 83 radial samples per angular offset, 249 samples per source pair, and 124,500 samples total. All inverse bearings and distances were independently recomputed with WGS84 `Geod.inv` from returned observer coordinates to the source catalog point.

## Results

At the helper default tolerance of `1e-9` degrees, exactly 25 of 124,500 samples were marked unconverged. Each belonged to a different source pair, matching A43's 25 failed source-pair count. The absolute residuals for those samples ranged from `1.0002168e-9` to `1.0149961e-9` degrees, with 95th percentile `1.0098233e-9` degrees. Thus every failed result missed the strict cutoff by at most 1.50 percent. Independent inverse distance errors for those samples ranged up to `2.182e-9` m. The returned coordinates and independent inverse checks were finite for every failed sample.

The largest failed residual came from report index 2 and catalog key `enc-component:approach:Approach.Beacon_Lateral_point:US4DE12M.000:approach.bcnlat.fid=1933914`. Its source coordinate is `(-75.3929250000001, 39.18361940000068)`, requested bearing is `205.725571474809` degrees, and range is 2,875 m. The residual is `1.014996087e-9` degrees and the independent distance error is `2.77e-11` m.

Repeating the exact 124,500 solves with an explicit solver tolerance of `1e-7` degrees produced zero failed samples and zero failed source pairs. The maximum reported residual was `9.9752299e-8` degrees, and the maximum independent distance error over all samples was `3.3873e-9` m. These results support using `tolerance_deg=1e-7` explicitly inside the proposal generator. The shared helper default remains `1e-9` degrees so callers retain its existing strict behavior.

## Interpretation

The 25 failures are numerical cutoff misses, not evidence of geometric or iterative solver failure. The proposed `1e-7` degree setting is only the reverse-solver convergence tolerance. It does not change the observation sigma, A43 angular tolerance, cell acceptance, proposal ranking, or source-pair and candidate-cell budgets. The generator must continue to mark any sample whose residual exceeds its declared solver tolerance as failed, and it must retain its explicit failed-sample accounting.

A focused regression test covers the worst observed sample. It verifies that the default helper remains unconverged just above `1e-9` degrees, while an explicit `1e-7` degree tolerance converges and independently reproduces the requested bearing and range.
