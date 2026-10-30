# Slow preloaded site diagnosis.

J.C. Vaught.

This analysis reads the completed 20-site run, terminal arrays, observations, cache entries, and sampled profiles. It computes coordinate distances and descriptive landmark geometry after the run. It performs no localization search, visibility query, simulator invocation, assignment solve, cache build, or search-logic change.

The early land-occlusion shortcut has no blockers in the saved coast-wide map. The stored barriers array has shape (0, 4). Preparation automatically inserts a land polygon only when that polygon is exactly an axis-aligned rectangle without holes, unless explicit interior rectangles are supplied. The real coast produced no such entries. The early object-gathering path calls the rectangle blocker helper with zero entries, so this shortcut cannot reject hidden objects. Whole-cell land classification still operates, and detailed polygon sight-line checks still run during final prediction. The gap concerns early object occlusion rather than absence of the detailed land map.

The resulting candidate burden differs sharply across sites. Charleston requires detailed scoring at 171 terminal centers. Philadelphia requires 411,200, Wilmington NC 81,734, Providence 22,764, and Bridgeport 22,857. Posthoc distances show that most of those heavy candidate clouds lie far from the actual observer. In Providence, 22,647 of 22,764 centers lie in two 5 km bins near 28.35 to 28.38 degrees N and 80.64 degrees W. Bridgeport has 22,495 centers near 25.73 degrees N, 80.20 degrees W. Philadelphia has 296,204 centers in its largest 5 km bin near 28.39 degrees N, 80.63 degrees W. These are distant look-alike catalog regions that passed the available pruning. Truth distances are analysis only and are not proposed as search inputs.

Detailed prediction dominates the sampled time on the slowest worker. The combined pp_pairs kernel evaluates geodesic bearings and ranges, then checks polygon line-of-sight for in-range objects. At the four slowest sites it accounts for 92.8 to 95.6 percent of sampled CUDA stream time. Cooperative assignment accounts for approximately 0.25 to 0.38 percent. Heavy regions gather roughly 560 to 716 candidate objects per center in these samples. The profiles combine bearing and sight-line work, so they do not establish which of those two operations dominates separately. Stream timings include host launch gaps, and sampling covers every tenth batch rather than a complete kernel-busy trace.

Refined work also remains attached to the GPU that receives its parent root packet. There is no active descendant queue stealing. Wilmington places 99.46 percent of its terminal centers on GPU 2; GPUs 0 and 1 finish around 1.96 s while GPU 2 continues to 81.53 s. Providence places 99.49 percent on GPU 0, with the other workers finished around 1.29 s. Bridgeport places 98.42 percent on GPU 1, with the other workers finished around 1.46 s. Philadelphia is less concentrated but still has a long tail. GPUs 0 and 2 finish at approximately 30.22 and 55.08 s while GPU 1 continues until 264.24 s.

| Site | Search (s) | Terminal centers | Unresolved centers | Largest worker terminal share |
|---|---:|---:|---:|---:|
| B11 Philadelphia | 264.244 | 411,200 | 372,659 | 72.66% |
| B16 Wilmington NC | 81.533 | 81,734 | 69,045 | 99.46% |
| B5 Providence | 24.363 | 22,764 | 19,847 | 99.49% |
| B7 Bridgeport CT | 16.868 | 22,857 | 20,217 | 98.42% |
| B12 Wilmington DE | 7.496 | 5,968 | 4,546 | 95.22% |
| B22 Palm Beach | 5.978 | 5,018 | 4,510 | 98.82% |
| B18 Savannah | 5.787 | 26,229 | 5,606 | 74.63% |
| B6 New Haven | 4.624 | 3,967 | 2,986 | 94.48% |
| B20 Jacksonville | 4.490 | 1,049 | 632 | 91.13% |
| B17 Charleston | 0.833 | 171 | 95 | 100.00% |

Most unresolved centers in the heavy runs have zero warning flags and lack a complete nominal visible assignment. Philadelphia has 372,350 zero-flag unresolved centers out of 372,659 unresolved centers; Wilmington has 68,998 out of 69,045. Their independent physical rejection graph still permits an explanation because it uses the same early conservative blockers. Clearing numerical warnings would not address this bulk of unresolved work. Saved data does not identify the exact contribution of hidden objects versus nominal angular grouping to every mismatch, so no percentage of the slowdown is assigned solely to the missing blocker shortcut.

Savannah is different. It retains 18,705 consistent centers, with 19,128 terminal centers within 1 km of truth. Its detected aids lie 7.17 to 9.99 km away, so bounded angular noise permits a broad genuine feasible region. Its three GPU workers finish at approximately 5.33, 5.33, and 5.79 s. Better load sharing alone has less room to help this case, and safe pruning must retain genuinely feasible centers.

The first implementation priorities are usable conservative land-occlusion proofs using the existing map, followed by redistribution of active descendant work across idle devices. A map-side auxiliary blocker index can be considered separately from the preserved base cache. A useful proof may reject only when a positive detection has no physically visible injective explanation. Warning behavior and feasible solutions must remain intact. No expected speedup is claimed without a separately approved run.

The complete per-site measurements, warning histograms, terminal distances, largest spatial bins, GPU ownership counts, profile fractions, and sampled candidate-object counts are recorded in diagnosis.json. The script analyze_saved.py reproduces these values from saved server data. Profiles with zero sampled prediction calls must not be interpreted as zero actual prediction work; short unsampled scoring batches exist, including Jacksonville.
