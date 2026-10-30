# Preloaded GPU results for all approved B locations.

J.C. Vaught.

All 20 approved sites returned completed estimates after full Eastern Seaboard searches on GPUs 0 through 2. Median search duration was 1.304326 s and median WGS84 position error was 1.060387 m. Nineteen estimates were within 4 m. Savannah remained the accuracy outlier at 23.455641 m.

The first site and every subsequent site reused GPU maps loaded before the timed searches. One worker process and the same three device-owner threads served the entire suite. Initial GPU loading took 2.304693 s without seeding, pruning, or scoring any cells. Shared host loading of the saved inputs and simulator scene took 51.209571 s once. Both preparation durations are excluded from the per-site search times. Final selection took 19.597126 s in total across the 20 sites and is also excluded.

| Site | Location | Detected aids | Search time (s) | Error (m) |
|---|---|---:|---:|---:|
| B2 | Portland ME | 62 | 0.974 | 2.246 |
| B4 | Boston | 74 | 0.885 | 0.267 |
| B5 | Providence | 32 | 24.363 | 3.565 |
| B6 | New Haven | 36 | 4.624 | 0.933 |
| B7 | Bridgeport CT | 24 | 16.868 | 2.338 |
| B8 | Port Jefferson NY | 26 | 1.338 | 1.842 |
| B9 | New York and New Jersey | 68 | 1.111 | 0.863 |
| B11 | Philadelphia | 18 | 264.244 | 2.061 |
| B12 | Wilmington DE | 32 | 7.496 | 1.368 |
| B13 | Baltimore | 47 | 1.212 | 1.188 |
| B14 | Virginia Hampton Roads | 97 | 0.774 | 1.277 |
| B15 | Morehead City | 65 | 0.903 | 0.793 |
| B16 | Wilmington NC | 24 | 81.533 | 0.742 |
| B17 | Charleston | 67 | 0.833 | 0.856 |
| B18 | Savannah | 21 | 5.787 | 23.456 |
| B19 | Brunswick GA | 26 | 1.270 | 0.396 |
| B20 | Jacksonville | 21 | 4.490 | 1.264 |
| B21 | Port Canaveral | 57 | 0.889 | 0.217 |
| B22 | Palm Beach | 33 | 5.978 | 0.408 |
| B24 | PortMiami | 57 | 0.946 | 0.614 |

B1, B3, B10, and B23 remain excluded by the previously approved aid-count filter. Their observations were not regenerated for this run. The selected scope therefore contains 20 sites rather than all 24. The raw runner field all24_recorded is false for this selected scope; all 20 selected sites are present and complete.

Each search completed all 39,435 roots at 1 m terminal resolution, using 128 roots per task and the existing 512-cell processing batch. Seed 11, the corrected 20 cm catalog, bounded bearing noise, land visibility, warning rules, and workspace ceilings were retained. Inputs were generated once per site. No vessel clutter entered the marker-only search. Marker counts differ from earlier-catalog runs because the corrected catalog and its keyed simulator streams apply consistently to all sites here.

The saved-output audit checks each estimate freeze, uniqueness and completeness of the selected aid matches, zero selected warning flags, WGS84 errors computed after selection, complete coast bounds, per-device reuse, worker/thread identities, and the saved cache hashes. The selected estimates were frozen before observer truth was read for accuracy. The worker received bearings, bounds, and output paths rather than observer coordinates. The cache hashes, sizes, and modification times remained unchanged. Worker cleanup left no live descendants.

The source revision was b5caf1e. The full raw run remains at runs/b-location-suite-preloaded-20261030T005811Z on the server. This folder preserves summary data, observations, diagnostics, estimates, freeze hashes, profiles, cache fingerprints, and the posthoc audit. Raw terminal arrays remain in the original server run.
