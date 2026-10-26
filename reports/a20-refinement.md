# A20 continuous multi-mode refinement

Status: passed on `accuracy/a20`.

## Callable handoff

`refine_modes(measurements, catalog, vector_map, config, modes, *, initial_step_m=100.0, max_iterations=50) -> RefinementResult` accepts A18 `SpatialMode` values or `Hypothesis` seeds. `modes` can be a generator, list, or tuple. Score-ranked candidate outputs can be converted to hypotheses using each candidate's projected east/north coordinates and complete objective score. A20 re-scores every seed before refining, so the seed score does not substitute for exact candidate evaluation. `config` must match the full-coast scoring objective, range, type-label setting, and bounds; the current refinement scorer executes on CPU in FP64.

The caller owns score-only spatial mode extraction and selection. For the frozen 250 m candidate grid, form deterministic eight-neighbor local minima from complete objective-score rows, order those minima by objective and then east/north coordinates, choose an explicitly recorded top-$M$ budget, and pass all $M$ seeds to `refine_modes`. A20 imposes no implicit candidate or mode cap: it refines every supplied seed and returns the number in `RefinementResult.modes`. Report the grid/neighbor rule, tie handling, and chosen $M$ with downstream results. Do not use evaluator information to select seeds. The result is a set of locally refined alternatives and does not establish a global continuous optimum or authorize acceptance.

Each trial recomputes the range-limited, line-of-sight-visible and angularly merged predicted groups and solves the complete configured assignment through `CandidateScorer`. Only a strict objective decrease moves a pose; equal-cost trials preserve the current position and assignments are fully recomputed when they switch. Refinement honors inclusive configured bounds, water status, and unknown visibility coverage. Its limits are at most 50 iterations, relative objective change below `1e-8`, and displacement below `0.01 m`.

`RefinedMode` returns the final hypothesis, assignments, iteration history, iteration count, stop reason, whether the hypothesis improved, uncertainty rank and singularity information, and the estimated 95% major ellipse radius when defined. The conditional ellipse uses fixed final estimated assignments, WGS84-bearing finite-difference Jacobians, and per-observation plus catalog-position variance. Angular extent does not enter the variance. Rank-deficient or unresolved geometry returns no ellipse and a singular reason. Common-bearing bias remains unsupported and is not represented as a global uncertainty certificate.

## Current validation

The coarse-cell synthetic recovery starts exactly 250 m east and 250 m south of the known synthetic pose and uses a 250 m initial search step. With four measurements and mixed sigmas, exact scoring improves from `33.754756497777` to `1.951945637304`, reduces position error from `353.553391 m` to `2.021833 m`, and stops after 29 iterations at the `0.01 m` displacement threshold. The final four-assignment score equals a direct `CandidateScorer` evaluation. Its conditional geometry has rank two and estimated 95% major radius `32.726290 m`.

The six A20 tests also cover score-driven reassignment switches, exact score parity, shore and unknown-coverage rejection, equal-score position stability, separate refinement of multiple wrong-basin seeds, nonincreasing objective history, and singular one-bearing uncertainty. Validation passed with the locked Python 3.11 environment, scoped Ruff formatting and lint, scoped `ty`, `git diff --check`, and 66 tests across A20, A10, A14, A17, A18, association, visibility, and grid regressions.

Owned implementation and tests are `bearing_localization/search/refinement.py` and `tests/accuracy/test_a20.py`. This report records the standalone A20 API and its caller-owned mode budget; it does not claim that a coast-wide scoring run has completed.
