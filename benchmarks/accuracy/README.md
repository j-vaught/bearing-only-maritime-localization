# Accuracy suite

The controller builds the pinned port splits, plans independent physical trials, prepares v2 measurements through the simulator and pre-search eligibility files through the evaluator, and runs explicitly configured search and evaluation commands. Failed or timed-out preparation and command attempts stay in per-split tables and logs. A rerun resumes completed work and only retries failed search rows when `--retry-failures` is supplied.

The catalog at `data/sites/east-coast-major-ports-top150-2024.csv` pins B1 through B24 to `port-001` through `port-024`; legacy B1 through B5 are retained separately. The split builder joins port neighborhoods whose anchors are within 3 km plus a 10 km search footprint using a conservative 26 km threshold, then moves each connected component wholly into development. `splits.json` records source and effective assignments and the decisions. The initial design contains 25 placements per port within 3 km of its anchor and two deterministic realizations. Each physical seed depends on split, port, placement, realization, and visibility range, and excludes algorithm and report-label mode. Nominal trials cover 1 km, 5 km, and 10 km with paired report-type labels. One-factor stress profiles use placements 1 through 5 and realization 0. Support-driven expansion waits until every nominal support record for the split exists, then counts distinct physical placement-realizations across the split for each range and report-label gate. If a gate has fewer than 100 supported trials, the controller adds one deterministic batch of 25 placements to the next eligible port, prepares that batch, and reevaluates support. Batches continue only until every gate reaches 100 or all ports reach 100 placements.

Placement sampling imposes no minimum separation beyond the pinned count and water/radius constraints. Sampling must produce the full requested placement count; failures are recorded in `placement-sampling-attempts.csv` and per-trial preparation attempts. The A08 MapV2 files in `.agent-work/cache/maps` are reference geometry for sampling water locations and computing support. The simulator sees the full CatalogV2 and filters physical visibility by each trial's declared range. Search must receive the separately supplied full-coast CatalogV2 and full-coast MapV2 so no port-local oracle narrows its domain.

## Standalone preparation

Run these commands from the repository root with the project's configured environment. The preparation command writes separate measurement, truth, diagnostics, support, difficulty, and physical-identity audit files beneath the input root. Only measurements and full-coast search inputs are sent to the search process.

```sh
uv run python tools/run_accuracy_suite.py splits --output benchmarks/accuracy/splits.json
uv run python tools/run_accuracy_suite.py plan --output-dir benchmarks/accuracy --split-manifest benchmarks/accuracy/splits.json
uv run python tools/run_accuracy_suite.py prepare --plan benchmarks/accuracy/config.json --catalog /absolute/path/to/full-coast-catalog-v2.json --map-cache .agent-work/cache/maps --input-root benchmarks/accuracy/inputs --split development
uv run python tools/run_accuracy_suite.py prepare --plan benchmarks/accuracy/config.json --catalog /absolute/path/to/full-coast-catalog-v2.json --map-cache .agent-work/cache/maps --input-root benchmarks/accuracy/inputs --split validation
```

Preparation requires the A08 reference file `noaa-enc-port-BN-15km.map-v2.json` for each mapped port. Catalog input must be the complete coast catalog, not a port-filtered subset.

## Search and evaluator invocation contract

Copy `commands.template.json` to a local `commands.json`, then replace its three full-coast input paths and the frozen search configuration path. Its argv arrays are the A32 v2 contract. The controller writes a run-level scope manifest containing the full-coast declaration, catalog and map hashes, configured projected CRS, and exact projected bounds; the search command receives this manifest at `{search_scope_manifest_path}`. It receives `LocalizationResult` output at `{estimate_path}`. The evaluator consumes that estimate with `{truth_path}` and pre-search `{support_path}` and writes evaluation output to `{evaluation_path}`. Search argv must not include truth, diagnostics, support, or difficulty paths. `search_domain_scope` and `search_map_scope` must remain `full_coast`.

```sh
cp benchmarks/accuracy/commands.template.json benchmarks/accuracy/commands.json
uv run python tools/run_accuracy_suite.py run --plan benchmarks/accuracy/config.json --commands benchmarks/accuracy/commands.json --input-root benchmarks/accuracy/inputs --run-root benchmarks/accuracy/runs --split development --timeout-s 1800
uv run python tools/run_accuracy_suite.py run --plan benchmarks/accuracy/config.json --commands benchmarks/accuracy/commands.json --input-root benchmarks/accuracy/inputs --run-root benchmarks/accuracy/runs --split validation --timeout-s 1800
```

Held-out trials remain sealed unless both a frozen release manifest declaring `authorized_by` as `A38` and `freeze_complete` as true, and the explicit `--authorize-a38-release` flag are supplied. The runner records source commit, command configuration, catalog, map, search configuration, simulator outputs, attempts, status, timeout, return code, and logs for audit and resume.
