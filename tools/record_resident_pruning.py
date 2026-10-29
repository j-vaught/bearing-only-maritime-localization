"""Record every GPU pruning decision using saved caches, without terminal scoring.

Pass the ordinary cached resident CLI arguments, including explicit --execute.
The original engine supplies identical traversal, subdivision and predicates.
Only the output observer and terminal score operation are replaced.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from maritime.search.gpu.resident import engine
from maritime.search.gpu.resident.optimized import OptimizedResidentDevice
from tools.run_cached_resident_only import main as cached_main

_original_run = engine.run_resident_search


def recorded_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
    devices: list[Any] = []

    class RecordingDevice(OptimizedResidentDevice):
        def __init__(self, *device_args: Any) -> None:
            super().__init__(*device_args)
            self.trace_cells: list[np.ndarray] = []
            self.trace_states: list[np.ndarray] = []
            devices.append(self)

        def prune(self, cells: Any, bearings: Any, tolerances: Any) -> Any:
            states = super().prune(cells, bearings, tolerances)
            # Copies observe the decision; no host map/bearing predicate is used.
            self.trace_cells.append(self.cp.asnumpy(cells))
            self.trace_states.append(self.cp.asnumpy(states))
            return states

        def score(self, cells: Any, bearings: Any, tolerances: Any) -> dict[str, Any]:
            # Pruning visualization needs terminal rectangles, not new pose scores.
            return {"cells": cells}

    kwargs["device_factory"] = RecordingDevice
    result = _original_run(*args, **kwargs)
    output = Path(args[4])
    cells = np.concatenate([np.concatenate(d.trace_cells) for d in devices])
    states = np.concatenate([np.concatenate(d.trace_states) for d in devices])
    gpu_ids = np.concatenate(
        [np.full(sum(map(len, d.trace_states)), d.device, dtype=np.int8) for d in devices]
    )
    config = args[1]
    widths = np.max(cells[:, 2:] - cells[:, :2], axis=1)
    # The trace stores exact cells. Infer scale only for presentation grouping.
    depth = np.floor(np.log2(config.initial_size_m / widths) + 1e-7).astype(np.int8)
    manifest = {
        "schema": "ResidentPruningTraceV1",
        "cell_columns": ["xmin", "ymin", "xmax", "ymax"],
        "crs": "EPSG:32618",
        "terminal_scoring_executed": False,
        "decision_execution": "existing optimized GPU predicate",
        "state_codes": {
            "0": "retained",
            "1": "entire_cell_known_land",
            "2": "at_least_one_positive_marker_has_no_possible_match",
            "3": "too_few_distinct_possible_markers",
        },
        "depth_semantics": "dyadic cell scale; clipped edge roots may be narrower",
        "summary": result,
    }
    with (output / "pruning-trace.npz").open("wb") as stream:
        np.savez_compressed(
            stream,
            cells=cells,
            state=states,
            depth=depth,
            gpu=gpu_ids,
            manifest_utf8=np.frombuffer(json.dumps(manifest).encode(), dtype=np.uint8),
        )
    (output / "trace-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return result


if __name__ == "__main__":
    setattr(engine, "run_resident_search", recorded_run)
    cached_main()
