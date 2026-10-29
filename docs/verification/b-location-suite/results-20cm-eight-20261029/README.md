# Eight-site rerun with 20 cm chart-aid grouping.

J.C. Vaught.

The eight previously uncertified sites were searched sequentially over the full Eastern Seaboard domain on GPUs 0–2. All eight now have certified selected centers with zero warning flags and complete positive-aid assignments. The warning, certification, pruning, and selection sources remain unchanged. The two production changes relative to the previous suite are shared chart-coordinate identity and its cached object-data conversion.

The saved base map cache was reused without rebuilding or modification. A separate versioned object-only sidecar was created for the corrected catalog. The previous sidecar remains unchanged, and both new cache fingerprints were stable through the suite. Shared preparation took 64.916 s and is excluded from individual search times. It includes input loading, object-sidecar creation, and simulator geometry loading. Device uploads are included in each search time.

Observations were regenerated using the corrected catalog, the same simulator settings, and the same seed. Catalog identity changes alter keyed detection and noise draws, so these are corrected-model reruns rather than identical-observation timing ablations. Truth was used only in simulation and after estimate freezing for independent WGS84 distance evaluation.

| Site | Detected aids | Search time (s) | Error (m) |
|---|---:|---:|---:|
| B2 — Portland ME | 62 | 8.096 | 2.246 |
| B6 — New Haven | 36 | 8.379 | 0.933 |
| B7 — Bridgeport CT | 24 | 21.007 | 2.338 |
| B18 — Savannah | 21 | 12.380 | 23.456 |
| B19 — Brunswick GA | 26 | 8.848 | 0.396 |
| B20 — Jacksonville | 21 | 9.699 | 1.264 |
| B21 — Port Canaveral | 57 | 8.143 | 0.217 |
| B22 — Palm Beach | 33 | 8.855 | 0.408 |

Savannah remains the accuracy outlier at 23.456 m despite a valid bounded-bearing certificate. The other seven errors range from 0.217 to 2.338 m.

The two audit files verify saved estimate hashes, independently recomputed errors, complete traversal of all 39,435 roots per site, use of all three selected devices, unchanged warning/selection sources and scientific settings, and cache preservation. No additional search was run for auditing. Compact site inputs, diagnostics, frozen estimates and summaries are retained here. Full terminal archives remain in the HDD run directory recorded by the manifest.
