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
| A08 | ENC and map ingestion | Running ingestion and source acquisition |
| A09 | Likelihood costs | Running likelihood implementation |
| A10 | Exact association | Awaiting prerequisites |
| A11 | Vector water and visibility | Passed vector geometry checks; integrated 84135c9 |
| A12 | Conservative map summaries | Running conservative summaries |
| A13 | Exact spatial queries | Passed Python 3.11 spatial checks; integrated 873ae88 |
| A14 | Predicted visible groups | Running candidate visibility and grouping |
| A15 | Simulator mismatch experiments | Awaiting prerequisites |
| A16 | Evaluation metrics | Running release metrics |
| A17 | Existing algorithm adapters | Awaiting prerequisites |
| A18 | Exhaustive reference search | Awaiting prerequisites |
| A19 | Pairwise proposals | Running pairwise proposal implementation |
| A20 | Continuous refinement | Awaiting prerequisites |
| A21 | Region lower bounds | Awaiting prerequisites |
| A22 | Branch-and-bound controller | Awaiting prerequisites |
| A23 | Reverse-wedge proposals | Awaiting prerequisites |
| A24 | Acceptance and ambiguity | Awaiting prerequisites |
| A25 | Scene difficulty | Awaiting prerequisites |
| A26 | Dataset and benchmark controller | Awaiting prerequisites |
| A27 | GPU cost generation | Awaiting prerequisites |
| A28 | GPU proposals | Awaiting prerequisites |
| A29 | Assignment acceleration experiment | Awaiting prerequisites |
| A30 | Multiple-GPU runtime | Awaiting prerequisites |
| A31 | Search telemetry | Passed telemetry and real GPU timing checks; integrated 25ab68e |
| A32 | CLI and main orchestration | Awaiting prerequisites |
| A33 | Contract and process integration | Awaiting prerequisites |
| A34 | Data and geometry integration | Awaiting prerequisites |
| A35 | Search integration | Awaiting prerequisites |
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
