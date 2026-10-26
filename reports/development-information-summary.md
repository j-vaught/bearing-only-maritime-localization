# Development Scene Information Summary

This report summarizes the frozen nominal development population using persisted evaluator difficulty and support artifacts. All 3,600 nominal rows remain in the all-row counts, including unsupported scenes, low match counts, and singular geometry. Supported rows are shown separately according to the frozen support manifest. Radius and known-association match bands describe scene geometry. They do not predict localization accuracy, set an acceptance threshold, or establish an oracle error bound by themselves.

The frozen population contains 3,600 nominal rows, with 2,310 supported and 1,290 unsupported. All 3600 support files and all 3600 difficulty files matched their frozen hashes. There are 0 missing or invalid difficulty artifacts, 0 missing geometry strata, and 0 missing matched-group counts. The 544 null ellipse radii correspond to singular geometry, not missing difficulty files.

Matched-group bands are reporting bins only. The persisted known-association matched_group_count field is reported separately from the support manifest count. The bins do not alter support eligibility.

| Range | Type reports | Nominal rows | Supported | Unsupported | All-row geometry strata ≤25 / 25–100 / >100 / singular | Supported geometry strata ≤25 / 25–100 / >100 / singular | Match bands all rows; supported (0 / 1–2 / 3–9 / 10–24 / ≥25) | Missing difficulty | Null radius |
|---:|:---:|---:|---:|---:|:---|:---|:---|---:|---:|
| 1 km | On | 600 | 269 | 331 | 368 / 41 / 13 / 178 | 257 / 12 / 0 / 0 | 108 / 120 / 286 / 84 / 2; 0 / 0 / 195 / 72 / 2 | 0 | 178 |
| 1 km | Off | 600 | 269 | 331 | 368 / 41 / 13 / 178 | 257 / 12 / 0 / 0 | 108 / 120 / 286 / 84 / 2; 0 / 0 / 195 / 72 / 2 | 0 | 178 |
| 5 km | On | 600 | 438 | 162 | 444 / 69 / 39 / 48 | 382 / 47 / 9 / 0 | 30 / 51 / 92 / 200 / 227; 0 / 0 / 74 / 137 / 227 | 0 | 48 |
| 5 km | Off | 600 | 438 | 162 | 444 / 69 / 39 / 48 | 382 / 47 / 9 / 0 | 30 / 51 / 92 / 200 / 227; 0 / 0 / 74 / 137 / 227 | 0 | 48 |
| 10 km | On | 600 | 448 | 152 | 444 / 68 / 42 / 46 | 390 / 47 / 11 / 0 | 30 / 47 / 65 / 186 / 272; 0 / 0 / 48 / 130 / 270 | 0 | 46 |
| 10 km | Off | 600 | 448 | 152 | 444 / 68 / 42 / 46 | 390 / 47 / 11 / 0 | 30 / 47 / 65 / 186 / 272; 0 / 0 / 48 / 130 / 270 | 0 | 46 |

Across all rows, the 95% major-radius strata are 25-100m: 356, <=25m: 2512, >100m: 188, singular: 544. Among supported rows they are 25-100m: 212, <=25m: 2058, >100m: 40, singular: 0.

The match-count bins are 0, 1–2, 3–9, 10–24, and ≥25. They are descriptive groupings. Every nominal row appears in the all-row counts; no geometry filter was applied.

Source provenance is recorded in the companion JSON. The frozen eligibility manifest SHA-256 is 2330090802952093bab1d5ec6cb97c6298cce27ee2050804f6eab91d66e8d050, and the preparation ledger SHA-256 is f84072c6cd77af4dfebece37632d80a6675880be0ab8607636be37f27cab1928. The corrected plan is /home/jvaught2/projects/bearing-only-maritime-localization/benchmarks/accuracy/plans/anchor-corrected-2026-10-26; its config, split, and trial-table hashes match the manifest. No search output, heldout input, or validation outcome was read.

J.C. Vaught
