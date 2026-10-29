# Saved-output audit of seven failed sites

J.C. Vaught. October 29, 2026.

All seven sites contain saved terminal centers that match every positive aid report. Every fully matched center carries numerical-boundary flag 8 and remains unresolved. Consequently, a common certification veto explains the absence of estimates more directly than inadequate aid count or inability to form a complete nominal assignment. No search, simulation, scoring, cache construction, or source change was performed for this audit.

The evidence is from remote repository `/home/jvaught2/projects/bearing-only-maritime-localization` at revision `a6fc937`. The final index is `runs/b-location-suite-aids16-final-20261029/results.csv`. Its `attempt_directory` field selects the authoritative saved attempt, including the retry attempts for B20–B22. Counts below come from the indexed `estimate.json` and aggregation of existing `search/**/leaf-*.npz` arrays.

| Site | Positive reports and maximum nominal matches | Terminal centers | Inconsistent | Unresolved | Fully matched | Fully matched flags |
|---|---:|---:|---:|---:|---:|---|
| B6 New Haven | 34 | 10,169 | 1,722 | 8,447 | 90 | All 8 |
| B7 Bridgeport | 24 | 81,902 | 8,578 | 73,323 | 55 | All 8 |
| B18 Savannah | 20 | 117,186 | 14,461 | 102,725 | 17,262 | All 8 |
| B19 Brunswick | 28 | 64 | 22 | 42 | 19 | All 8 |
| B20 Jacksonville | 23 | 310 | 71 | 239 | 36 | All 8 |
| B21 Port Canaveral | 53 | 60 | 24 | 36 | 25 | All 8 |
| B22 Palm Beach | 33 | 2,665 | 435 | 2,230 | 31 | All 8 |

B7 also has one land center. Every nonland terminal at B6, B7, B18, B19, B21, and B22 carries flag 8. B20 has 89 flag-8 terminals and 221 flag-0 terminals. Its 175 unresolved flag-0 terminals are not fully matched. No audited terminal carries uncertain-geometry, capacity, or numerical-failure flags 1, 2, or 4.

The separate physical rejection result is zero at every fully matched center. Zero means that the independent conservative physical graph did not prove impossibility. It is not a positive location certificate. The complete rejection-reason distributions are given below. Reason 2 is a positive report without a possible object, and reason 4 is Hall deficiency.

| Site | Retained, reason 0 | Missing-object rejection, reason 2 | Hall rejection, reason 4 |
|---|---:|---:|---:|
| B6 | 8,447 | 1,550 | 172 |
| B7 | 73,324 | 7,253 | 1,325 |
| B18 | 102,725 | 14,027 | 434 |
| B19 | 42 | 15 | 7 |
| B20 | 239 | 71 | 0 |
| B21 | 36 | 24 | 0 |
| B22 | 2,230 | 424 | 11 |

Because all fully matched centers carry flag 8, the source's global numerical-boundary veto is a proven necessary blocker. The active path is `docs/verification/b-location-suite/run_suite.py`, function `search_child`, through `ProfiledResidentDevice`, `OptimizedResidentDevice`, and `predict_groups_parallel`. In `maritime/search/gpu/resident/engine.py` lines 311–314, positive certification requires selected residuals strictly inside each physical tolerance, exact matched groups, known observer water, and `flags == 0`. The selector additionally requires complete distinct record assignments, status 0, and flag 0 in `tools/select_resident_result.py` lines 50–55. Thus no estimate is an intentional outcome of the implemented certification contract.

The likely common trigger is nearly coincident chart records surviving exact-coordinate canonicalization. `maritime/catalog_identity.py` lines 149–157 groups records by exact floating-point latitude and longitude, while lines 59–62 preserve the exact binary coordinate in each coordinate-derived key. The saved simulator groups contain distinct coordinate-derived keys separated by less than $10^{-9}$ degrees. The numbers of such pairs are 11, 16, 19, 2, 16, 6, and 1 for B6, B7, B18, B19, B20, B21, and B22, respectively. These are counts within already saved simulator groups, not newly computed visibility or associations.

For example, B19 contains the detected grouped keys `chart-coordinate:0x1.f1822390fec00p+4:-0x1.45c42c145b000p+6` and `chart-coordinate:0x1.f1822390fec00p+4:-0x1.45c42c145b00cp+6`. Their decoded positions are approximately `(31.094272199999978, -81.44157439999981)` and `(31.094272199999978, -81.44157439999998)`. B22 contains a detected pair at approximately `(26.772638099999313, -80.04193280000072)` and `(26.772638099999995, -80.04193279999998)`. Exact-coordinate identity leaves these separate despite their negligible geometric separation.

That representation interacts directly with `maritime/search/gpu/resident/parallel_prediction.cu` line 124. Every group member within twice the range numerical margin of the nearest representative increments a tie counter. The configured margin is 0.001 m. `parallel_prediction.py` lines 248–249 then sets global flag 8 if any raw group has more than one such member. This occurs before representative-type filtering. Consequently, an unrelated tied group can veto an otherwise complete positive assignment. The baseline kernel has the analogous global tie warning at `kernels.cu` line 242. Shared simulator and search canonicalization therefore does not eliminate this failure mode.

Saved flags merge several causes into bit 8. Range boundaries, angular grouping boundaries, representative distance ties, and report-to-group gating boundaries can all set it. The leaves do not retain the triggering object/group, individual selected residuals, or selected exactness flags. Therefore, the saved record proves the global flag veto but does not prove which setter fired at every center or that clearing only this bit would produce a valid certificate. The nearly coincident pairs make representative distance ties a strong common-cause inference. Selected group extent and representative disagreement remain additional possibilities because complete nominal matching can use a group's extent while positive certification uses the representative's physical bearing tolerance.

Posthoc comparison with saved truth confirms that all seven searches retained a fully matched, unresolved, flag-8 center within 0.40 m in projected coordinates of the observer. This rules out loss of the observer's neighborhood as the explanation for these seven failures. It does not change the frozen estimate.

| Site | Nearest saved center distance, projected m | Best saved complete diagnostic error, geodesic m |
|---|---:|---:|
| B6 | 0.210 | 0.210 |
| B7 | 0.187 | 1.273 |
| B18 | 0.108 | 18.705 |
| B19 | 0.397 | 0.913 |
| B20 | 0.174 | 0.551 |
| B21 | 0.218 | 0.217 |
| B22 | 0.393 | 0.988 |

The nearest centers all have status 2, flag 8, full nominal cardinality, and physical rejection reason 0. Their evidence locations below are relative to the indexed attempt directory and use zero-based row indices.

| Site | Saved leaf | Row |
|---|---|---:|
| B6 | `search/roots-21456-21464-gpu-0/leaf-00001844.npz` | 74 |
| B7 | `search/roots-20976-20984-gpu-1/leaf-00003519.npz` | 94 |
| B18 | `search/roots-3904-3912-gpu-1/leaf-00000829.npz` | 342 |
| B19 | `search/roots-2464-2472-gpu-1/leaf-00000186.npz` | 25 |
| B20 | `search/roots-2456-2464-gpu-1/leaf-00000188.npz` | 40 |
| B21 | `search/roots-3864-3872-gpu-1/leaf-00000310.npz` | 46 |
| B22 | `search/roots-5040-5048-gpu-0/leaf-00000476.npz` | 13 |

These seven failures share the certification-warning pattern. Aid count, Gaussian-tail noise, capacity exhaustion, incomplete coast coverage traversal, and absent complete nominal assignments do not explain it. A correction requires inspecting how numerical warnings are scoped to selected physical evidence and how source coordinate precision relates to identity. Whether such a correction produces certified results remains unmeasured because no new scoring was authorized.
