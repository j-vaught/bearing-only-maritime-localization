# Synthetic baseline v0.1.0

This checked-in result set compares an exhaustive global-bearing position grid with and without multi-start least-squares refinement. Both searches were run with type labels enabled and disabled. The benchmark uses ten fixed-seed trials and is intended as an initial reproducible baseline.

## Full-report results

| Visibility limit | Type labels | Search | Fix rate | Median position error | 95th-percentile position error | Median search time | Under 100 ms |
|---:|:---:|---|---:|---:|---:|---:|---:|
| 10 km | On | Grid | 10% | 355 m | 659 m | 198 ms | 0% |
| 10 km | On | Refined grid | 50% | 159 m | 17.36 km | 154 ms | 0% |
| 10 km | Off | Grid | 10% | 11.96 km | 14.95 km | 209 ms | 0% |
| 10 km | Off | Refined grid | 0% | 13.18 km | 18.72 km | 182 ms | 0% |
| 5 km | On | Grid | 10% | 355 m | 676 m | 106 ms | 20% |
| 5 km | On | Refined grid | 70% | 17 m | 18.03 km | 84 ms | 90% |
| 5 km | Off | Grid | 10% | 433 m | 22.57 km | 58 ms | 100% |
| 5 km | Off | Refined grid | 0% | 17.67 km | 22.51 km | 51 ms | 100% |

The 1 km range produced too few observations to form an estimate in all ten trials. No tested feature count from 3 through 12 met the 95% success target at 5 km or 10 km. The large p95 errors expose failure cases hidden by the median, so median error alone is not a useful reliability claim.

The 10 km range is not a 10 FPS result. The full refined search had a 154 ms median, and the unrefined search had a 198 ms median. At 5 km, the type-aware refined search had an 84 ms median but succeeded in only seven of ten trials. This leaves the baseline short of both reliable localization and a 100 ms total update budget.

## Backend microbenchmark

The separate NumPy angular-residual kernel took 36.3 ms for 512 candidate positions, 61 observations, and 245 AIS records. CuPy was unavailable on the benchmark machine. This kernel benchmark excludes data association and is not directly comparable to the full search timing.

## Interpretation

These numbers come from synthetic stationary points in a square map with Gaussian bearing noise. They are not measurements from archived AIS, navigable water, a shipboard sensor, or a coastline-wide index. Ten trials are too few for a stable reliability estimate. The figures show both a representative bad localization and aggregate performance; all raw trials and grouped metrics are included next to this file.
