# Accuracy implementation ledger

The implementation uses the original 40 bounded workstreams and nine additional specialist workstreams. All computation and environments remain on the server. The integration branch is accuracy/integration, starting from 32456174d6f7baea2c25bdfca6490f940b4214bf.

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

## Verified program handoff at bb2ff81

A26's real parent-worker smoke passed with driver 1ef881b and scorer 0d34a17 in 123.884 seconds. One explicitly selected candidate was scored; all 1,457,546 IDs received unique dispositions, and full-domain completion/certification correctly remained false. The earlier strict-config failure and forced timeout remain archived. The coordinator integrated the driver and fixed standalone test collection. The combined gate passed 45 tests with one environment-gated skip; the bb2ff81 commit body's 46 is a transcription error corrected here. Scoped Ruff and ty passed.

A separate profile specialist now owns only tools/profile_cached_candidate_scoring.py, its tests, and its measured report. It compares identical predetermined cache-index samples across exact/screened modes and one/four CPU workers. A26 owns full-domain continuation, A37 reviews it independently, and A20's existing complete-objective refinement handoff is ready. The new role brings the dispatched specialist-role count to forty-nine, with the coordinator separate. A further storage pass preserved all output paths and recovered 468,852,736 bytes from identical completed plan/inventory files. Full search recovery and subsequent release experiments remain pending.


## Cached scoring scale measurements and continuation integrity

The predetermined 2,048-candidate exact-scoring comparison completed with one, four, and eight workers. Request-stage times were 51.227, 14.530, and 8.136 seconds; startup took 6.613, 16.945, and 28.157 seconds separately. All completed cases evaluated every selected candidate and agreed on objective 24.1270900878417 and candidate cell:-35:2251. Eight workers used 7.96 GiB summed worker peak RSS. Sixteen workers timed out during startup before dispatch. These shared-host sample results select eight exact workers for the full-domain diagnostic; they are not a full-search accuracy or fresh-frame latency result. The corrected report distinguishes cache-order strata and counts startup once when illustrating runtime estimates.

The revised screening controller removes a redundant partial visibility scan. On the same 255 candidates, direct serial exact scoring took 18.238 seconds and revised screening took 23.025 seconds, despite pruning 85 candidates. All 85 pruned bounds passed independent exact-score validation, and the best score and tie agreed. The revised implementation is retained as an experimental comparison, but direct exact scoring is selected for the next full run because screening remained 26.2 percent slower in this diagnostic. The integration controller/pool/profile gate passed 35 tests in 9.04 seconds, with scoped Ruff and ty passing.

The independent evaluator adapter is integrated through 3d030f1. It verifies identity, catalog and map bindings, candidate plan and inventory, exact-score sidecars, continuation lineage, and complete per-ID dispositions before opening evaluator truth. Search coordinates use EPSG:32618 and truth uses EPSG:32619; physical errors are calculated geodesically. Its ten focused tests, Ruff, and ty pass. The new continuation producer is under a real parent-and-child subprocess check at 1002d733 after independent review exposed ordering, action-count, sidecar, and interrupted-manifest integrity defects. The corrections retain prior attempts and refuse mismatched continuation state. A completed full-domain localization result is still pending.

The measured comparison is available as a [zoomable progress PDF](figures/cached-search-progress-2026-10-26.pdf). Its Typst source and imported JSON retain profile source hashes, the sixteen-worker failure, and the slower screening experiment. The figure was compiled, inspected, and corrected to remove rounded legend boxes and show exact stage times. Further exact geometry reuse and a candidate-specific pre-visibility lower bound remain separate investigations; neither changes the frozen scoring source for the next run.

The storage pass removed 27 inactive regenerable environments only after checking clean worktrees and active process references. The measured free-space increase was 446,189,568 bytes, leaving 46,828,638,208 bytes free and 3,878,965,248 bytes above the 40 GiB reserve. Shared cache links mean summed file blocks overstate physical reclamation, so the report preserves both measurements. Source, inputs, outputs, active environments, and root environment remain intact.


## Interrupted full-domain scoring and refinement checkpoint

The exact cached run used all 1,457,546 candidates as its declared domain and stopped after 47,264 durable checks when disk headroom approached the 40 GiB reserve. The operator terminated only the owned worker process group; the supervisor retained complete candidate dispositions. External elapsed time was 859.992 seconds. There are 47,182 exact scores, 82 unresolved candidates, zero failed candidate IDs, and 1,410,282 unknown or unvisited candidates. A trailing uncommitted 32-row sidecar remains excluded from ranking. The raw worker-failed status records SIGTERM; the operator record distinguishes this resource stop from a search crash.

The independently verified lowest saved objective is 14.6053352926, with 853,001.682 m position error and zero of three correct associations. The correct area now appears among the retained alternatives. Score rank 12 is 101.335 m from truth with two of four correct associations; rank 5 is 296.286 m away with four of four correct associations. These observations establish useful candidate retention, not successful top-ranked localization. Search selection used no truth. The complete partial result and immutable bindings are in [the evaluator report](../reports/experiments/a35-cached-evaluation/report.md).

A20 is preparing a bounded continuous-refinement experiment over up to 64 score-selected spatial modes. Its inputs contain measurements, catalog, map, configuration, and committed coarse hypotheses only. It will recompute complete exact assignment, range, visibility, and merging during CPU FP64 refinement. A35 independently evaluates the output after it is durable. No refined result has yet been produced. The experiment remains incomplete search, even if its best estimate improves.

The latest broad frozen gate at 134bd78 passed 926 tests with 32 optional skips and seven subtests; Ruff formatting, lint, type checks, and installed command checks passed. Packaging builds were deferred under the storage reserve. Subsequent trusted pre-visibility geometry and row-minimum bound changes passed 37 focused independent audit tests. A22 and A06 are now preparing and reviewing a small exact-versus-bound profile. Production pruning remains unchanged until caller review and measured evidence are available.

The updated evaluator verifies interrupted ledgers and excludes orphan sidecars; its twelve focused tests, Ruff, and ty pass. Calibration, validation search, held-out outcomes, the six release decisions, and complete-search latency remain pending. Storage headroom fluctuates with other server activity. Further runs retain the 40 GiB floor and require bounded outputs.

## Completed selected-mode refinement and screening comparisons

The 64-mode pattern-refinement diagnostic completed every selected mode in 1,255.418 seconds. Every selected mode improved its exact objective, but the independently verified first-ranked result remains 853.440 km from truth with zero of four correct associations. The nearby four-correct-association alternative moved from 296.286 m to 266.378 m and ranks eleventh after refinement. These results demonstrate a local-search failure and do not establish successful localization. The source global search remains incomplete, with 1,410,282 unknown or unvisited candidates.

A second bounded diagnostic fits the currently assigned geographic bearings, then recomputes complete range, land visibility, angular merging, and exact association. It completed both coarse and pattern-refined starts for all 64 selected modes in 57.652 seconds. The estimator uses no truth inputs and never converts these local proposals into accepted fixes. Its immutable manifest, checkpoint, and trial rows are retained under reports/experiments/assigned-bearing-refinement-diagnostic-v1. Independent evaluation of this second diagnostic is being finalized.

The pre-visibility lower-bound profile was slower than direct exact scoring on the same 255 candidates. Exact scoring took 17.906 seconds and screening took 32.632 seconds. All 34 exclusions passed independent exact verification, and the optimum and tie agreed. The bound remains an experimental comparison; production pruning has not switched to it. The profile and initial preflight failure are retained under reports/experiments/a22-pre-los.

The integrated diagnostic gate passed 51 tests, scoped Ruff, ty, and git diff checks. A standalone test-import defect was repaired before this gate. These focused checks supplement the earlier broad 926-test gate and do not replace the final release gate. Nineteen clean, remotely preserved, inactive worktrees were removed after active-process checks, reclaiming 3,378,491,392 measured bytes while preserving all evidence and source branches. The 40 GiB reserve remains mandatory.

The corrected zoomable progress figure is available under docs/figures/cached-refinement-progress-2026-10-26.pdf. It distinguishes the first-ranked wrong result from the nearby alternative and labels the modes as score-selected. Calibration, validation search, held-out outcomes, release accuracy decisions, and clean end-to-end latency measurements remain pending.

## Independent assignment-fit diagnosis

The independently checked 64-mode assignment-conditioned fit retains a wrong first-ranked result at 853,439.8 m error and objective 10.324237. Its nearest selected alternative improves from 266.378 m to 181.279 m with four correct associations and objective 14.777115. The fifth true group is visible at that location. Its 2.1501 degree residual gives an edge cost of 8.564, exceeding the combined unmatched-report and unused-group cost of 8.189. The exact assignment therefore correctly omits it locally.

Frozen-source rechecks agree with the current scorer at the selected winner, nearby alternative, and evaluator-only true position. At truth, all five groups match and objective 5.206587 is lower. This identifies an assignment-conditioned local minimum. The known-association 95 percent major semiaxis is 607.14 m for the four reports and 145.04 m for all five; these evaluator-only diagnostics never enter search. The complete evaluation is retained under reports/experiments/a35-cached-evaluation/assigned-refinement-evaluation.json with SHA-256 e0c1b989f82b7a29e3a09dfde541ed0f437edcc82a4a15ab162fd8e01422d3a8.

A new bounded proposal is being implemented and independently reviewed. It tries a single additional unmatched-report/catalog assignment, fits the pose jointly, and recomputes the unchanged complete objective. All 64 existing score-selected modes remain the diagnostic population. The local proposal window and branch budget are explicit heuristic limits; this work cannot certify the unvisited coast-wide domain. Root integration checks for the new evaluator and existing bound audit passed 22 tests, Ruff, ty, and whitespace validation after a standalone test import repair.

## Assignment expansion implementation gate

The bounded single-addition proposal and its standalone diagnostic runner are integrated. Each branch adds one unmatched report/catalog pairing, fits the geographic bearings jointly, then recomputes the complete canonical objective. Prepared catalog and seed geometry are reused across branches. Existing score-selected modes, measurements, map, catalog, noise, and objective remain unchanged.

The coordinator's combined gate passed 65 tests in 0.71 seconds, scoped Ruff and ty, and whitespace checks. Independent review contributed adversarial cases. An unmocked production-scorer fixture confirms escape from an exact four-match seed to all five associations with a lower complete objective. Review repairs bind the actual loaded package and source-file hashes, persist each branch result before the next branch, preserve callback interruptions, and make standalone test collection independent of PYTHONPATH. The diagnostic allows at most 50 MiB of outputs while preserving the 40 GiB reserve.

Read-only preflight found 22,000 branches across 64 modes. The real-data run is authorized at frozen runner eb17e17, with a 1,800-second deadline and the canonical server benchmark lock. No outcome is yet available. This experiment is a local proposal comparison over selected alternatives; the underlying global search remains incomplete and cannot return an accepted release fix.

The separate broad gate at 918ab7b passed 1,016 tests, 32 optional skips, and seven subtests. Ruff lint, ty and installed commands passed. Its one formatting failure was repaired separately. Wheel/source-distribution installation and final release checks remain pending.

## CI packaging, refinement execution, and bound batching

GitHub CI run 40325296325 completed successfully for a9ac9f202c2f53f5adcf2df12fff4e3c649f7d10. The Python job passed tests, installed command checks, wheel and source-distribution build/install checks, Ruff formatting/lint, and ty. The Typst job compiled every figure source. The optional hardware job was skipped. This supplies packaging and figure-build evidence without new server dependency downloads.

The first assignment-expansion launch failed before output creation because its 50 MiB run-output limit was incorrectly measured against the shared outputs directory. Commit 6f7cdf4354fddbc7237625bde2d82763362068c8 repairs that path scope while preserving the free-space reserve. The actual running diagnostic is frozen at that commit; the earlier eb17e17 authorization identifies its predecessor, not the executed package. No search branch ran in the failed launch.

Stage-one pre-visibility bound batching is integrated with unchanged canonical likelihood inputs and uncertainty variants. It reuses clutter from the empty-target calculation and prepares component membership once. The scalar-reference comparisons preserve the complete bound decomposition exactly. The combined bound/evaluator integration checks passed 36 tests. No timing gain is claimed yet, and the default pruning policy remains unchanged.

Focused pytest collection exposed an existing path-order dependency in test helper imports. The shared pytest configuration now adds the repository root explicitly, so a focused test does not depend on other collected tests modifying sys.path. The previously failing four-test caller suite passes by itself. The dataset, objective, execution limits, and acceptance thresholds are unchanged.

The serial real-data diagnostic remains active. A separate four-worker implementation and independent review are being prepared using the identical mode population and branch algorithm. Parallel execution will wait for the serial lock and its own correctness gate. The evaluator will retain an interrupted prefix and pending work if the serial deadline is reached.

## Preserved serial assignment-expansion deadline result

The serial diagnostic at executed commit 6f7cdf4354fddbc7237625bde2d82763362068c8 reached its 1,800-second deadline. It finalized 60 of 64 selected modes and left four pending. Actual elapsed time was 1,800.016071 seconds. The event ledger contains 20,435 branch starts, 20,435 branch results, and one deadline failure event. Results comprise 12,185 non-improvements, 8,225 invalid-water proposals, six maximum-evaluation proposals, and 19 running-best improvements. These counts are proposal outcomes, not independent localization trials.

The raw manifest, checkpoint, rows, and events are retained under reports/experiments/assignment-expansion-diagnostic-v1. The manifest hash is 290c2645f8252a951491a99e2fa6bca6b29d2222d04cec8c9935a75b27db5e23; row and event hashes match the producer. The final tracked files total 22,055,971 bytes. The manifest's earlier output_bytes field was sampled before its final atomic rewrite and is 288 bytes smaller; no file is missing.

Decision remains ambiguous, search status remains budget_exhausted, and certificate scope remains none. The unfinished mode's event tail remains preserved but does not become a fabricated completed-mode hypothesis. Independent evaluation is checking the returned results. The parallel runner remains under review, including partial process-start failure cleanup, before any actual execution.


## Provisional recovery and reviewed four-worker implementation

At integration 94c0c7b, the independently validated pass-one result places the best of 60 completed selected modes 11.127008 m from truth with five of five correct associations. Score is 5.026904167180264. The serial run remains interrupted at 1,800.016071 seconds with four pending modes and incomplete global coverage. Full branch replay remains in progress; acceptance and certification are not claimed.

A35's immutable source and checkpoint-prefix validation fixes are integrated as 4ad2709. The CPU pool and runner are integrated as 2862818 and a9e9dea. Independent review commits 82d18c2 and 94c0c7b clear the earlier startup failures. The combined root integration check passes 43 tests, Ruff and ty. Actual four-worker execution waits for the 40 GiB disk reserve. A39 is adding a clearly provisional fourth stage to the zoomable progress figure.

The existing success criteria, supported denominators, calibrated acceptance requirements, split manifests and held-out isolation remain unchanged. Calibration, the six release decisions, clean GPU scaling measurements and measured-bearing field validation remain unfinished.


## Audited development recovery and postprocessing integration

A35's full audit is integrated as ecaf55b. It independently verifies the 11.127008 m, five-match lowest-score result among 60 completed modes, while retaining four pending modes and incomplete global coverage. A39's audited figure update is integrated as 44b38c2. The four-worker run at a41570c is executing the same 64-mode, 22,000-branch workload after verified cleanup restored the disk reserve.

The shared result-contract fix at 4e8bc42 preserves candidate counters while declaring request or assignment-branch exhaustion. It passes 42 CPU-environment contract/serialization tests. A32 owns normal-CLI expansion integration; A26 owns suite argument binding and execution fingerprints. Neither changes acceptance thresholds or opens validation/held-out results.

Coordinator review rejected the initial parallel evaluator's complete-audit logic and producer-schema assumptions. Its owner is repairing those paths with production-derived contract fixtures and independent A35 review. Complete parallel validation is pending. CI import ordering was repaired at cbc2bee after an otherwise passing build/test/figure run. Final broad integration checks remain required after the CLI and evaluator changes land.


## Current integration checkpoint and audit-count correction

The standard CLI and suite now expose both refinement-seeded and direct coarse-seeded assignment expansion. Integrated source commits 4ede022 and 603240c preserve the objective, truth boundary, explicit budgets, and ambiguous status. The direct route avoids calling pattern refinement, records the seed stage and selected/unselected mode counts, and binds these settings into the execution fingerprint. A32 reports 66 focused checks and A26 reports 21 owned checks plus 24 combined checks. These are software checks; no new real-data benchmark ran for the direct route.

The serial full-audit count requires a correction. Completed modes 0 through 59 contain 20,381 branch outcomes, including 12,150 exact-scored poses independently replayed, 8,225 invalid-water outcomes, and six solver-limit outcomes retained structurally. The unfinished mode-60 tail contains 54 additional scored events whose record integrity was checked but whose objectives were not replayed in that audit. Thus the prior statement of 12,204 independent exact-score replays was incorrect. The immutable audit JSON is preserved. The winning 11.127008 m, five-correct-association hypothesis was independently rescored and is unchanged.

The four-worker run finalized at 568.419209 seconds when its storage guard fired. It completed 63 of 64 selected modes and left mode 48 pending. Its manifest remains failed, with ambiguous decision, exhausted budget, no certificate, and incomplete global coverage. Raw manifest, checkpoint, rows, events, and runner log are tracked under reports/experiments/parallel-assignment-expansion-diagnostic-v1. This interrupted measurement is not a complete four-worker latency or a valid speedup ratio against the serial run.

The serial run completed mode 48, so their union contains all 64 selected modes. A separate audit validates unchanged inputs, source hashes, overlapping event records, and returned hypotheses before replaying the additional completed modes. Composite validation remains pending. Neither original producer will be relabeled completed, and finite selected-mode coverage cannot certify the unvisited coast-wide domain.

Hosted CI at 03d3459 passed 1,158 tests with 32 optional skips but failed two evaluator tests that depended on server-specific paths. The portable fixture repair is integrated as 0e31f9f; a new hosted run covers 603240c. The local broad attempt passed 1,152 tests with eight environment/storage failures; those eight passed on a corrected targeted rerun. This history is retained rather than described as one clean full-suite run.

The user has requested publication of the validated implementation checkpoint on main. This advances the working repository and visible figures without declaring the unfinished scientific release gates passed. The user also authorized inactive archives on another comech server. Active implementation and computation remain on comech-2080, with frozen input paths and the 40 GiB reserve preserved.


## Published main checkpoint and complete selected-mode audit

Main advanced from 3245617 to merge commit 798350f1d75319bcd5e5c7cb75207bf22222a346 through pull request 1. Hosted CI run 40341227263 at e5d83a5 passed 1,171 tests with 32 optional skips, installed commands, wheel/source-distribution builds and installations, Ruff formatting/lint, ty, and every Typst figure build. The optional hardware job was skipped. Independent review found a direct coarse-flag API error and a remaining historical-source fixture path dependency before publication; both were repaired. The integrated API/evaluator/suite gate passed 53 focused tests.

The separate composite audit is complete across all 64 score-selected modes. It combines 63 completed parallel rows with serial mode 48 after validating source/input identities, checkpoints, event chains, exact overlapping payloads, and complete returned hypotheses. The score-ranked winner remains mode 4 at objective 5.026904167180264, 11.127008 m geodesic error, and five of five correct associations. The second-ranked mode remains 853,439.780 m away with zero of four correct associations. The score winner and nearest mode happen to coincide; the evaluator reports both separately.

The audit reuses 12,150 exact branch-score checks from serial completed modes 0–59 and independently rescored 510 new exact poses from modes 60–63. It structurally retains 9,334 invalid-water outcomes and six solver-limit outcomes without claiming canonical replay of those non-exact results. These categories reconcile all 22,000 selected-mode branches. The result JSON SHA-256 is 080b61e160a18c60657bfc7e4394b0c57df742a597f841ab484045e96cd62917. A separate metadata evaluator accepts the composite as complete_audit while preserving the parallel producer as failed with mode 48 pending.

The canonical replay required explicit frozen-module loading. Three startup import/version failures produced no audit artifact and are documented with the successful ordered bootstrap command in reports/experiments/a35-cached-evaluation/parallel-composite-audit-report.md. The successful scorer and shared contracts came from frozen 0d34a17, with the assigned-refinement helper from its separately bound e2e8b879 source.

The updated compact and detailed Typst figures show rank 1 of 64 and retain both original execution stops. A combined audit does not supply a completed four-worker latency, a coast-wide certificate, a calibrated accepted fix, or a population accuracy rate. The source search still has 1,410,282 unknown or unvisited IDs. Calibration, validation and held-out search, all six scientific gates, clean complete-frame GPU timing, and final review remain pending.

Follow-up pull request 2 carries this audit and subsequent optimization experiments. A stronger feasible-incumbent pruning profile and a direct coarse-seed expansion comparison are being prepared under the same input hashes, objective, storage reserve, and benchmark lock. No improved timing is claimed before those runs complete.

## Main publication, storage recovery, and direct-coarse comparison

Main now includes both reviewed integration batches. Pull request 1 merged as 798350f after 1,171 tests passed with 32 optional skips. Pull request 2 merged as 52a29a6 after 1,186 tests passed with 32 optional skips. Both gates passed formatting, lint, type checking, package builds and installation, standalone entry points, and all Typst builds. Optional GPU jobs were not executed by these CI runs. The compact and detailed recovery figures are visible from main and retain the distinction between a complete 64-selected-mode composite audit and the interrupted producer runs.

Inactive work was archived to comech-1176 and verified before local removal. The measured physical-space increase during removal of 36 clean, pushed, inactive worktrees was 6,660,120,576 bytes. Active source, frozen references, datasets, and running outputs remain on comech-2080. The storage report retains manifests and restoration mappings.

The stronger-incumbent pre-line-of-sight experiment retained all 255 candidate dispositions and independently checked every proposed prune. It safely pruned 58 candidates and preserved the exact best and tie set over the sample plus external incumbent. Screening took 30.680 seconds versus 19.939 seconds for direct exact scoring including incumbent validation; independent post-prune verification added 2.853 seconds. This shared-host experiment remained slower, so it does not enable production screening or establish a latency target.

A new direct-coarse diagnostic uses each original A20 cached hypothesis, revalidates all three historical starts for provenance, and recomputes the eligible branch pool. It contains 24,419 planned branches versus 22,000 in the prior best-saved-start comparison. Its first attempt failed before dispatch because the loader returned a ModeRow dataclass rather than a dictionary. That failed attempt is retained. The corrected runner passed its production-row preflight and is executing within the original deadline and output cap. No accuracy result is claimed before finalization and independent evaluation.

The frozen nominal development inventory contains 3,600 range/type rows with 2,310 supported rows. All stored support and difficulty hashes verified; no difficulty files or required fields were missing. Supported geometry strata contain 2,058 rows with known-association major radius at most 25 m, 212 from 25 to 100 m, and 40 above 100 m. These are descriptive evaluator diagnostics, not search accuracy or new eligibility filters. Paired type-on/off rows are not independent physical scenes.

Coordinator checks for the integrated pruning and coarse-runner changes passed 49 focused tests, scoped Ruff formatting and lint, full-project ty, and git diff checks. The complete CI gate for this next publication batch remains pending. Held-out search, six-condition release decisions, clean one/two/four-GPU timings, and measured-bearing field validation remain pending.
