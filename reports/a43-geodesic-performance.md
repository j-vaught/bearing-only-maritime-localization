# A43 geodesic wedge proposal performance

## Change

The geodesic wedge generator now builds each source pair's 3-by-3 sample-cell neighborhood with vector operations and removes duplicate cell pairs using a 1D integer key. The key encodes the observed signed cell-index bounds and falls back to two-column NumPy uniqueness when the bounded key space cannot fit in signed 64-bit arithmetic. This preserves the original lexicographic cell traversal. Cell-center inverse transforms and WGS84 inverse-bearing checks run in per-source batches. Cells within 1e-6 m of the final range or half-diagonal boundary, or within 1e-10 degrees of the angular decision boundary, use the unchanged scalar predicate.

The generator also exposes `solver_tolerance_deg`, defaulting to 1e-7 degrees, and reports that value plus the maximum observed solver residual. The tolerance must be finite and positive and may not exceed `min(0.001 degrees, 0.01 times the minimum effective observation sigma)`. The shared reverse-bearing helper retains its strict 1e-9 degree default. The solver change addresses a separate measured convergence issue without changing ray geometry, angular acceptance, scoring, sampling, or candidate budgets.

## Comparison evidence

The timed workload used five bearings from frozen measurement input SHA-256 `ed1145cee93d4f06ffa983f671319dee49e9a95cb4692a2dd14a44c4a8eeceb1` and the first 100 lexically sorted objects from frozen catalog SHA-256 `2554a1f6e574b6b9ffdefe6bb7b96636d756c9cbd87018be8f7e078738227704`. The frame origin was (-76.43, 39.32), range was 10,000 m, cell size was 250 m, angular tolerance was 0.5 degrees, source-pair budget was 500, candidate-cell budget was 100,000, chunk size was 16, and solver tolerance was 1e-7 degrees. Both implementations received the same ordered inputs and settings; the scalar oracle was loaded from commit 583e4a6 and explicitly run at the same solver tolerance.

Three serial runs under the shared benchmark lock measured scalar times of 0.881, 0.852, and 1.444 s and optimized times of 0.511, 0.516, and 0.514 s. Median runtime fell from 0.881 s to 0.514 s, a 1.715x improvement. The third scalar sample was an observed timing outlier and remains in the record. Both versions produced 11,847 proposals from 500 processed pairs, with zero failed pairs, zero failed samples, and 1,500 degenerate samples. Ordered IDs, ranking, distinct source witnesses, report-to-source mappings, counts, and generation status matched exactly. Maximum normalized-residual difference was 3.34e-16 degrees and maximum evidence-score difference was 2.23e-16.

The same profile run collected 3.66 million calls and 1.49 s of profiled time for the scalar implementation, versus 761 thousand calls and 0.66 s for the optimized implementation. In the final profile, the 500 neighborhood deduplications took 0.064 s; 1D NumPy unique took 0.024 s. The earlier two-column `unique(axis=0)` experiment spent 0.376 s in uniqueness and showed no meaningful raw speedup, so it was replaced before this result. Profile text, binary profiles, and machine-readable comparison details are in the ignored A43 output folder.

The strict 1e-9 degree setting was measured separately before changing the generator tolerance. It produced 25 failed pairs and samples in the same 124,500-sample subset. The independent A04 audit found those residuals between 1.0002e-9 and 1.0150e-9 degrees. At 1e-7 degrees, the scalar oracle and optimized generator both produced zero failed samples and a maximum observed residual of 9.97523e-8 degrees. These strict-baseline failures remain recorded as diagnostic evidence and are not counted as failures of the approved 1e-7 setting.

## Parity and validation

The test suite compares the vectorized cell splat with the original scalar set construction at negative and integer boundaries. It compares batched cell evidence against the scalar predicate around range, half-diagonal, and angular decision boundaries. Complete scalar-oracle signatures match for a 30-pair synthetic fixture and a 100-pair subset of the frozen real catalog. The synthetic fixture generated 454 proposals; the real subset generated 4,511 proposals from 100 processed pairs. The real subset had zero failed pairs and samples.

The focused A43 and A47 suites passed 14 tests. Ruff and `ty check` passed. The suite exercises output ordering, IDs, witness multiplicity and mappings, rankings, counters, solver metadata, and decision-edge acceptance.

During development, an intermediate batch mask incorrectly treated all in-range cells as angularly accepted. Scalar parity caught it: the intermediate run returned 16,463 proposals instead of the expected 11,847. That version was corrected before the final comparison and is not part of any commit.

## Scope and limits

These measurements cover a bounded 500-pair component workload and a 100-pair test subset. They do not establish full-coast runtime or memory behavior. The optimized path preserves the candidate stream, traversal order, IDs, witness semantics, matching, budgets, failure counts, and geodesic predicate. It does not cap or truncate source data. The final comparison source hash is recorded in `comparison-500.json` in the ignored A43 output folder.
