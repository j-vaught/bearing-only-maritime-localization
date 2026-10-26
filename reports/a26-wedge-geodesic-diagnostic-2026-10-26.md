# Wedge-Geodesic Full-Coast Diagnostic

Prepared by J.C. Vaught.

This was a development-only diagnostic for development / port-001 / placement-001 / realization r0 / 10 km / types off / nominal. The search used the frozen five-observation measurements, full-coast catalog, MapV2 candidate, configuration, and scope manifest. The command received no truth file. Evaluation-only truth remained in a separate directory and was opened only after the search output was written. This run was not an acceptance or calibration result, and it did not use held-out data.

## Frozen inputs and replay

The search implementation was commit `0ae7c1e5bd6ee43674fc3a8038e80539446e2777`. The runner and evaluator helper are archived in `tools/run_a26_wedge_geodesic_diagnostic.py` and `tools/evaluate_a26_wedge_geodesic_diagnostic.py`. The run used the `wedge_geodesic` algorithm with 250 m cells, 0.5 degree wedge tolerance, 333,340 source-pair budget, 5,000,000 cell-storage budget, 4,096-cell chunks, 256 exact candidates, and solver tolerance 1e-7 degree. Refinement remained enabled with 8 modes, 50 iterations, and a 100 m initial step. The timeout was 1,800 seconds with process-group cleanup.

| Input | SHA-256 |
|---|---|
| Measurements | `ed1145cee93d4f06ffa983f671319dee49e9a95cb4692a2dd14a44c4a8eeceb1` |
| Search configuration | `adef2647b728c2459c64a7f88b3c7a821f6bce8f05fb25cacd0e98dbd9cf2df0` |
| Scope manifest | `d32e42622c7d959a26009bb23b5c5ea8336d4b13329f59e3fffb8e3f25e175e1` |
| Full-coast catalog, decompressed | `2554a1f6e574b6b9ffdefe6bb7b96636d756c9cbd87018be8f7e078738227704` |
| Full-coast candidate MapV2, decompressed | `cce049c5911b39072d30f26c09947782a78e350e65a3f69c6923312fba3f9d93` |

The catalog archive is tracked at `data/catalogs/accuracy-inputs-v2/east-coast-fullcoast-catalog-v2.json.gz`; decompressing it reproduces the catalog hash above. The candidate map archive is tracked at `data/maps/accuracy-inputs-v3/noaa-enc-east-coast-fullcoast-map-v3-candidate.json.gz`; its decompressed bytes reproduce the recorded map hash. The exact measurements, config, scope manifest, estimate, command manifest, evaluator diagnostics, truth-side files, and compressed telemetry are in `data/accuracy/diagnostics/2026-10-26/a26-wedge-geodesic/`.

## Search result

The process completed with return code 0 before its timeout, but the search result itself reports `search_status=failed` and `decision_status=ambiguous`. The decision reason is `missing_frozen_calibration`. It produced 261 saved hypotheses, of which 256 exact candidates were scored. The exact-scoring status is `budget_exhausted`; all 256 scored candidates returned ordinary score results, but the search is not complete or acceptable because proposal generation recorded numerical failures.

The proposal pass processed all 333,340 source pairs and reported 1,457,546 candidate cells. It reached `generation_status=completed` with `generation_budget_reason=none`, but `generation_complete=false`. The run recorded 209 failed source pairs and 209 failed geodesic samples among 83,001,660 samples. It also recorded 1,000,020 degenerate samples, 418 unresolved-model pairs, 66,668 unique witness keys, and 24,328,107 unique witness edges. The maximum reported solver residual was 1.0005828699e-7 degree, just above the declared 1e-7 degree tolerance.

| Matching cardinality | Candidate cells |
|---:|---:|
| 5 | 239,528 |
| 4 | 191,041 |
| 3 | 216,474 |
| 2 | 283,419 |
| 1 | 527,084 |

The priority histogram records matching cardinality, vote count, and candidate cells. It shows that all 256 evaluated proposals had cardinality 5; the selected proposal subset was ordered by matching count, vote count, known-water status within a tier, residual evidence, and cell order.

| Matching cardinality | Votes | Candidate cells |
|---:|---:|---:|
| 5 | 5 | 239,528 |
| 4 | 5 | 17,358 |
| 4 | 4 | 173,683 |
| 3 | 5 | 9,959 |
| 3 | 4 | 13,017 |
| 3 | 3 | 193,498 |
| 2 | 5 | 7,700 |
| 2 | 4 | 4,906 |
| 2 | 3 | 15,075 |
| 2 | 2 | 255,738 |
| 1 | 5 | 4,836 |
| 1 | 4 | 2,502 |
| 1 | 3 | 5,931 |
| 1 | 2 | 26,728 |
| 1 | 1 | 487,087 |

The top-scoring estimate has score 23.7862 and CRS-correct geodesic error 548,327.465 m. It assigns four of the five reports and has zero exact catalog-key matches to the corresponding truth records. The minimum-error saved hypothesis is score-rank 105 with score 108.9873 and error 128,757.746 m. It assigns all five reports but also has zero exact catalog-key matches to the corresponding truth records. Across all 261 saved hypotheses, the minimum position error is 128.758 km and the median is 2,067.223 km. These error figures are evaluation-only. The estimate coordinates in EPSG:32618 were compared geodesically against truth recorded in EPSG:32619 after transforming both to WGS84.

A fixed-point evaluator pass scored the truth location at 5.206587168, with five assignments, matched cost 4.206587168, clutter cost 1.0, and zero misses. The 250 m cell containing the truth center, cell (1594, 4161), is water and scored 17.849166741 with four assignments. Its exact cell-predicate scan over all source pairs found five votes, matching cardinality 5, 13 unique catalog keys, and 18 accepted report/source incidences. The corrected per-report evidence score for that cell is 0.358545753 and its mean normalized angular residual is 0.23667468. The final evaluator audit is SHA-256 58eed8123e615e92127fb8f2511e44c02d6139f7456a49c080a2cc952651b0d7; it supersedes prior audit revisions 5a3f574d34f6c51a54b1979045d887be9035e1d0637d9a54df461c1f0c07d5d2 and 057608bda4be0e922a7438dbcb2dc5e7994d6fb31297f04dc678841c7f089f78. Replaying the configured 3 angular offsets and 83 range samples showed that all 18 eligible source/report pairs emit the containing cell, with 333 valid sample-neighbor hits. The 256th evaluated witness had mean normalized residual 0.02674343, while the containing cell's mean normalized residual was 0.23667468. A neighboring water cell (1594, 4160) scored 16.355641148 with four assignments. The source-pair replay confirms the containing cell was generated but omitted after ranking at the 256-witness evaluation cutoff.

## Known-association residuals

The following residuals were computed after search from the frozen truth observer, the truth target locations, and the measured bearings. Truth was EPSG:32619 and was transformed only for evaluation. Each measurement had sigma 0.5 degree.

| Observation | Truth range (m) | True bearing (degree) | Measured bearing (degree) | Residual (degree) | Residual (sigma) |
|---:|---:|---:|---:|---:|---:|
| 0 | 2,238.521 | 196.6741 | 196.4697 | -0.2044 | -0.409 |
| 1 | 1,204.297 | 200.2624 | 200.3648 | 0.1024 | 0.205 |
| 2 | 5,103.708 | 206.3061 | 206.2256 | -0.0805 | -0.161 |
| 3 | 1,690.112 | 215.9641 | 215.6579 | -0.3062 | -0.612 |
| 4 | 7,893.905 | 195.0259 | 194.0280 | -0.9979 | -1.996 |

Four observations fall within +/- 0.5 degree. The fifth is about 2 sigma from the known association. All five remain within +/- 1.5 degrees, which supports a separately predeclared tolerance comparison; this report does not initiate that rerun.

## Timing

The external cold one-shot wall time was 706.479 s. The telemetry request boundary, which includes result serialization, measured 705.436 s. Static session startup was 12.993 s. Proposal generation took 350.458 s, proposal ordering 10.210 s, exact scoring 53.843 s, and continuous refinement 270.677 s. The complete candidate-search stage took 419.865 s. The estimate's `elapsed_s` was 321.964 s and excludes proposal generation and ordering. Estimate serialization took 0.0073 s. The process did not time out. The timing window followed the exclusive lock and paused preparation; the output remains a single-host diagnostic.

## Separate solver audit

After the frozen run ended, a separate coordinate-only audit replayed the 209 failed samples using an independent inverse-geodesic check. All 209 converged when the internal iteration target was set to half the public tolerance, while the public tolerance remained 1e-7 degree. The maximum revised inverse residual was 1.5325e-10 degree and the maximum range error was 2.0462e-9 m. The failure mechanism was that the former loop stopped at the full public tolerance and an independent inverse validation could then exceed it slightly. This later audit does not alter the frozen run's source, estimate, telemetry, or failed status. Its correction is commit `781caf8f993f4b2863e34ad1fee8700ee8062854`, documented in `reports/a04-solver-correction.md`.

## Artifacts and checks

The raw 11 MB telemetry is retained in the ignored server output directory and as deterministic gzip in the tracked evidence directory. Decompressing the tracked telemetry reproduces raw SHA-256 `1bb8f0ac09ebc6f8f6ad884a675b5e246133fb9463ba0b6f5543c7955cf9a77d`. The CRS error and bearing-residual evaluator output is kept under `evaluator-only/evaluator-diagnostic.json`; the fixed-point and source-pair cell audit is kept under `evaluator-only/wedge-omission-audit.json`. The run manifest and all artifact hashes are recorded in `evidence-manifest.json`. The solver audit's baseline failed sample coordinates are stored separately in `post-search-audit/audit-baseline-failures.json`.

The search result is failed and cannot support an accuracy claim. The fixed-point audits show that the known truth scores well and its containing cell has five votes, while residual ordering excluded it from the exact-scored subset. The 209 numerical failures independently force the search status to failed. No calibration or held-out evaluation was run.

Validation of the new runner and evaluator helper passed Ruff, `ty`, and `git diff --check`. The search was launched exactly once from the frozen source commit under the 1,800-second process-group timeout. The earlier wedge-complete run and the other 75 unrun pilot calls were left untouched.
