# A14 visibility screen

Implemented `bearing_localization.search.visibility_screen.screen_candidate_visibility` as an exact line-of-sight screen over an immutable `PreparedPredictionContext`. It uses the context spatial index, WGS84 geodesic range and initial bearing, calibrated visible-feature offsets, inclusive range configuration, and shared circular angular grouping.

The screen groups all in-range physical targets before checking occlusion. The shared grouping algorithm partitions bearings at circular gaps larger than the merge threshold. Removing occluded members can only enlarge gaps between retained bearings, so distinct original components cannot merge. Each component with one exact clear member therefore certifies one distinct surviving visible group. A requested lower bound may stop ray checks early. `certified` indicates that the reported integer K is valid, while `complete` indicates every applicable in-range target was resolved.

Component evidence includes original group members, exact ray states, the confirmed clear key, unresolved keys, and unexamined keys. Unsupported feature offsets or unknown observer/LOS coverage produce an explicit unresolved reason and invalidate certification. No truth or target cap is used.

Validation passed. The exhaustive tiny angular fixture enumerated every occlusion subset, including circular north wrap, bridge-connected bearings, duplicate bearings, and a separate component. Full scan output matched scalar prediction group count on a blocked foreground/background and wraparound fixture. Early-stop, unknown LOS, unsupported model metadata, unknown observer with empty catalog, and shoreline endpoint coverage cases passed. Focused suite result was 38 passed across the new screen tests and existing A14, angular grouping, and visibility tests. Ruff formatting/check and ty check passed.

This implementation was not benchmarked and does not change search/scoring integration.

## Follow-up metadata certificate gate

The screen uses a versioned explicit allowlist for top-level model and inert metadata keys. It accepts all observed top-level metadata keys in the frozen 66,668-record catalog, including AIS `identity_kind`, `motion_status`, `motion_valid`, and `position_interpolated`, plus ENC identity, linkage, source and uncertainty fields. The separate provenance contract field is not inspected. Source-component rows are accepted only with the known seven-field A34 schema, including source longitude and latitude. Nested `source_attributes.height` is accepted as vertical and inert under the declared 2D point model; the predictor uses planimetric coordinates, 2D line of sight, and point bearings. Horizontal offset, shift, translation, dimension, length, width, beam, or unknown geometry keys remain unresolved.

On the frozen catalog, all 18 observed top-level metadata keys are explicitly recognized. Applying the final policy to all 66,668 records marks zero records unsupported: all nested source-component rows match the seven-field schema, and the only nested model-like field is vertical `height`, which is inert under the fixed 2D point model. The rejected-metadata regression cases are synthetic adversarial extensions; they do not occur in this frozen catalog.

Added regressions for adversarial offset/shift keys, unknown top-level metadata, nested dimensions, A34-shaped resolved source-component metadata, AIS motion metadata, supported visible-feature offsets, and vertical ENC height under the 2D model. Focused validation now passes 50 tests across screen, A14 predictor, shared grouping and search visibility. Ruff and ty pass. This follow-up is a certificate-safety gate only. The predictor and scorer remain unchanged.


## Frozen-input CPU diagnostic

A CPU-only diagnostic used the first 128 saved hypotheses in saved order from the frozen run, without reading truth. The catalog had 66,668 records. Across three measured repetitions after three warmups, early K=8 screening took 2.60 ms median and 124.37 ms mean; full screening took 2.58 ms median and 124.97 ms mean. The p95 values were 885.8 ms and 897.6 ms respectively, reflecting high-ray candidates rather than typical work. Screening averaged 23.35 exact rays per candidate, with a maximum of 462. All 128 certificates were valid, full-scan group counts matched prediction on all candidates, and all candidates scored. K reached at most 5, so the K=8 threshold did not shorten these scans. Full scoring took 17.69 ms median and 20.02 ms mean. Setup took 0.78 s.

These are shared-host diagnostic timings, not benchmark claims. They show that many candidates are inexpensive while a small tail is ray-heavy. The component scan now stops checking additional members after the first confirmed-clear target, while retaining those member keys as unexamined evidence; this follows the certificate proof. A matched timing for that change remains pending, so no speedup is claimed. The screen does not alter candidate scoring, prediction, or search defaults.


## Batched line-of-sight experiment

A non-default full-scan path batches exact line-of-sight queries through the existing `VectorMap.line_visibility_many` implementation. Partial-K scans retain the scalar path. The batch path reconstructs the same deterministically ordered component evidence and has a private scalar oracle for parity checks. Exact result and per-component evidence matched on all 256 paired calls for the first 128 saved hypotheses over two repetitions; the run did not read truth. On that shared host, scalar median/mean/p95 were 2.67/124.91/917.76 ms and forced-batch values were 18.64/21.12/57.26 ms. The batch path cuts the high-ray tail but is slower at the median. The first 128 candidates showed a clear crossover at 32 rays: all 17 candidates at or above that count were faster with batching, while most smaller candidates favored scalar visibility.

The 32-ray threshold was fixed before checking the remaining saved hypotheses. The second subset covered ranks 129–260, with 264 exact scalar/batch result comparisons. At or above 32 rays, 86 of 91 candidates were individually faster in batch mode; five were slower. Summing per-candidate median timings under the fixed branch rule produced a 9.02x aggregate reduction for this second subset. This is a workload-specific CPU diagnostic, not an accuracy claim or general benchmark.

| Saved hypotheses | Candidates using batch at 32+ rays | Exact result pairs | Scalar median-sum | Fixed-rule median-sum | Reduction |
| --- | ---: | ---: | ---: | ---: | ---: |
| Ranks 1–128, development selection | 17 of 128 | 256 | 15.99 s | 2.35 s | 6.79x |
| Ranks 129–260, threshold check | 91 of 132 | 264 | 75.11 s | 8.33 s | 9.02x |
| All saved hypotheses | 108 of 260 | 520 | 91.10 s | 10.68 s | 8.53x |

The public screen uses the batch path only for complete scans with at least 32 exact in-range LOS targets and a known-water observer. Partial-K scans and smaller full scans remain scalar. The candidate objective, per-ray classification, and result evidence remain unchanged.


The retained per-candidate harness is `tools/a14_visibility_batch_profile.py`. It verifies the frozen inputs before timing and asserts exact scalar/batch result equality for each measured candidate. The command for the first 128 saved hypotheses is `uv run python tools/a14_visibility_batch_profile.py --limit 128 --repeats 2`; the separate ranks 129–260 check uses `uv run python tools/a14_visibility_batch_profile.py --offset 128 --limit 0 --repeats 2`. The checked catalog, map and measurement SHA-256 values are `2554a1f6e574b6b9ffdefe6bb7b96636d756c9cbd87018be8f7e078738227704`, `cce049c5911b39072d30f26c09947782a78e350e65a3f69c6923312fba3f9d93`, and `ed1145cee93d4f06ffa983f671319dee49e9a95cb4692a2dd14a44c4a8eeceb1`. The script writes raw per-candidate tables under `.agent-work/outputs/a14-screen/profile_crossover_offset*_limit*.json`.
