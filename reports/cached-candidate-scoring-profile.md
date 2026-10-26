**Cached candidate scoring profile**

The profile used the frozen proposal cache at 1,457,546 cells. The cache tree SHA-256 is `d6154c18349842d22ca1cd5eaf2d022a6ac67004eebc9e35c5c59d03ad767e72` and the strict proposal context SHA-256 is `3e450d04fd82d4bec5c5786d9e49960d51a0dab4615e7237b55bc493251aea44`. The static measurements, catalog, map, and scope manifest matched their declared hashes. The scoring configuration was the frozen `wedge_geodesic` configuration with the `bearing_set_map_v1` objective, 10,000 m maximum range, type matching disabled, and candidate budget 1. No truth or held-out data was loaded.

The workload was cache-order sampling. It selected the first 128 cache indices and 128 evenly spaced indices over the complete cache, then deduplicated IDs stably. Index 0 occurs in both sequences, so the measured set contains 255 unique candidates. Selected IDs and transformed EPSG:32618 positions were written before any scoring began. AEQD cell positions were converted through the declared local frame to geographic coordinates and then to EPSG:32618 map coordinates.

| Workers | Mode | Wall time (s) | Candidates/s | Exact evaluations | Exact scored | Pruned | Peak worker RSS (MiB) | Sum worker RSS (GiB) | Best score | Best tie IDs |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | exact | 28.934 | 8.813 | 255 | 131 | 0 | 1,025.6 | 1.00 | 24.468390694319403 | `cell:74:2578` |
| 1 | screened | 123.106 | 2.071 | 173 | 49 | 82 | 1,022.6 | 1.00 | 24.468390694319403 | `cell:74:2578` |
| 4 | exact | 25.496 | 10.002 | 255 | 131 | 0 | 1,023.1 | 3.98 | 24.468390694319403 | `cell:74:2578` |
| 4 | screened | 52.126 | 4.892 | 180 | 56 | 75 | 1,023.8 | 3.99 | 24.468390694319403 | `cell:74:2578` |

Exact mode evaluated all 255 candidates in both worker configurations with identical actions and scores. Screened mode retained the same best objective and tie ID as exact mode for both worker configurations. Cross-worker exact and screened comparisons also agreed within the required absolute tolerance of $10^{-8}$ and relative tolerance of $10^{-10}$. Every pruned candidate carried a supported lower bound. The one-worker screened case had 82 prunes and the four-worker screened case had 75 prunes. The raw audits retain every action, score, lower bound, bound support flag, model status, visible-group count, and candidate position.

The timed run held the canonical `.agent-work/benchmark.lock` for all four cases and used a 600 s process-group watchdog. The host was busy during the run. The lock-time snapshot recorded a load average of 14.55 and two active GPU processes. These results are shared-host diagnostic measurements. They do not support a clean-host speed claim or a 100 ms claim.

The earlier whole-domain estimate incorrectly multiplied pool startup by the number of candidate groups. The corrected startup-once formula is $T_mathrm{full} = T_mathrm{startup} + (1{,}457{,}546 / 255) T_mathrm{request}$. It gives 30.97 h for exact one-worker scoring, 9.13 h for exact four-worker scoring, 180.40 h for screened one-worker scoring, and 51.35 h for screened four-worker scoring. These values use the measured steady request stage and are diagnostic estimates from the fixed cache-order sample.

The fixed sample contains two distinct cache-order strata. For exact mode, the first four chunks cover the dense first-128 prefix and took 16.592 s at one worker and 16.642 s at four workers. The final four chunks cover the 127 evenly spaced tail selections and took 2.098 s at one worker and 2.105 s at four workers. The prefix and tail therefore have different costs. A 50/50 mixture is not a representative coast estimate, so the two strata remain separate in this correction.

**Evenly spaced 2,048-candidate scaling correction**

A second workload was declared before scoring as 2,048 evenly spaced cache indices across the full cache. It used exact mode, chunk size 32, and sequential worker counts of 1, 4, 8, and 16 under the canonical lock. Numerical-library thread settings were fixed before imports with `OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, and `NUMEXPR_NUM_THREADS=1`. The source SHA was `0d34a17` and the timing harness SHA was `eb1b072a97ea7c4f6b5d83a900bd86c900e191cd`.

| Workers | Status | Startup (s) | Steady request (s) | Wall time (s) | Steady candidates/s | Peak worker RSS (MiB) | Sum worker RSS (GiB) | Exact evaluations | Exact scored | Best score |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | completed | 6.613 | 51.227 | 60.222 | 39.979 | 1,021.2 | 1.00 | 2,048 | 992 | 24.1270900878417 |
| 4 | completed | 16.945 | 14.530 | 34.018 | 140.945 | 1,023.2 | 3.89 | 2,048 | 992 | 24.1270900878417 |
| 8 | completed | 28.157 | 8.136 | 38.888 | 251.729 | 1,023.6 | 7.94 | 2,048 | 992 | 24.1270900878417 |
| 16 | timeout_or_failure | worker startup timed out after 53.073 s | — | 53.073 | — | — | — | 0 | 0 |

The completed cases agreed on the best objective and tie ID `cell:-35:2251` within the required tolerances. The previous 255-candidate tie `cell:74:2578` was not selected by the evenly spaced sample, so these sampled best values are not full-domain objectives. The 16-worker case dispatched zero candidates and retained its startup failure disposition. The public result manifest is 2,738,342 bytes and its selection manifest is 200,027 bytes. The two files occupy 2,938,369 bytes.

Using the same startup-once formula with the 2,048-candidate steady request stages gives diagnostic full-domain estimates of 10.13 h at one worker, 2.88 h at four workers, and 1.62 h at eight workers. The 16-worker timeout prevents an estimate. The eight-worker steady request stage was faster than four workers, but its startup was 28.157 s and aggregate worker RSS was 8.14 GiB. The 4-worker case had lower wall time because its startup was 11.212 s shorter. The profile supports exact-mode trials at four and eight workers under an actual memory budget. It does not establish release throughput or a linear scaling law, and it does not justify a 16-worker choice after the observed startup timeout.

The measured original raw files occupy 521,715 bytes. The scaling raw files occupy 2,938,369 bytes. These are actual output volumes for the recorded samples. A full-domain output volume depends on whether every audit is retained and must be measured from the continuation output format.

The complete process and command snapshots used for diagnosis are retained only in ignored server diagnostics. Tracked manifests retain aggregate process counts, CPU and load summaries, and GPU memory totals. The raw result file is [profile-results.json](experiments/cached-candidate-scoring/profile-results.json), and the pre-measurement selection record is [selected-candidates.json](experiments/cached-candidate-scoring/selected-candidates.json). The 2,048-candidate result is [scaling-2048-results.json](experiments/cached-candidate-scaling/scaling-2048-results.json), and its selection record is [scaling-2048-selection.json](experiments/cached-candidate-scaling/scaling-2048-selection.json).
