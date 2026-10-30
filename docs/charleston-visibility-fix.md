# Charleston simulator land-blocking correction

The simulator omitted real land after map clipping produced a mixed GeometryCollection. Its VectorMap constructor used a polygon iterator that handled Polygon and MultiPolygon but skipped GeometryCollection. The known-land spatial index consequently had no polygon candidates, so line-of-sight queries returned clear through land. GPU preparation already used recursive polygon extraction and retained the actual land. The GPU blocked verdict was correct for the investigated rays.

The [source-map provenance check](verification/charleston-collection-provenance.json) found 25,876 polygons and 13 lines after coverage clipping. A 9.475-meter isolated line survives unknown subtraction, and a positive-area Charleston polygon interior also survives. The effective land therefore remains a mixed collection. One saved simulator detection crosses 1,634.017 meters of strict land interior between the observer and its chart aid. This is a substantial obstruction, not a shoreline tolerance effect.

The constructor now recursively indexes the polygon members and prepares that same polygon subset for point classification. Preparing only polygons also prevents isolated clipping lines or points from being classified as land interiors. The original land_geometry object, coverage and unknown geometry remain unchanged. The GPU kernels, prepared arrays and saved archive are unchanged. The [independent tiny-case audit](verification/resident-collection-index-audit.json) confirms bitwise-identical packed arrays across four geometry scenarios.

The cache identity retains compatibility for exactly this reviewed source correction. Its resolved VectorMap source path and full source SHA must match the pinned corrected revision before its preparation fingerprint is mapped to the prior revision. An unrelated file or any unlisted future source digest receives no compatibility mapping and invalidates normally. This preserves the existing cache without changing its key, header or archive and without weakening general invalidation. Four focused identity tests verify that restriction, and two existing cache tests still pass.

Nine regression cases failed before the correction and pass afterward. The combined CPU geometry and simulator suite passes 47 tests, covering indexed and unindexed rays, scalar and batched queries, land interiors, holes, shoreline contact, unknown coverage, and line/point clipping remnants. The 28 focused GPU pruning, prediction, assignment and integration checks also pass. Independent agents reviewed the actual fix and cache compatibility mapping.

Historical measurements generated with the affected constructor remain unchanged. They contain detections through land and do not become valid accuracy inputs merely because the constructor has been corrected. The original long search keeps running with those original measurements. Fresh corrected measurements must be stored separately and used for subsequent accuracy evaluation.

The GPU still conservatively reports some shoreline or coverage-boundary coincidences as unresolved where the CPU reference permits clear shoreline contact. Unknown-observer precedence also differs. Those policies are distinct from this known-water Charleston omission; no GPU tolerance was relaxed to conceal the error. The bounded check below records all disagreements rather than treating unresolved as clear.

The [bounded Charleston check](verification/charleston-visibility-fixed-small.json) compares all 425 catalog targets within 10 kilometers of the true observer. The source map layers are cropped with a positive one-meter margin around all evaluated rays before local reference overlays. The original nominal catalog records reproduce the cached target positions exactly, with identical types and extents. Corrected indexed CPU, unindexed CPU and unchanged GPU queries agree on every ray. They classify 258 rays as blocked and 167 as clear, with zero mismatches or unresolved rays in this sample.

The actual simulator regenerates a separate seed-11 frame using the original sensor settings. It produces 70 total reports, including 46 positive chart-aid reports. At the true Charleston point the GPU finds distinct matches for all 46 at the unchanged plus/minus 0.5-degree gate. Candidate scoring gives the following results.

| Evaluated offset east, north | Positive chart aids matched | Scoring status |
| --- | --- | --- |
| 0 m, 0 m | 46 of 46 | Unresolved, flag 8 |
| 5 m, 0 m | 45 of 46 | Unresolved, flag 8 |
| 0 m, 5 m | 44 of 46 | Unresolved, flag 8 |
| 20 m, 20 m | 29 of 46 | Unresolved, flag 8 |

The observations are [saved separately](verification/charleston-corrected-local-measurements-seed11.json). The original observations, current search, baseline GPU sources and prepared-world archive remain unchanged. The archive's complete SHA-256 digest, size and nanosecond modification time match the before-fix snapshot; the newly computed preparation key also matches the saved entry, which loads with reconstruction forbidden. The original search's evaluated-region counter advanced during the check.

Complete positive matching at the true point confirms that the land-blocking mismatch no longer removes required detections in this sample. Numerical-boundary flag 8 remains active for the predicted scene, so scoring still reports unresolved. The check preserves that flag and does not claim a location estimate or coast-wide localization accuracy. No full search was started or substituted for the live run.
