# Bearing-only maritime localization

Locate a boat from true-north object bearings, optional types, a catalog of known objects, and a land/water map. Search receives no boat position, target distances, or observation-to-catalog identities.

## Install and run

Use Python 3.11 and [uv](https://docs.astral.sh/uv/). Run from the repository root.

```text
uv sync --locked --all-groups
uv run python examples/simulate.py
uv run python examples/wedge.py
```

Every example loads the same complete East Coast catalog and vector map from `data/`. The compressed files unpack automatically into that same folder after checksum verification. No additional dataset download is required.

Edit the settings at the top of an example to choose an observer, visibility range, candidate budget, or method. Observer IDs refer to `data/observer_locations.csv`. The table records each location's current map status. B1 is in known water. Simulation rejects positions on land or outside known coverage.

## Examples

| Python file | Purpose |
|---|---|
| [simulate.py](examples/simulate.py) | Generate visible bearings and separate truth, without running search. |
| [grid.py](examples/grid.py) | Uniform grid search. |
| [hierarchical.py](examples/hierarchical.py) | Coarse-to-fine search. |
| [randomized.py](examples/randomized.py) | Seeded randomized search. |
| [pairwise.py](examples/pairwise.py) | Pairwise bearing-intersection proposals. |
| [wedge.py](examples/wedge.py) | Geodesic reverse-bearing proposals and seed refinement. |
| [branch_bound.py](examples/branch_bound.py) | Region subdivision with explicit unresolved-bound status. |
| [gpu.py](examples/gpu.py) | Grid candidate scoring using optional GPUs. |
| [compare_algorithms.py](examples/compare_algorithms.py) | Run methods on the same observations. |
| [compare_types.py](examples/compare_types.py) | Compare type labels enabled and disabled on identical bearings. |
| [compare_ranges.py](examples/compare_ranges.py) | Compare 1, 5 and 10 km visibility. |
| [compare_backends.py](examples/compare_backends.py) | Compare CPU and GPU requests sequentially. |
| [replay.py](examples/replay.py) | Search using saved bearings without running the simulator. |

The scripts share `examples/_data.py` for verified data loading and `examples/_common.py` for simulation, process handoff, and result reporting. They do not contain separate copies of the dataset. Small synthetic fixtures live under `tests/`.

## Outputs

Each search invocation writes a new timestamped folder under `runs/`. It contains measurements, separate truth, search settings, ranked results, a process log, and a summary. Comparison examples also write `comparison.csv` and `comparison.json`.

The summary reports the best hypothesis's geodesic position error separately from accepted-fix error. Ambiguity, failure, and budget exhaustion remain explicit. `search_process_ms` includes interpreter startup, full input loading, search, and output writing; data unpacking and simulation happen before that interval.

These are editable manual experiments. Default budgets do not complete a coast-wide search, and the 25 km grid is a coarse demonstration. The full catalog and declared search domain remain unchanged across examples. Range and land visibility limit detections, not the search domain. Dynamic-map branch-and-bound does not currently have a certified exclusion bound. An uncalibrated result may remain ambiguous.

## Data and software

All data files are directly under [data/](data/README.md). `objects.json.gz` and `map.json.gz` are the shared runtime inputs. Source CSVs and manifests retain provenance. `observer_locations.csv` is simulator/evaluator truth; search receives only bearings, catalog, map, and configuration.

Production code is in `bearing_localization/`, `maritime_localization_shared/`, and `maritime_localization_ingest/`. Tests are named by the behavior they check.

For GPU examples, use Linux with compatible NVIDIA hardware and install the optional extra.

```text
uv sync --locked --all-groups --extra gpu
uv run python examples/gpu.py
```

No benchmark or recurring job starts automatically.
