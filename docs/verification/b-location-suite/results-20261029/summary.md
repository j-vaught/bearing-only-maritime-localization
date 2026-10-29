# B-location evaluation results

J.C. Vaught.

The strict minimum of 16 detected positive aid reports admitted 20 full-coast search attempts. Eleven produced a frozen consistent estimate, eight completed without a consistent estimate, and Philadelphia reached the 600-second timeout. Searsport, Portsmouth, South Jersey, and Port Everglades were skipped because their aid counts were 3, 14, 8, and 15. All 24 authoritative coordinates were retained.

The 11 frozen estimates have a mean WGS84 geodesic error of 1.677736 m, a median of 1.753628 m, and a maximum of 3.565432 m. Search timing excludes simulation, shared preparation, saved-output selection, and accuracy evaluation. Original and retry shared preparation took 48.103301 s and 48.612712 s, respectively. Every search used GPUs 0 through 2 and the complete saved Eastern Seaboard rectangle. GPU 3 remained outside the suite.

An idle-device utilization sample initially blocked 13 sites before simulation. Those sites were retried with a guard that checks actual compute contexts and memory occupancy. The final table replaces only these unsearched cases, and both source manifests and original attempts are preserved. The saved-output audit verified the strict aid filter, exact coordinates, full-coast settings, frozen estimates, geodesic errors, process cleanup, and unchanged cache fingerprints. The earlier user-stopped B1 attempt is preserved separately.

| Site | Port | Aids | Search s | Error m | Result |
|---|---|---|---|---|---|
| B1 | Searsport ME | 3 | — | — | Skipped |
| B2 | Portland ME | 67 | 8.192 | — | No consistent estimate |
| B3 | Portsmouth NH | 14 | — | — | Skipped |
| B4 | Boston | 79 | 8.175 | 0.875 | Frozen estimate |
| B5 | Providence | 33 | 14.530 | 3.565 | Frozen estimate |
| B6 | New Haven | 34 | 16.580 | — | No consistent estimate |
| B7 | Bridgeport CT | 24 | 60.398 | — | No consistent estimate |
| B8 | Port Jefferson NY | 25 | 8.878 | 1.842 | Frozen estimate |
| B9 | New York and New Jersey | 66 | 8.154 | 0.942 | Frozen estimate |
| B10 | South Jersey | 8 | — | — | Skipped |
| B11 | Philadelphia | 17 | 597.414 | — | 600 s timeout |
| B12 | Wilmington DE | 30 | 11.435 | 1.332 | Frozen estimate |
| B13 | Baltimore | 49 | 8.616 | 1.942 | Frozen estimate |
| B14 | Virginia Hampton Roads | 100 | 7.814 | 2.076 | Frozen estimate |
| B15 | Morehead City | 70 | 7.941 | 1.324 | Frozen estimate |
| B16 | Wilmington NC | 20 | 371.496 | 0.742 | Frozen estimate |
| B17 | Charleston | 64 | 7.819 | 2.060 | Frozen estimate |
| B18 | Savannah | 20 | 88.951 | — | No consistent estimate |
| B19 | Brunswick GA | 28 | 9.139 | — | No consistent estimate |
| B20 | Jacksonville | 23 | 9.027 | — | No consistent estimate |
| B21 | Port Canaveral | 53 | 8.396 | — | No consistent estimate |
| B22 | Palm Beach | 33 | 8.793 | — | No consistent estimate |
| B23 | Port Everglades | 15 | — | — | Skipped |
| B24 | PortMiami | 58 | 8.507 | 1.754 | Frozen estimate |

The Philadelphia search time is the final completed batch timestamp. The watchdog stopped its process group at the configured 600-second ceiling. All partial data remain available in the linked attempt directory.
