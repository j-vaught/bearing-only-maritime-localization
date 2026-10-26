# Accuracy implementation ledger

The implementation uses 40 bounded workstreams. All computation and environments remain on the server. The integration branch is accuracy/integration, starting from 32456174d6f7baea2c25bdfca6490f940b4214bf.

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
| A08 | ENC and map ingestion | Finishing ingestion, coverage manifests, and chart-only CatalogV2 export |
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
| A26 | Dataset and benchmark controller | Controller checks passed; repairing aggregate support-extension rule |
| A27 | GPU cost generation | Passed CPU and device-1 cost parity; integrated 3600ff6 |
| A28 | GPU proposals | Passed device-2 planar proposal parity; integrated 8695a64 |
| A29 | Assignment acceleration experiment | Assignment acceleration deferred by measured bottleneck rule; integrated 0917632 |
| A30 | Multiple-GPU runtime | Running multi-device implementation and hardware checks |
| A31 | Search telemetry | Passed telemetry and real GPU timing checks; integrated 25ab68e |
| A32 | CLI and main orchestration | Running standalone CLI integration |
| A33 | Contract and process integration | Awaiting prerequisites |
| A34 | Data and geometry integration | Awaiting prerequisites |
| A35 | Search integration | Passed search integration checks; integrated 718c11e |
| A36 | GPU correctness integration | Awaiting prerequisites |
| A37 | Independent regression and CI | Awaiting prerequisites |
| A38 | Performance and held-out evaluation | Awaiting prerequisites |
| A39 | Figures and reproducibility documentation | Awaiting prerequisites |
| A40 | Independent release review | Awaiting prerequisites |

## Evidence

No release accuracy or latency result has been produced by this implementation. The existing four GPUs have external memory allocations; timed trials require activity checks and an exclusive benchmark lock.

Authored by J.C. Vaught.

## Integrated check at 873ae88

The Python 3.11 CPU suite passed 312 tests with one optional GPU test skipped and seven subtests passed. Separate CuPy kernel checks ran successfully on devices 2 and 3. These checks do not establish localization accuracy or complete-search latency.

## Integration update

Thirty-three specialist agents have been dispatched. Accuracy and held-out evaluation remain pending. Device-level parity is established only for the documented cases; geographic proposal preparation remains on the CPU. The development assignment profile found proposal preparation dominant and assignment below the 20 percent acceleration threshold.
