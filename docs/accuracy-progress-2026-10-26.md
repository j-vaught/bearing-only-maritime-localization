# Accuracy implementation progress on 26 October 2026

## Current outcome

The implementation is continuing on the accuracy/integration branch. Forty-seven specialist roles have contributed, with the coordinator separate. The source, tests, figures, and reproducibility work are pushed in logical milestones. Release accuracy, complete-search latency, and measured-bearing field accuracy remain unvalidated.

The current development population is fixed before search. It contains 6,120 planned rows, including 3,600 nominal rows and twelve explicitly failed stress preparations. There are 600 nominal rows in each range and type condition. Supported counts are 269 at 1 km, 438 at 5 km, and 448 at 10 km for each type mode. Missing results, crashes, ambiguity, and timeouts remain in the supported denominator.

## Verified software progress

The broad integration checkpoint at 37f4c35 passed 623 tests, with 25 optional hardware tests skipped and seven subtests passed. Formatting, lint, type checking, wheel and source distribution builds, isolated installation, and all five installed command help checks passed. A subsequent integration checkpoint at c76be7c passed 102 focused proposal, controller, session, eligibility, and reporting tests.

Actual hardware checks passed the one-, two-, and four-device session selection matrix and 88 related tests without skips. The small four-device session fixture issued cost kernels only on devices zero and one because its batches were small. Separate runtime tests exercised one, two, and four workers. These checks establish tested numerical and state behavior, not four-device throughput.

The search now supports a reusable static-input session. Catalog and map parsing and projection occur once; each request validates its measurements, configuration, static input hashes, and coordinate system and creates a fresh scorer. Changed static inputs fail explicitly. One-shot execution remains available. Startup, request processing, and serialized-output timing are reported separately.

The formal controller now binds all six execution contexts and the frozen eligibility manifest before dispatch. A real search-to-evaluator-to-calibration-builder test passed before the timeout-protocol extension. The protocol now binds the actual external deadline and process-group termination policy across run identity and calibration provenance. The consumer propagation failure found during integration has been repaired. The combined calibration, runner, pilot, session, and metrics regression now passes 116 tests, including synthesized missing trial rows. Original support files remain unchanged, and their optional derived support flag is verified against the source fields. Timeout cleanup terminates the owned process group before the controller advances, preventing orphaned searches from contaminating later cases.

## Performance experiments

Exact map construction fell from 11.148 and 11.361 seconds in the baseline runs to 4.562 and 4.600 seconds after removing redundant unions and preparing predicates. All 7,163 land parts were preserved. A batch of 20,000 classifications fell from 11.308 seconds to 0.195 seconds, with identical water, land, and unknown counts. The figure and raw plotting data are in docs/figures/a45-map-diagnostic-2026-10-26.

Catalog validation retained every check and identical canonical output. Its observed loading median changed only from 5.04 to 4.96 seconds. This small component difference does not explain the full search bottleneck.

A proposed pair-geometry cache did not help the measured first-64-proposal case and was rejected. A direct geodesic-polar replacement initially changed eligibility for catalog coordinates separated by less than a micrometre. The guarded implementation now retains the original coordinate-transform calculation near numerical decision boundaries. The real first-64 stream has identical ordered identities and statuses, with maximum candidate position difference 1.76e-8 metres and range difference 1.98e-8 metres. The guarded first-64-proposal timing fell from 5.5715 to 0.5696 seconds, a 9.78-fold component improvement. Frame construction fell from 664 calls to 37. These measurements cover a fixed proposal prefix and do not establish complete-search latency.

Initial full-coast pilot attempts timed out, and a separate assignment profiler produced no complete assignment-share estimate. Contaminated or interrupted attempts are retained with explicit status. Assignment acceleration remains deferred until a complete profile supports it. The new pilot reached its five-search inspection checkpoint from immutable source c76be7c. All 80 planned calls remain recorded, with 75 not yet run. Pairwise and wedge each reached the 300-second watchdog. Hierarchical, grid, and reference grid returned in 15.272, 15.284, and 15.949 seconds respectively, but each evaluated only 64 unknown-coverage candidates, retained no scored hypotheses, and reported budget exhaustion. No useful location or accuracy comparison was produced. The detailed checkpoint is in [the pilot report](../reports/a26-pilot-c76be7c.md). The shared host has external processor and graphics-device activity, so these timings are diagnostic. Clean performance claims require a separate uncontaminated run.

A subsequent 30-second profile located the expensive observed work after proposal generation. Pairwise generation consumed 0.617 seconds in that partial window, while five visibility predictions issued 909 line-of-sight queries and 2,108 constructive geometry intersections. The watchdog stopped profiling before a result was available. Exact shoreline visibility is therefore the next measured optimization target; its result must agree with the original geometry calculation.

## Matched regression and repair checkpoint

The preserved version-one search was rerun from both main commit 3245617 and integration commit 966f80b using identical inputs. The local B1 control returned the same location and 10.3346 m error from both commits. The full-coast blind B1 control returned the same location and 89.091 m error from both commits. These matched cases show that the existing path still reproduces. Their single-run elapsed times were 4.796 versus 5.636 seconds locally and 28.269 versus 30.128 seconds coast-wide. They are diagnostic timings under shared-host activity. The commands, inputs, and results are retained in reports/legacy-to-v2-regression.md.

The new version-two path has different inputs, observation semantics, geometry, objective, and budgets. Its failed pilot cannot establish a matched accuracy regression against those controls. It does establish an incomplete search that requires repair.

Exact batched shoreline visibility now clips the original geometry to an expanded envelope around each candidate's rays. It does not simplify the geometry. All 909 saved ray classifications match the original scalar calculation, including 635 blocked and 274 clear rays. The fixed-ray median changed from 10.6785 to 0.1697 seconds, approximately 63 times faster, including crop construction. The same pairwise case that previously reached 300 seconds now finishes in 48 seconds. Its first hypothesis is still 391,446.9 m wrong, with no correct associations, and its status remains ambiguous and budget_exhausted. Faster visibility resolves the measured runtime blockage but does not repair the omitted location hypotheses.

The capped wedge pilot processed only 87 of 333,340 observation-to-catalog source pairs before its 64-cell storage limit stopped generation. A new explicit complete-voting implementation separates source coverage and accumulator storage from the later exact-scoring budget. On the same five observations and 66,668-record catalog, complete voting with coarse 10 km proposal cells processed all 333,340 pairs and produced 3,678 cells in 7.452 seconds. This component measurement excludes map evaluation and exact scoring. The next frozen-input diagnostic uses 250 m proposal cells and 256 exact-scoring candidates; no position-error result is available yet.

Integration commit 07d8036 exposes the complete-voting path as wedge_complete. It preserves vote priority and orders equal-vote cells across the known-water domain before exact scoring. Source or scoring exhaustion remains explicit. Scoped lint and type checks passed, and 54 integration tests passed across configuration, program handoffs, wedge coverage, and candidate ordering. The new run retains the same map, catalog, measurement set, and complete scoring objective. The original 80-call pilot remains archived with its 75 unrun calls.

## Scene geometry and map coverage

The evaluator-only development summary validates all 3,600 nominal rows and their frozen input hashes. Among 2,310 supported rows, 2,058 have a known-association 95 percent ellipse major radius at most 25 metres, 212 are between 25 and 100 metres, and 40 exceed 100 metres. None is singular. Type-enabled and type-disabled rows are paired observations, not independent physical scenes. Median usable catalog-match counts are seven, 26, and 29 at 1, 5, and 10 km. These figures describe geometry assuming correct identities; they are not observed localization accuracy. The zoomable figure is available as [PDF](figures/development-geometry-2026-10-26.pdf), with its editable Typst source and JSON plotting data alongside it.

The versioned map package preserves the exact approved geometry. Known coverage is 76.260149 percent of the declared East Coast bounding box; 23.739851 percent remains explicitly unknown. Fine port geometry remains embedded within the broader coverage. The map coverage PDF and PNG are available under docs/figures/accuracy-map-coverage-2026-10-26.

## Remaining work and resource controls

Validation physical preparation has completed 570 of 2,040 initial rows. Preparation is paused during the next exclusive diagnostic run. Each bounded wave is archived and verified before further preparation. The first 270-row wave shrank from about 7.5 GiB to 1.9 GiB after lossless compaction. New output remains subject to the 40 GiB free-space reserve. No held-out search has run.

Remaining gates are the full-search pilot, final source and hardware regression, complete development calibration and validation, frozen held-out evaluation, clean one-, two-, and four-device timing, final figures, and independent release review. The six accuracy conditions require at least 90 percent accepted-fix availability and 95 percent conditional accuracy within 100 metres. The 100 ms latency target is separate. A failed target will be reported with its evidence.

## Overnight audit trail

Completed milestones are pushed to their workstream branches and integrated in dependency order. Commit messages record the change, its purpose, validation commands and outcomes, measured comparisons, and remaining limitations. Rejected optimizations and failed runs remain available. The draft pull request stays open until the independent release review has a documented outcome.

Authored by J.C. Vaught.
