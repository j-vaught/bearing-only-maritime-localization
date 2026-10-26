# Accuracy implementation ledger

The implementation uses the original 40 bounded workstreams and five additional specialist workstreams. All computation and environments remain on the server. The integration branch is accuracy/integration, starting from 32456174d6f7baea2c25bdfca6490f940b4214bf.

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
