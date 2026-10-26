# Legacy-to-V2 regression comparison

## Matched public V1 replays

I ran the same seeded B1 case through the public V1 pairwise search on detached `origin/main` (`32456174d6f7baea2c25bdfca6490f940b4214bf`) and the integration starting commit (`966f80bb73468fc3bc33a804f81da2d8bf65132c`). Both runs used the original Chesapeake inputs, 23 measurements, the same 799-record candidate catalog, the same `.5°` noise, `.1°` merge resolution, 10 km range, complete rectangular B1–B5 bounds, type labels enabled, CPU backend, and identical pairwise limits. Search received measurements, catalog, bounds, and configuration; truth was consulted only after the estimate was returned.

| Worktree | Estimate (east, north) | Error | AIS / aid matches | Legacy score | Search time |
| --- | ---: | ---: | ---: | ---: | ---: |
| `origin/main` | (-25,149, 132,704) m | 10.335 m | 8 / 13 | 41.3071 | 4.796 s |
| integration | (-25,149, 132,704) m | 10.335 m | 8 / 13 | 41.3071 | 5.636 s |

The result pose, score, and association counts are identical. This matched replay shows no observable V1 accuracy regression on this case. The two elapsed times are single measurements and do not establish a performance regression; the integration run took 0.840 s longer in this execution.

The measurement fingerprint is `ef23c7176df83af21c8edbfa27c6e6aab5a6ac821aae3ceb7511620d456aebcd` and the candidate catalog fingerprint is `06a5f19ff63cc0593908ed3faf848bf373186cd8b7a309a2c44cb21f094cd208`. Input hashes are identical in both roots: AIS `acb3202809fdaa8bdf424e2f6a9420a04e2e63f0c3cf1bd1505803363674a1f8`; chart aids `8695f76a76197a72a1209ebdb63f58ef25f1246a27e7079622badc6eeabdb475`; B1–B5 site file `19766d582314e8263ce7613200b02606a243d5ef5c39b582cbc8ae4534f4f61b`; Chesapeake water mask `319d4f7cbaa6f634bdfc68f6341f2dc498a21c7459869ea93ef21a5401fdf53e`.

The integration and main revisions contain changes to association label handling, per-observation uncertainty use, pairwise proposal implementation, and compatibility dispatch. In this replay, every observation has `.5°` sigma and labels are the known `vessel`/`aid` classes, so those specific changes do not alter the returned pose or objective. The output comparison is evidence of parity for this B1 input, not every legacy case.

## Matched full-coast blind B1 control

I extracted only the B1 iteration from `tools/benchmark_eastern_seaboard_pruning.py` and ran its same public hierarchical-search call once on each worktree. This reproduces the archived full-coast B1 setup without running B2–B5 or the later evaluation-only local-window diagnostic. The full coast scope remains unchanged. Search receives 138 seeded measurements, the 53,684-record AIS/ENC catalog, the full coast bounds, and the full coast 500 m water mask. The B1 truth pose is read only after search returns, to calculate error.

| Worktree | Estimate (east, north) | Error | Reports / catalog | Search time | Status |
| --- | ---: | ---: | ---: | ---: | --- |
| `origin/main` | (-25,087.930, 132,772.667) m | 89.091 m | 138 / 53,684 | 28.269 s | estimate returned |
| integration | (-25,087.930, 132,772.667) m | 89.091 m | 138 / 53,684 | 30.128 s | estimate returned |

The measurement fingerprint is `1503a151fa86b5d625f9beb9e26e36e1218264597ef27e418007c20b7bc4491d`; the seed is `20262023`, and the full coast bounds are `[-508212.930, 893753.774, -1501133.583, 833963.102]` m. All four input hashes matched between worktrees: AIS `8fb341c12f8f4f4351669223bd12442e9ee5c3ec096218aa3a73b1bcc3cf65d1`; chart aids `674c5fe1bffd06dd3ff9001f8eec6ebd06500565de125efb24f48f5b8ca4ffff`; water-mask manifest `fbf9872176ef670e044755e2f43c4822514a85faa56fdab41ba4dd9353b19516`; water-mask raster `e8f99df2f09338a30f3f1aa603b49d31d4a16c2cdca00815582b94976a1a18c4`.

Both code versions returned exactly the same estimate, score, and error for this full-coast control. Runtime was 1.859 s higher in the integration execution, but each is a single measurement. The archived driver reports approximately 28.4 s and 89 m for this B1 setup, matching the fresh main replay. The integration changes therefore do not explain the previously reported full-coast result on this matched case.

## Why the older 17.42 m and current 10.33 m local results differ

The archived B1–B5 local pilot row at commit `b4ebf52` reports 17.421 m error; the fresh matched main/integration pairwise replays both report 10.335 m. The replayed settings and generated inputs are identical between main and integration, so this difference is not evidence of a main-to-integration regression. The archive predates current pairwise proposal caps: current source sets `max_pair_sources=5000` and `max_neighbors_per_source=64`, while the archived source did not. Those caps can change the proposal pool. This source-history difference is a plausible explanation, but the archived commit was not rerun, so the cause is not isolated.

## Full-coast V2 comparison boundary

The V1 local replay and A26 full-coast feasibility checkpoint are different experiments. The former uses the Chesapeake 500 m binary water mask, 799 catalog objects, 23 B1 reports, legacy absolute wrapped-residual scoring with injective assignment, and legacy unmatched penalties. A26 uses a partial-coverage coast MapV2, 66,668 catalog objects, five generated reports, the `bearing_set_map_v1` probabilistic objective, and candidate budget 64 over 39,840 cells. Its first checkpoint evaluated 64 candidates; map coverage remained unresolved, no candidate was scored or retained, and its status was `budget_exhausted`/`unresolved_coverage`. The evaluator was not invoked, so it did not produce a position error or accuracy result.

These objective, map, catalog, observation, and budget differences prevent direct V1-to-V2 score, accuracy, or runtime comparison. A26 is a search-feasibility checkpoint and cannot be described as an accuracy regression or improvement. The frozen experimental MapV2 hash used there was `cce049c5911b39072d30f26c09947782a78e350e65a3f69c6923312fba3f9d93`; no substitute map or reduced coast domain was used here.

## Artifacts and validation

The replay helper is `tools/compare_legacy_regression.py` for the local Chesapeake V1 case and `tools/compare_fullcoast_b1.py` for the single full-coast B1 control. JSON outputs are kept under ignored `.agent-work/outputs/` and include source/input hashes, settings, fingerprints, estimates, errors, and timing. The A26 focused tests passed before the replay. Ruff and `ty` passed for the local replay helper. Full-coast times are retained as observed diagnostic values, not as a controlled performance claim.
