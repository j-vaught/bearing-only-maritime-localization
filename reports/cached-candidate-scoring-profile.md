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

The timed run held the canonical `.agent-work/benchmark.lock` for all four cases and used a 600 s process-group watchdog. The host was busy during the run. The lock-time snapshot recorded a load average of 14.55, several unrelated CPU workers, and two active GPU processes. These results are therefore shared-host diagnostic measurements. They do not support a clean-host speed claim or a 100 ms claim.

Linear extrapolation from this cache-order sample gives approximately 45.94 h for exact one-worker scoring, 40.48 h for exact four-worker scoring, 195.46 h for screened one-worker scoring, and 82.76 h for screened four-worker scoring over all 1,457,546 cached cells. These estimates assume the sampled mix represents the full cache and are not a finite full-domain runtime certificate. The observed four-worker speedups were 1.13x for exact mode and 2.36x for screened mode, so worker scaling is not linear. Each worker used about 1.0 GiB RSS. A four-worker run used about 4.0 GiB aggregate worker RSS in addition to the parent process. Four workers are the measured starting point for a full-domain attempt, with a practical cap of eight until a quieter-host measurement establishes the memory margin. Any choice up to 16 workers must be checked against the actual aggregate RSS.

The two tracked raw files occupy 521,715 bytes. Scaling the detailed profile JSON by the sampled candidate count gives an estimated 2.84 GB, or 2.64 GiB, for a full-domain JSON with the same audit structure. This is a storage estimate for the profile representation and excludes any richer full-search checkpoint or hypothesis artifacts.

The profile source harness was committed and pushed at `60447c8`; the follow-up parity assertions were committed and pushed at `e516186`. The raw result file is [profile-results.json](profile-results.json), and the pre-measurement selection record is [selected-candidates.json](selected-candidates.json).
