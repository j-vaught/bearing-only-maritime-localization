# A35 frozen objective diagnosis

## Scope and inputs

This evaluator-only diagnosis used the frozen development trial `development/port-001/placement-001/r0/range10000m/typesoff/nominal`. The primary scorer evaluated the evaluator truth position and saved post-search candidates after the wedge search had written its estimate. Truth was not passed to candidate generation or search, and no search configuration, objective, acceptance threshold, or input was changed.

The frozen measurements, map, catalog, scope manifest, and estimate hashes were verified by the diagnostic. Their hashes are recorded in the JSON artifact. The estimate SHA-256 is `dec727542052eb0e1b8461bc1a8fb9fe99e3bd35fea617dd24841c4e9459f738`. The scorer read the exact searched MapV2 and CatalogV2 and the frozen `bearing_set_map_v1` configuration.

## Coordinate and map audit

The truth metadata declares EPSG:32619, while the search domain and map use EPSG:32618. The raw truth coordinates `(508789.7116, 4922460.3136)` therefore cannot be queried directly against the search map. Transforming the truth to EPSG:32618 gives `(986157.3001, 4940645.2713)`. The exact searched map classifies that transformed position as water, and its visibility coverage is known. This resolves the apparent invalid-water result caused by comparing coordinates from different UTM zones.

## Objective at truth

At the transformed truth, the exact scorer predicts five visible groups and assigns all five reports. The five predicted group member-key sets match the simulator detected group member-key sets exactly. The range maximum is 10,000 m inclusive, the circular merge tolerance is 0.1 degrees, and every report retains its own 0.5 degree sigma.

| Position | Matched cost | Baseline and report clutter | Missed-group cost | Total objective | Matched reports |
|---|---:|---:|---:|---:|---:|
| Evaluator truth | 4.206587168 | 1.000000000 | 0.000000000 | 5.206587168 | 5 of 5 |
| Best saved hypothesis | 0.382261952 | 24.544416126 | 0.000000000 | 24.926678078 | 1 of 5 |
| Closest saved hypothesis | 0.585387163 | 24.544416126 | 29.933606209 | 55.063409498 | 1 of 5 |

The truth score is 19.720090910 lower than the best saved candidate score. At truth, there are no predicted misses or unresolved map/model outcomes. Candidate scoring at the best result reproduces its saved score exactly. The closest saved point predicts 14 groups, matches one report, and incurs the miss cost for 13 groups.

## Saved hypotheses and diagnosis

The completed proposal-generation phase examined all 333,340 configured source pairs and 1,530,303 cells. Candidate scoring stopped at its 256-candidate limit, with 252 scored candidates and four unresolved-coverage outcomes. The result is budget-exhausted and ambiguous, with no acceptance or global certificate.

The estimate contains 260 saved hypotheses. Their geodesic errors range from 1,897.057 m to 2,508,163.522 m. None is within 100 m of truth. Grouping saved positions into connected spatial modes with a 100 m geodesic radius yields 253 modes. The highest-ranked mode is 1,898,447.237 m from truth and explains only report 4 using `source:70`. The best-error mode is the rank-214 candidate, 1,897.057 m from truth, which matches report 1 to `enc-physical:d3b987597ebe5005aa3d` and is heavily penalized for missed predicted groups.

Across the saved hypotheses, match counts are 0 for 189 rows, 1 for 50, 2 for 8, 3 for 11, and 4 for 2; none matches all five reports. The exact truth location has the expected group count, identities, bearing residuals, and primary-objective ranking. These results indicate a missed proposal basin in the bounded candidate set, not a mismatch in the primary scoring model at the true position.


## Correction to the earlier A43 distance diagnostic

The older A43 result has the same measurement, map, and catalog hashes as this frozen case. Its saved truth JSON has the same trial ID and observer coordinates as the current truth; the current truth CRS record identifies those coordinates as EPSG:32619. A43 telemetry declares its estimate coordinates in EPSG:32618. Recomputing the geodesic distances with those explicit coordinate systems changes the earlier reported top error from 391,446.878 m to 509,653.742 m. The earlier minimum over seven saved positions changes from 391,368.947 m to 506,055.789 m. The original A43 artifacts remain intact; both the prior values and corrected errors are preserved under prior_a43_post_search_diagnostic in the machine-readable output.

## Artifacts and checks

The machine-readable evaluator output is `.agent-work/outputs/a35-frozen-objective/diagnostic.json`, SHA-256 `c8b2c35f1056e634d6f4e2ff675309e4a537b6aad481e1ae5bc892e150ccca37`. It retains all 260 candidate errors, score decompositions, assignments, and the 253 spatial modes. The diagnostic implementation is `tools/diagnose_a35_frozen_objective.py`.

Focused tests verify transformation between UTM zones and 100 m geodesic mode clustering. Ruff, `ty`, the two diagnostic tests, and `git diff --check` passed. This is one frozen development case and does not establish a release-level accuracy claim.
