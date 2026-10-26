# Accuracy implementation ledger

The implementation uses the original 40 bounded workstreams and eight additional specialist workstreams. All computation and environments remain on the server. The integration branch is accuracy/integration, starting from 32456174d6f7baea2c25bdfca6490f940b4214bf.

## Release gates

Each of the six range and type conditions requires at least 90% unique-fix availability and at least 95% conditional accuracy within 100 m. Unsupported scenes are reported separately. Poor geometry remains in supported denominators. The 100 ms target is evaluated separately.

## Workstream status

| Task | Responsibility | Status |
|---|---|---|
| A01 | Input contracts | Passed focused checks; integrated a12cf5e |
| A02 | Result and configuration contracts | Passed focused checks; integrated f98ada2 |
| A03 | Packaging, serialization, and environment | Passed packaging, serialization, CPU and real GPU checks; integrated 428a4cc |
| A04 | Geographic coordinates | Passed geodesic checks; integrated 1c4deef |
| A05 | Object taxonomy | Passed taxonomy checks; integrated 53809f9 |
| A06 | Independent reference fixtures | Passed independent assignment-reference checks; integrated b01dcd6 and 38ceea5 |
| A07 | AIS and sensor normalization | Passed normalization checks; integrated 405acfb |
| A08 | ENC and map ingestion | Passed acquisition/ingest checks; integrated 305aa0f; coastline coverage remains partial |
| A09 | Likelihood costs | Passed likelihood checks; integrated b5f3241 |
| A10 | Exact association | Passed exhaustive assignment parity and legacy label checks; integrated 0dd5145 |
| A11 | Vector water and visibility | Passed vector geometry checks; integrated 84135c9 |
| A12 | Conservative map summaries | Passed conservative geometry checks; integrated 69b198c |
| A13 | Exact spatial queries | Passed Python 3.11 spatial checks; integrated 873ae88 |
| A14 | Predicted visible groups | Passed visibility and grouping checks; integrated e4c8f23 |
| A15 | Simulator mismatch experiments | Passed simulator mismatch checks; integrated 48b8d77 |
| A16 | Evaluation metrics | Passed denominator and failure fixtures; integrated 7eb3212 |
| A17 | Existing algorithm adapters | Passed adapter and autonomous search checks; integrated 4888558 |
| A18 | Exhaustive reference search | Passed exhaustive finite-grid checks; integrated 83bf3b9 |
| A19 | Pairwise proposals | Passed proposal checks; integrated 99a33f9 |
| A20 | Continuous refinement | Passed continuous refinement checks; integrated 34fee96 |
| A21 | Region lower bounds | Passed bound checks; integrated f4b2817; spatial bounds remain weak |
| A22 | Branch-and-bound controller | Passed queue and budget checks; integrated 79f59af |
| A23 | Reverse-wedge proposals | Passed wedge proposal checks; integrated 169f629 |
| A24 | Acceptance and ambiguity | Passed calibration contract checks; integrated 9626e36; empirical calibration pending |
| A25 | Scene difficulty | Passed difficulty checks; integrated ce40379 |
| A26 | Dataset and benchmark controller | Passed aggregate support-extension checks; integrated 12e8292 |
| A27 | GPU cost generation | Passed CPU and device-1 cost parity; integrated 3600ff6 |
| A28 | GPU proposals | Passed device-2 planar proposal parity; integrated 8695a64 |
| A29 | Assignment acceleration experiment | Assignment acceleration deferred by measured bottleneck rule; integrated 0917632 |
| A30 | Multiple-GPU runtime | Passed one/two/four-device correctness; integrated 3a6859c |
| A31 | Search telemetry | Passed telemetry and real GPU timing checks; integrated 25ab68e |
| A32 | CLI and main orchestration | Passed standalone CLI checks; integrated 90ec7ce |
| A33 | Contract and process integration | Passed contract/process integration and GPU CLI parity; integrated 09dfc53 |
| A34 | Data and geometry integration | Passed data/geometry integration; integrated 88fcf27; source ambiguity retained |
| A35 | Search integration | Passed search integration checks; integrated 718c11e |
| A36 | GPU correctness integration | Passed GPU integration and CLI device parity; integrated 71da644 |
| A37 | Independent regression and CI | Prior gate passed 505 tests; renewed gate pending end-to-end corrections |
| A38 | Performance and held-out evaluation | Archive and execution helpers tested; formal evaluation pending corrected handoffs |
| A39 | Figures and reproducibility documentation | Exact map archives and verification integrated; result figures await evaluation |
| A40 | Independent release review | Read-only preflight underway; final verdict awaits final evidence |

## Evidence

No release accuracy or latency result has been produced by this implementation. The existing four GPUs have external memory allocations; timed trials require activity checks and an exclusive benchmark lock.

Authored by J.C. Vaught.

## Integrated check at 873ae88

The Python 3.11 CPU suite passed 312 tests with one optional GPU test skipped and seven subtests passed. Separate CuPy kernel checks ran successfully on devices 2 and 3. These checks do not establish localization accuracy or complete-search latency.

## Integration update

Thirty-three specialist agents have been dispatched. Accuracy and held-out evaluation remain pending. Device-level parity is established only for the documented cases; geographic proposal preparation remains on the CPU. The development assignment profile found proposal preparation dominant and assignment below the 20 percent acceleration threshold.

## Data and device integration update

Thirty-seven specialist agents have now been dispatched. The combined runtime catalog preserves 66,668 records, including all 64,400 chart records; 44,580 chart records carry unresolved identity information. The detailed geographic mosaic preserves known port coverage and marks the remaining domain unknown. These inputs do not establish complete-coast visibility.

The one-, two-, and four-device CLI checks returned identical hypotheses, decision statuses, and budget accounting for the nine-candidate reference fixture. These are correctness checks, not performance or accuracy benchmarks.

## End-to-end preflight corrections

All forty specialist roles have been dispatched. The prior automated gate passed 505 tests, with 25 optional GPU tests skipped in the CPU environment and seven subtests passed. Separate hardware checks passed on one, two, and four devices. These checks establish only the tested software behavior, not the six release accuracy requirements.

The preflight review identified and corrected an overly restrictive chart-identity rule. All valid chart records remain eligible for range, visibility, and association calculations. Physical support counts remain conservative and use only distinct resolved nearest representatives after angular merging. The source linkage heuristic is an explicit study assumption. Missing names and uncertain cross-chart linkage remain diagnostic metadata.

The search decision now separates verified competing local modes from global optimality certificates. Raw distant proposals cannot establish positive confidence, and low-gap alternatives can veto acceptance. Standalone CLI wiring and independent regression are being updated before calibration.

The program handoff review also identified different projected coordinate systems between simulator truth and search output, static settings across range/type conditions, and shared output paths between algorithm runs. Corrections preserve raw inputs and estimates, record coordinate provenance, create condition-specific configurations, and isolate hashed run artifacts. Explicit-coordinate evaluation computes physical error geodesically.

The map archives are committed under data/maps/accuracy-inputs with streaming checksum verification. They preserve the exact partial source collection, coast mosaic, and twenty-four port maps. Unknown coverage remains unknown. Seven hundred prepared rows using the superseded catalog remain archived as provisional evidence; no held-out search has run. Formal accuracy, latency, and measured-bearing field validation remain unvalidated.


## Integration update on 26 October 2026

Forty-five specialist roles have been dispatched, with the coordinator separate. A41 completed verified archive deduplication, A42 completed population freezing, and A43 added bounded profiling and is optimizing proposal geometry reuse. A44 investigates catalog loading, while A45 investigates exact map construction. Their optimizations must preserve input validation, candidate ordering, geometry, and the complete objective.

The latest completed broad CPU gate at commit 2792c75 passed 578 tests, with 25 optional hardware tests skipped and seven subtests passed. Separate subsequent one-, two-, and four-device checks passed 82 hardware tests without skips. The calibration integration at 4085d73 passed 72 focused tests. These checkpoints precede the remaining performance changes and require a renewed final gate.

Calibration artifacts now bind the range, type mode, objective, algorithm, input hashes, execution settings, implementation, and planned population. Version-one artifacts remain readable but cannot establish release acceptance. Nominal scenes alone select thresholds. Failed stress preparations remain explicit unresolved rows. Missing nominal eligibility prevents freezing.

The corrected development manifest includes every one of 6,120 planned rows. Its 3,600 nominal rows have no unresolved eligibility. Each range/type condition has 600 nominal rows. Supported counts are 269 at 1 km, 438 at 5 km, and 448 at 10 km, separately for each type mode. The twelve failed stress preparations remain recorded. These are eligibility counts established before localization outcomes, not successful fixes. The manifest digest is 2330090802952093bab1d5ec6cb97c6298cce27ee2050804f6eab91d66e8d050.

The approved map package is stored under data/maps/accuracy-inputs-v3. On a common equal-area basis, known coverage is 76.260149 percent of the declared bounding box and unknown coverage is 23.739851 percent. Fine port geometry is retained. Coverage is independent of localization accuracy.

The first diagnostic pairwise pilot recorded two 300-second timeouts and one interrupted attempt. The first attempt may overlap a map-file rewrite and is retained with that qualification. Future runs use immutable, hash-named inputs. A separate watchdog-limited 30-second profile found approximately 10.5 seconds in input loading, 10.5 seconds in map construction, and 5.8 seconds in proposal generation. It stopped before completion, so its partial counters and times are not a completed-search benchmark. It motivates profiling and exact caching, not weakening the search domain or score.

Verified hardlink deduplication reclaimed 15.29 GiB while preserving every archived path. Further preparation remains subject to the 40 GiB free-space reserve. Formal calibration, validation, held-out accuracy, final latency, and measured-bearing field accuracy remain unvalidated.

## Full-coast search repair checkpoint at 07d8036

A46 owns deterministic water-prioritized candidate ordering and hierarchy budget accounting. Its three accepted milestones preserve all generated candidates, retain deferred coarse candidates, and distinguish exact budget fits from unresolved work. The GPU grid dispatcher uses the same ordering. A47 owns complete chunked wedge voting, separating source coverage from accumulator storage and final exact scoring. Independent scalar parity passed, and the frozen five-observation catalog run covered all 333,340 source pairs at coarse resolution. A33 integrated the explicit wedge_complete program path; 54 combined tests, lint, and type checks passed.

A43's exact batched line-of-sight implementation preserves all 909 frozen classifications and reduced that component median from 10.6785 to 0.1697 seconds. The repaired pairwise run completes in 48 seconds, but the best score-ranked hypothesis remains 509,653.742 m from truth after the explicit CRS correction below. This result remains ambiguous and budget_exhausted. A26 is executing the next independent complete-voting diagnostic, while A35 compares simulator prediction and objective decomposition after search to locate any remaining model disagreement. Truth is restricted to that evaluator-only diagnosis.

A17 reproduced the existing version-one local and full-coast B1 controls at main and integration. Both commits return identical estimates, with errors 10.3346 m locally and 89.091 m coast-wide. The newer sparse five-observation pilot is a different problem instance and objective. Its failure remains recorded independently.

A38 has prepared 570 of 2,040 validation rows and is paused for the timing window. A41 verified lossless deduplication after each bounded wave; available space is approximately 63.94 GiB. No held-out search has been opened. Empirical calibration, six release gates, final timing, publication results, and independent final review remain unfinished.

## Evaluator correction and geodesic repair

A35 verified that the saved truth frame is EPSG:32619 and the estimator frame is EPSG:32618. Applying both explicit transforms gives the earlier A43 top error 509,653.742 m and minimum error 506,055.789 m. The earlier 391 km diagnostic values were incorrect and are retained only as superseded evidence. The new complete-wedge result has top error 1,898,447.237 m and nearest error 1,897.057 m among 260 hypotheses. It processed all source pairs but only 256 exact candidates and remains budget_exhausted.

The exact objective at transformed truth is 5.206587168 with five correct predicted groups; the returned top objective is 24.926678078 with one match. A35 therefore identified a missed search basin. A04 independently measured up to 5.2323 degrees of global-frame north error and implemented a true-north geodesic primitive with explicit convergence flags. A47 is integrating corrected geographic proposals and source-aware candidate priority. Existing searches and diagnostic artifacts remain preserved.

The renewed A37 checkpoint at 07d8036 passes 709 CPU tests, 28 optional skips, seven subtests, full static checks, installation and standalone commands, and all 19 Typst sources. The tested software gate remains separate from the still-failed localization diagnostic. No held-out outcome has been opened.

## Geodesic diagnostic handoff at 0ae7c1e

A04 completed the independent numerical tolerance audit. A43 completed and pushed the vectorized geodesic generator with scalar-oracle parity and a 1.715-fold 500-pair component speedup. A33 integrated the explicit wedge_geodesic CLI, source-witness priority, separate stage timings, failure handling, and missing-sigma fallback. The coordinator reran the combined suite with 69 passing tests and pushed the integration checkpoint. A26 is assigned the unchanged frozen development case at that exact commit under the benchmark lock. No search result is yet available for the new path.

A39 corrected and regenerated the progress PDF and PNG, preserving the superseded figure commit. The figure records the corrected 509,653.742 m earlier diagnostic error and does not claim the pending geodesic search is accurate. A38 reached 1,830 of 2,040 validation preparation rows with zero preparation failures and 53.08 GiB free at the wave-27 checkpoint. Preparation pauses for the diagnostic; validation search and held-out outcomes remain unopened. A19 is repairing bounded continuous-seed enumeration, and A30 is implementing a persistent CPU exact-scoring pool. Both are separate follow-on milestones.

## Failed geodesic run and finite-candidate screening checkpoint at 25747d6

The 0ae7c1e development diagnostic finished in 706.479 seconds and did not recover the boat. Top and nearest saved errors were 548,327.465 m and 128,757.746 m. All 333,340 source pairs were visited, but only 256 of 1,457,546 cells were exactly scored. A35 proved the truth-containing water cell was emitted by the configured sampler and discarded by geometric priority. Complete objective scores in its neighborhood beat the returned best score. The evaluator-only diagnostic is isolated from estimator inputs.

A04 repaired the numerical solver's stopping margin without changing the public tolerance. All 209 recorded failed samples converge on replay; the complete source set has not been rerun. A19 and A33 integrated continuous witness seeds with fair interleaving before cell centers. A30 repaired persistent-worker termination and canceled-batch isolation after A37 found hangs. The coordinator's combined checkpoint passed 143 focused tests before subsequent cache and bound additions.

A43 rejected slower CPU proposal multiprocessing and published its failed experiment. The exact measured prototype source was lost, and the reconstruction is labeled accordingly. A28's optional GPU geodesic prototype passed six actual-device tests; one million samples took 1.523 seconds versus 1.734 seconds for the CPU helper in a single locked comparison. No full-search speed claim follows.

A13's reusable proposal cache and A14/A21's visibility/cardinality lower-bound helpers are integrated through 25747d6. A06 owns the independent mathematical and adversarial review. A14 is resolving metadata support and profiling its exact screen. A22 owns the finite-candidate screened controller. A33 owns cache CLI wiring. A37 is running a renewed repository-wide gate. These tasks are active in separate worktrees; pruning is not yet approved as a complete integrated path.

A38 completed validation preparation and the eligibility-only extension. The resulting 2,340 rows include 1,500 nominal rows, with per-type supported counts 136, 178, and 181 at 1, 5, and 10 km. The first 60 extension attempts failed before the placement-limit adapter repair; successful retries and every original failure remain recorded. All six support-count minima are met. No validation search or held-out outcomes have been opened. Release accuracy, calibration, latency, and final independent review remain outstanding.

## Independent finite-set approval and scalable experiment handoff

A06 permits experimental finite-candidate pruning at ecc991d after independent mathematical and adversarial review. The focused review passed 59 tests and the complete suite passed 872 tests, with 31 optional skips and seven passing subtests. Unknown coverage and unsupported models receive exact fallback, supplied incumbents are re-scored, and no continuous-domain claim follows. A14's private batched screen preserves full result parity but remains non-default pending a fixed-threshold workload check.

A30's bounded parallel pool is integrated at 9654702. One-, two-, and four-worker tests pass, and the coordinator's combined pool/controller/reference checks pass 35 tests. A37 is reviewing out-of-order worker failures and the new cached experiment driver. A26 owns explicit per-ID failure/unvisited records, process-wide deadlines, and separate generation/scoring source identities. Large candidate scoring has not begun.

Two cache attempts failed before publishing a cache. The missing-parent failure motivated A33's early destination preflight. The interrupted second attempt exposed A13's repeated full-catalog lookup construction in the per-proposal validation loop. That lookup now builds once; all validation remains enabled. The coordinator's combined cache and CLI gate passed 64 tests. A26's new cache-build source is pinned to 33fded7, preserving generation settings and physical inputs. This retry remains a cache handoff experiment, not evidence of location recovery.

An additional storage specialist recovered approximately 460 MB of disposable or duplicate allocated content while preserving active outputs, data hashes, and all archived paths. The tracked storage report distinguishes reclaimed bytes from unrelated free-space movement. Release accuracy, calibration, clean scaling timings, and final release review remain unfinished.

## Complete cache generation at 33fded7

A26's fourth cache-build attempt completed in 437.944 seconds from the frozen source 33fded7. All 333,340 source pairs and 83,001,660 geodesic samples were processed with zero numerical failures. The 1,457,546-cell cache occupies 126,043,477 bytes. Its tree digest is d6154c18349842d22ca1cd5eaf2d022a6ac67004eebc9e35c5c59d03ad767e72. This handoff scored only one candidate and is not an accuracy result.

A14's fixed 32-ray hybrid visibility screen is integrated at 8d84647 after 520 full-result parity comparisons. The coordinator's focused gate passed 54 tests. A26, A30, and A37 continue the full-domain scoring handoff with explicit source identity, process deadlines, durable per-candidate outcomes, and real subprocess checks. A20 resumed to provide the complete-objective refinement handoff. Existing owners retain exclusive files; no duplicate implementation job was started. Search recovery, calibration, held-out evaluation, full latency, and final release review remain open.
