# Accuracy implementation progress on 26 October 2026

## Current outcome

The implementation is continuing on the accuracy/integration branch. Forty-five specialist roles have contributed, with the coordinator separate. The source, tests, figures, and reproducibility work are pushed in logical milestones. Release accuracy, complete-search latency, and measured-bearing field accuracy remain unvalidated.

The current development population is fixed before search. It contains 6,120 planned rows, including 3,600 nominal rows and twelve explicitly failed stress preparations. There are 600 nominal rows in each range and type condition. Supported counts are 269 at 1 km, 438 at 5 km, and 448 at 10 km for each type mode. Missing results, crashes, ambiguity, and timeouts remain in the supported denominator.

## Verified software progress

The broad integration checkpoint at 37f4c35 passed 623 tests, with 25 optional hardware tests skipped and seven subtests passed. Formatting, lint, type checking, wheel and source distribution builds, isolated installation, and all five installed command help checks passed. A subsequent integration checkpoint at c76be7c passed 102 focused proposal, controller, session, eligibility, and reporting tests.

Actual hardware checks passed the one-, two-, and four-device session selection matrix and 88 related tests without skips. The small four-device session fixture issued cost kernels only on devices zero and one because its batches were small. Separate runtime tests exercised one, two, and four workers. These checks establish tested numerical and state behavior, not four-device throughput.

The search now supports a reusable static-input session. Catalog and map parsing and projection occur once; each request validates its measurements, configuration, static input hashes, and coordinate system and creates a fresh scorer. Changed static inputs fail explicitly. One-shot execution remains available. Startup, request processing, and serialized-output timing are reported separately.

The formal controller now binds all six execution contexts and the frozen eligibility manifest before dispatch. A real search-to-evaluator-to-calibration-builder test passes. Original support files remain unchanged, and their optional derived support flag is verified against the source fields. Timeout cleanup terminates the owned process group before the controller advances, preventing orphaned searches from contaminating later cases.

## Performance experiments

Exact map construction fell from 11.148 and 11.361 seconds in the baseline runs to 4.562 and 4.600 seconds after removing redundant unions and preparing predicates. All 7,163 land parts were preserved. A batch of 20,000 classifications fell from 11.308 seconds to 0.195 seconds, with identical water, land, and unknown counts. The figure and raw plotting data are in docs/figures/a45-map-diagnostic-2026-10-26.

Catalog validation retained every check and identical canonical output. Its observed loading median changed only from 5.04 to 4.96 seconds. This small component difference does not explain the full search bottleneck.

A proposed pair-geometry cache did not help the measured first-64-proposal case and was rejected. A direct geodesic-polar replacement initially changed eligibility for catalog coordinates separated by less than a micrometre. The guarded implementation now retains the original coordinate-transform calculation near numerical decision boundaries. The real first-64 stream has identical ordered identities and statuses, with maximum candidate position difference 1.76e-8 metres and range difference 1.98e-8 metres. The guarded first-64-proposal timing fell from 5.5715 to 0.5696 seconds, a 9.78-fold component improvement. Frame construction fell from 664 calls to 37. These measurements cover a fixed proposal prefix and do not establish complete-search latency.

Initial full-coast pilot attempts timed out, and a separate assignment profiler produced no complete assignment-share estimate. Contaminated or interrupted attempts are retained with explicit status. Assignment acceleration remains deferred until a complete profile supports it. The new pilot is running from immutable source c76be7c and preserves all 80 planned cases. It uses method round-robin ordering and pauses after five searches for inspection. The shared host has external processor and graphics-device activity, so these timings are diagnostic. Clean performance claims require a separate uncontaminated run.

## Scene geometry and map coverage

The evaluator-only development summary validates all 3,600 nominal rows and their frozen input hashes. Among 2,310 supported rows, 2,058 have a known-association 95 percent ellipse major radius at most 25 metres, 212 are between 25 and 100 metres, and 40 exceed 100 metres. None is singular. Type-enabled and type-disabled rows are paired observations, not independent physical scenes. Median usable catalog-match counts are seven, 26, and 29 at 1, 5, and 10 km. These figures describe geometry assuming correct identities; they are not observed localization accuracy. The zoomable figure is available as [PDF](figures/development-geometry-2026-10-26.pdf), with its editable Typst source and JSON plotting data alongside it.

The versioned map package preserves the exact approved geometry. Known coverage is 76.260149 percent of the declared East Coast bounding box; 23.739851 percent remains explicitly unknown. Fine port geometry remains embedded within the broader coverage. The map coverage PDF and PNG are available under docs/figures/accuracy-map-coverage-2026-10-26.

## Remaining work and resource controls

Validation physical preparation has completed 390 of 2,040 initial rows. Each bounded wave is archived and verified before further preparation. The first 270-row wave shrank from about 7.5 GiB to 1.9 GiB after lossless compaction. New output remains subject to the 40 GiB free-space reserve. No held-out search has run.

Remaining gates are the full-search pilot, final source and hardware regression, complete development calibration and validation, frozen held-out evaluation, clean one-, two-, and four-device timing, final figures, and independent release review. The six accuracy conditions require at least 90 percent accepted-fix availability and 95 percent conditional accuracy within 100 metres. The 100 ms latency target is separate. A failed target will be reported with its evidence.

## Overnight audit trail

Completed milestones are pushed to their workstream branches and integrated in dependency order. Commit messages record the change, its purpose, validation commands and outcomes, measured comparisons, and remaining limitations. Rejected optimizations and failed runs remain available. The draft pull request stays open until the independent release review has a documented outcome.

Authored by J.C. Vaught.
