# A43 Post-Checkpoint Pairwise Profile

## Run identity and scope

This was a bounded 30-second CPU cProfile of the exact c76be7c, 10 km, types-off, budget-64 row recorded in .agent-work/outputs/a26-pilot-c76be7c/runs/pairwise/budget-64/c9b456821364c3ecc8e93992/run-manifest.json. It ran only after the five-search checkpoint and while holding the shared benchmark lock. The watchdog fired at 30.0536 seconds, saved partial cProfile artifacts, and exited with status 124. The lock was released on process exit. No estimate or final telemetry was produced, so the run gives partial-stack counts rather than completed-row work or runtime.

The pinned source was commit c76be7c0f728f302f51a5acf433288512e52c684. SHA-256 values were 9e2bc379205ba263180eecffddfbf4dc7779a1727a9eb7a7e935f7566ed124df for bearing_localization/search/proposals/pairwise.py, f8c8caeba0222ed80d11f56a5567bb0eb921699468c6390304d6c02946205d30 for bearing_localization/search/scoring.py, 8e0a580c44c91a70c084d1483fa55cbee2a41883cf63eeaea8f23c39f471b895 for bearing_localization/search/prediction.py, and 01d82f9efa0b6deccdf4a512f42702120ff4a51380f9e0b61c693f2adff6a250 for maritime_localization_shared/geospatial/vector_map.py. Frozen inputs were measurement SHA-256 ed1145cee93d4f06ffa983f671319dee49e9a95cb4692a2dd14a44c4a8eeceb1, catalog 2554a1f6e574b6b9ffdefe6bb7b96636d756c9cbd87018be8f7e078738227704, map cce049c5911b39072d30f26c09947782a78e350e65a3f69c6923312fba3f9d93, and config e34051b1ba59fd58268fb933cd53fa4ea4bdbb596ddfbe8c2870e49c48ca5c9c.

The partial artifacts are .agent-work/outputs/a26-pilot-c76be7c/profile-after-checkpoint/budget64-10km-typesoff.prof, .txt, and .json. The profile recorded 24,691,157 calls in 29.862 seconds.

## Observed hot stacks

In this profile, proposal generation was not the dominant sampled cost. generate_geographic_pairwise_proposals accumulated 0.617 seconds over two generator calls. run_pairwise_search_v2 accumulated 9.927 seconds, of which score_candidate_positions accumulated 9.290 seconds. The accumulator received 64 proposal positions and CandidateScorer.evaluate ran 55 times in the observed window.

The largest measured scoring stack was exact line-of-sight geometry. predict_visible_groups ran five times and accumulated 10.772 seconds. Those calls reached VectorMap.line_visibility 909 times for 10.723 seconds, _interior_overlap_exceeds 2,107 times for 10.588 seconds, and GEOS intersection_scalar 2,108 times for 11.440 seconds. This identifies candidate-specific ray-versus-land-polygon intersection as the primary hot path in the observed search interval. It does not establish counts for any work after the 30-second watchdog.

Catalog and map setup also consumed a substantial part of the first 30 seconds. accuracy_cli.__init__ accumulated 15.046 seconds, three loads_contract calls accumulated 9.997 seconds, MapV2 JSON parsing accumulated 4.653 seconds, and VectorMap.__init__ accumulated 3.545 seconds. These are startup costs in this cold CLI profile and should be separated from steady-state scoring when interpreting end-to-end latency.

## Source-confirmed work and optimization candidate

CandidateScorer.evaluate checks candidate water before calling predict_visible_groups in bearing_localization/search/scoring.py. predict_visible_groups computes observer water status once in bearing_localization/search/prediction.py, but each target then calls VectorMap.line_visibility. That method recomputes the same observer water classification for every target in maritime_localization_shared/geospatial/vector_map.py. The profile shows 909 line-visibility calls and 1,604 Shapely contains calls, so sharing the already-known candidate water status across a batch of target rays is a concrete exact-preserving optimization candidate. The map-facing API should remain safe for existing callers, and the current exact ray/polygon intersection, unknown-coverage handling, and shoreline tolerance should remain the oracle.

The larger measured cost is constructive ray/polygon intersection. A possible exact-preserving experiment is to add an indexed batch-visibility path that classifies the observer once, obtains the same STRtree polygon candidates, and applies the existing _interior_overlap_exceeds routine unchanged to each ray candidate. This removes repeated observer classification while preserving exact per-ray decisions. Any attempt to skip constructive intersections with prepared predicates must retain the existing intersection path for every predicate-positive pair and demonstrate exact parity; the profile alone does not show that such a predicate would improve runtime.

A correctness review for a future scoped change should compare ordered per-target clear, blocked, and unknown results against the existing scalar line_visibility oracle. Fixtures should include water, land and unknown observers, land interior crossings, shoreline-only contact, endpoint-on-shore, unknown-region overlap, polygon holes, zero-length rays, tolerance boundaries, and indexed versus unindexed queries. The same frozen 10 km row should then be profiled before and after under the benchmark lock, with startup and candidate scoring reported separately. No production change is made in this report.

## Interpretation limit

The profile stopped during search_v2_files and emitted no estimate or telemetry. Although 64 accumulator additions and 55 evaluations were observed, these counts do not prove that the CLI completed all work, nor do they measure the full candidate stream, refinement, or a complete runtime. The profile identifies the hot stack only for the observed 30-second prefix.
