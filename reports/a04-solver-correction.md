# A04 reverse-bearing solver correction

## Finding

The reverse solver's forward residual met the requested tolerance, but a second, independent `Geod.inv` check could remain slightly outside that same tolerance because the two geodesic computations round differently. The frozen A26 coordinate-only replay found 209 such samples among 83,001,660 samples. At the declared `1e-7` degree tolerance, the largest independent residual was `1.0005828698922414e-7` degrees.

The solver now uses half the caller's tolerance as its internal forward-residual stop target. The public tolerance is unchanged, and the independent inverse check remains authoritative against that full tolerance. This changes only numerical convergence margin. It does not change bearing uncertainty, the likelihood, candidate acceptance, or any coverage claim.

## Bounded replay

The replay used the frozen A26 measurement and catalog inputs. The measurement file SHA-256 was `ed1145cee93d4f06ffa983f671319dee49e9a95cb4692a2dd14a44c4a8eeceb1`; the catalog file SHA-256 was `2554a1f6e574b6b9ffdefe6bb7b96636d756c9cbd87018be8f7e078738227704`. It covered 66,668 catalog records, five recorded bearings per record, ranges sampled every 125 m through the 10 km limit plus half-cell diagonal, and three angular offsets (`-0.5`, `0`, `+0.5` degrees). The calculation used coordinate inputs only and did not inspect truth or run a search.

The 209 baseline failures were replayed through the corrected solver with the same public tolerance. All 209 converged. Their maximum independently recomputed bearing residual was `1.532498572487384e-10` degrees, and their maximum range error was `2.0461357053136453e-9` m. This corrected replay was limited to the previously failing samples, not all 83,001,660 samples.

The worst baseline case is retained as a regression. It used catalog key `enc-component:approach:Approach.Buoy_Lateral_point:US4GA1CH.000:approach.boylat.fid=986615`, catalog coordinate `(-80.98065189999943, 31.796930200000133)`, bearing `215.65789052601954` degrees, and range `625` m. Its prior independent residual was `1.0005828698922414e-7` degrees. The corrected solver converges with independent residual `1.8189894035458565e-12` degrees and range error `3.1093350116861984e-10` m.

## Validation

The focused reverse-bearing tests, Ruff checks, and `ty` checks passed after the correction. The regression exercises the public `1e-7` degree tolerance and independently checks the inverse bearing and range.

