# Running and debugging

## Start with one process

Run the small example in the README first. The standalone search command is the easiest place to debug because it does not launch the simulator or evaluator.

The same command can run under Python's debugger.

```text
uv run python -m pdb -m bearing_localization.search_cli --schema-version 2 --measurements runs/tiny/measurements.json --catalog examples/tiny/catalog.json --map examples/tiny/map.json --config examples/tiny/search-config.json --projected-crs EPSG:32618 --algorithm grid --spacing-m 100 --no-refine --output runs/tiny/result.json
```

At the debugger prompt, use `!import bearing_localization.accuracy_cli`, then `b bearing_localization.accuracy_cli.search_v2_files` and `c`. Inside the function, `n` advances one statement, `s` enters a call, and `p variable_name` prints a value. An editor debugger can launch the same module with the same arguments and the repository root as its working directory.

## Where to set breakpoints

| Component | Source |
|---|---|
| Load inputs and choose search method | `bearing_localization/accuracy_cli.py`, `search_v2_files`. |
| Complete candidate objective | `bearing_localization/search/scoring.py`. |
| Range, land visibility and merged detections | `bearing_localization/search/prediction.py`. |
| Bearing/type likelihood | `bearing_localization/search/likelihood.py`. |
| One-to-one matching, unmatched reports and missed objects | `bearing_localization/search/assignment.py`. |
| Additional association hypotheses | `bearing_localization/search/assignment_expansion.py`. |
| Continuous position refinement | `bearing_localization/search/refinement.py`. |
| Map queries and exact visibility | `maritime_localization_shared/geospatial/vector_map.py`. |
| Simulated measurement generation | `bearing_localization/simulator/runner.py`. |
| Result status and acceptance | `bearing_localization/search/decision.py`. |

## Search methods and options

Run `uv run bearing-localization-search --schema-version 2 --help` for the complete interface. Available methods include `grid`, `reference_grid`, `hierarchical`, `randomized`, `pairwise`, `branch_bound`, `wedge`, `wedge_complete`, `wedge_geodesic` and `wedge_geodesic_seeded`.

For the tiny case, replace `--algorithm grid` with `--algorithm hierarchical` or `--algorithm pairwise`. Remove `--no-refine` to allow continuous refinement. Use `--expand-assignments-from-coarse` to explore additional object associations directly from coarse hypotheses. That path is experimental and can be much slower. Its limits are `--expansion-max-branches`, `--expansion-max-nfev` and `--request-timeout-s`. The request timeout is associated with that expansion path; it is not a universal external watchdog for all CLI loading and proposal work.

The configuration's `candidate_budget` limits candidate scoring. It does not guarantee a particular elapsed time or complete coverage of the search domain. The CLI algorithm option overrides the algorithm in the configuration.

## Real East Coast inputs

The repository retains the current normalized catalog and map, their source manifests, raw selected AIS/chart-aid tables, and saved example bearings. No server-specific environment or private output directory is needed.

Unpack the catalog and map with the Python helper. It verifies both compressed and uncompressed hashes and preserves the original archives.

```text
uv run python examples/unpack_east_coast.py
```

Then run the full-domain example explicitly.

```text
uv run bearing-localization-search --schema-version 2 --measurements examples/east-coast/measurements.json --catalog runs/east-coast/catalog.json --map runs/east-coast/map.json --config examples/east-coast/search-config.json --scope-manifest examples/east-coast/scope-manifest.json --projected-crs EPSG:32618 --algorithm wedge_geodesic_seeded --no-refine --output runs/east-coast/result.json
```

This is a large search and can take minutes or longer. The supplied budget does not complete the coast-wide search. The bearings are simulated; the vessel catalog and chart features come from real data. The map explicitly retains unknown coverage, approximately 23.7 percent of the declared region. The catalog contains 2,268 selected AIS rows and 64,400 chart-aid components; many chart components have unresolved physical identities. Source times and identities are retained in the inputs.

The real example intentionally contains estimator inputs only. Its old evaluator truth used a different projected CRS, so it must not be compared directly to EPSG:32618 results. The original truth, conversion audit and archived results remain in Git history at `a8ed127`.

## Understanding the output

`hypotheses` contains ranked locations, assignments and objective components. Lower objective values are better under the selected objective; they are not calibrated probabilities. The best returned hypothesis can still be wrong.

`decision_status` distinguishes `accepted`, `ambiguous` and `no_fix`. `search_status` distinguishes `completed`, `budget_exhausted` and `failed`. `certificate_scope` records what optimality claim, if any, is supported. Completing a heuristic does not certify the whole coast. Do not convert an ambiguous result into an accepted fix merely to obtain an accuracy statistic.

The evaluator preserves those distinctions. Its accepted-fix error can be null for an ambiguous result. For debugging the tiny case, inspect the first hypothesis and the simulator truth directly in the debugger; both use EPSG:32618. Keep truth out of the search invocation.

## Optional GPU

CPU execution is the default. On Linux with CUDA 12-compatible hardware, install the optional dependencies.

```text
uv sync --locked --all-groups --extra gpu
```

Set `backend` to `"gpu"` in your search configuration and supply `--device-id 0`. Repeat the device option for supported multiple-device execution. GPU support depends on the chosen algorithm and geometry; some geographic proposal paths remain CPU-only. The CPU scorer remains the reference for exact results.

## Repository scope

The current tree keeps software, current inputs, examples and core tests. Publication figures, chronological agent logs, cached outputs, formal benchmark automation, one-off profiling/audit runners and shell scripts were removed from the checkout. Their committed history is preserved. Core solver behavior and acceptance thresholds were not changed by this cleanup.

All recurring implementation work remains paused. These commands run only when you invoke them.
