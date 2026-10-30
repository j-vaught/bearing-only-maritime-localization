# Bearing-only maritime localization

J.C. Vaught.

Locate a vessel from true-north charting-aid bearings across the eastern seaboard. The GPU search uses land blocking, distinct-marker pruning, coarse-to-fine refinement, and dynamic scoring across available GPUs. Search receives bearings and known objects, without the vessel's true position or object associations.

The repository includes the complete runtime map, AIS and charting-aid catalog, sensor configuration, and 20 eligible test locations numbered B1–B20. Each location has at least 16 detected charting aids under the supplied sensor configuration. Charleston is B14. The map archive has three checksum-verified parts that are assembled automatically. Inputs are expanded and verified automatically.

Use Linux, Python 3.11, compatible NVIDIA GPUs, and `uv`. Run these commands from the repository root. Choose a storage directory with room for the unpacked inputs and reusable spatial index.

```sh
uv sync --locked --extra gpu
uv run bearing-coast prepare --storage /path/to/storage
uv run bearing-coast sites
uv run bearing-coast run --site B14 --devices 0,1,2 --storage /path/to/storage
```

Preparation runs on the CPU once and saves a reusable map/object index. Search queries, visibility checks, matching, and refinement run on the selected GPUs. CPU work handles simulation, file loading, scheduling, and post-search evaluation. Maps remain loaded across sites and repeats within one invocation. Candidate transfers use pinned host staging and have a fixed traffic budget.

```sh
uv run bearing-coast run --all --devices 0,1,2 --storage /path/to/storage
uv run bearing-coast run --site B14 --repeats 2 --devices 0,1,2 --storage /path/to/storage
uv run bearing-coast search --measurements measurements.json --devices 0,1,2 --storage /path/to/storage
```

Results are written to a new directory under the storage directory's `runs/`. Simulated runs include measurements, a separate truth file, a frozen estimate, WGS84 error in meters, and timing tables in JSON and CSV. External-measurement search does not load simulator truth. Use `--output` to choose a different destination and `--help` to inspect command options.

`config/search.json` controls the search domain and resolution. `config/sensor.json` controls the 10 km sensor range, missed detections, vessel-only extra detections, and bearing noise bounded by ±0.5°. `data/sources.json` records input checksums and provenance. Unknown map coverage remains unknown. Tests run with `uv run pytest -q`.
