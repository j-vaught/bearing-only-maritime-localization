# A33 geodesic wedge CLI integration

Branch: `accuracy/a33-wedge-geodesic`.

## Changes

Added the explicit `wedge_geodesic` search algorithm to the v2 CLI. The dispatch calls the geodesic proposal generator, preserves its matching-count/vote-count priority, applies the known-water preference only within identical priority tiers, and exact-scores the ordered positions under the configured candidate budget.

Telemetry reports separate `proposal_generation`, `proposal_ordering`, and `exact_scoring` stage durations. Generation diagnostics include source-pair, candidate-cell, geodesic-sample, failed/degenerate-sample, vote/matching histograms, solver residual, and witness-edge totals. For evaluated candidates, telemetry records the proposal ID and complete report/catalog witness graph. It does not serialize witness graphs for every generated cell.

The result's `elapsed_s` remains exact scoring plus refinement and excludes proposal generation/order. The request timing includes validation through serialized estimate output and is the complete request measure.

Any failed geodesic sample or source pair sets the final search status to `failed`, removes acceptance, and suppresses positive refined-mode evidence. Generation status and generation budget reason remain separately reported. Source or cell budget truncation remains `budget_exhausted`.

Added `SearchConfig.proposal_solver_tolerance_deg`, defaulting to `1e-7`. The CLI enforces the generator limit `min(0.001 degrees, 0.01 times the minimum effective observation sigma)`, passes the value to the generator, and records both declared tolerance and maximum observed residual. Other scoring and acceptance settings are unchanged.

## CLI/config contract

Run with `--schema-version 2 --algorithm wedge_geodesic`. The SearchConfig JSON supplies `algorithm`, `candidate_budget`, `proposal_source_pair_budget`, `proposal_cell_budget`, `proposal_chunk_size`, `proposal_solver_tolerance_deg`, `max_range_m`, and finite `bounds_xy`. The current diagnostic request uses `--wedge-cell-size-m 250 --wedge-tolerance-deg 0.5`, `candidate_budget=256`, `proposal_source_pair_budget=333340`, `proposal_cell_budget=5000000`, `proposal_chunk_size=4096`, and the default refinement settings of 8 modes, 50 iterations, and a 100 m initial step. No truth input is read by search.

The status fields are `diagnostics.proposal_generation.generation_status`, `generation_budget_reason`, `generation_complete`, and `unresolved_model_count`; `diagnostics.proposal_ordering.policy`; `diagnostics.exact_scoring.status/complete/candidate_count`; and final estimate `search_status`/`decision_status`. Serialized-output request timing is `request_timing.request_elapsed_s`.

## Validation

`uv sync --locked --all-groups` succeeded.

Ruff format/check and `ty check` passed for `accuracy_cli.py`, `search/config.py`, and `test_a33.py`. `git diff --check` passed.

Focused tests passed, 63 total: A33, A02, A04 reverse-bearing, A47 geodesic, and A43 geodesic tolerance tests.

The v2 CLI help exposes `wedge_geodesic`. A fixture request completed with complete generation, no failed samples, solver residual within the configured tolerance, stage timing records, and evaluated witness records. A source-budget truncation plus injected failed sample produced `budget_exhausted` generation, final `failed` search, no acceptance, and empty positive mode evidence.

No performance claim is made.

## Follow-up correction

The CLI now preserves reports with missing optional bearing sigma in all three wedge proposal algorithms. Geodesic proposals use `max(declared_sigma_or_generated_sigma, sigma_floor)` for every report; this same effective sigma vector enforces the solver-tolerance bound. Legacy `wedge` and `wedge_complete` now include every bearing report in source-pair generation.

The added regression runs all three algorithms with one missing sigma and a second sigma below the configured floor. All five reports participate, the geodesic source-pair total remains 30, the missing value maps to the generated sigma of 0.7 degrees, and the low declared value maps to the floor of 0.2 degrees. The focused regression passed.
