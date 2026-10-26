# Bearing-only maritime localization

Locate a boat from true-north object bearings, optional object types, a catalog of known objects, and a land/water map. The estimator receives no boat position, target distances, or observation-to-catalog identities.

## Install

Use Python 3.11 and [uv](https://docs.astral.sh/uv/). Run commands from the repository root.

```text
uv sync --locked --all-groups
```

## Run the small example

The editable example has three vessels in synthetic open water. It uses a 200 m square search region so you can step through the complete program quickly.

Generate separate measurement and truth files.

```text
uv run bearing-simulator --schema-version 2 --fixture examples/tiny/scene.json --projected-crs EPSG:32618 --measurements-out runs/tiny/measurements.json --truth-out runs/tiny/truth.json --diagnostics-out runs/tiny/simulation.json --seed 11 --trial-id tiny
```

Search using measurements, catalog, map and configuration.

```text
uv run bearing-localization-search --schema-version 2 --measurements runs/tiny/measurements.json --catalog examples/tiny/catalog.json --map examples/tiny/map.json --config examples/tiny/search-config.json --projected-crs EPSG:32618 --algorithm grid --spacing-m 100 --no-refine --output runs/tiny/result.json
```

Evaluate the saved result separately.

```text
uv run bearing-localization-evaluate --schema-version 2 --estimate runs/tiny/result.json --truth runs/tiny/truth.json --support examples/tiny/support.json --coordinate-crs EPSG:32618 --output runs/tiny/evaluation.json
```

Read ranked positions and assignments in `runs/tiny/result.json`. Stage timings and diagnostics are written alongside it as `result.json.telemetry.json`. Without a calibrated acceptance rule, a returned location can have `decision_status="ambiguous"`. This is distinct from a failed search.

## Debug and change inputs

Edit `examples/tiny/scene.json` to change the simulator's observer, objects, noise or visibility. Its catalog and map are embedded so simulation is self-contained. If you change those objects or geometry, make the same changes in the separate `catalog.json` and `map.json` supplied to search.

Edit `examples/tiny/search-config.json` to change the search bounds, range, type policy or candidate budget. All projected search coordinates use EPSG:32618. Bearings are degrees clockwise from true north.


## Code

| Path | Responsibility |
|---|---|
| `bearing_localization/simulator/` | Simulated detections and separate truth output. |
| `bearing_localization/search/` | Candidate generation, visibility, association, scoring and refinement. |
| `bearing_localization/accuracy_cli.py` | Version-2 file loading and program orchestration. |
| `bearing_localization/evaluation/` | Truth-based measurements after search. |
| `maritime_localization_shared/` | Contracts, serialization, coordinates and geometry. |
| `maritime_localization_ingest/` | AIS, measurement and chart normalization. |
| `main.py` | Optional search/evaluation subprocess orchestration. |
| `data/` | Current real inputs and source manifests. |
| `examples/` | Small editable case and saved East Coast measurements. |
| `tests/` | Core software regression tests. |

## Checks

```text
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
uv run ty check .
```

The code retains both contract versions. Use `--schema-version 2` for the current search path. Search accuracy and complete coast-wide latency remain experimental. Removed reports, plots and experiment runners remain available in Git history at `a8ed127`.
