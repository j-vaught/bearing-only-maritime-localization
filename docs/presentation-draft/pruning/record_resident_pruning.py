"""Observe every current GPU pruning decision, without terminal scoring.

Use the ordinary cached CLI with --execute --optimized and a new output folder.
Synchronous host copies and recording overhead change replay timing and worker
dispatch. This is an instrumented observation, never a performance benchmark.
No host geometry, bearing, visibility, or matching predicate is evaluated.
"""

from __future__ import annotations

import json
import sys
from collections import deque
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any

import numpy as np

from maritime.search.gpu.resident import engine
from maritime.search.gpu.resident.distinct_pruning import prune_distinct_chart_geometry
from maritime.search.gpu.resident.optimized import OptimizedResidentDevice
from tools.run_cached_resident_only import main as cached_main

_original_run = engine.run_resident_search


def _key(cell: np.ndarray) -> bytes:
    return np.asarray(cell, dtype=np.float64).tobytes()


def _hierarchy(cells: np.ndarray, states: np.ndarray, roots: list[dict[str, Any]]):
    """Recover mechanical lineage from exact observed roots/rectangles only."""
    size = len(cells)
    parent = np.full(size, -1, dtype=np.int64)
    root_id = np.full(size, -1, dtype=np.int64)
    depth = np.full(size, -1, dtype=np.int16)
    lookup: dict[bytes, int] = {}
    errors: list[str] = []
    for row, cell in enumerate(cells):
        key = _key(cell)
        if key in lookup:
            errors.append(f"Repeated observed rectangle at rows {lookup[key]} and {row}")
        lookup[key] = row
    root_rows: dict[int, int] = {}
    for record in roots:
        for offset, cell in enumerate(record["cells"]):
            identifier = record["begin"] + offset
            row = lookup.get(_key(cell))
            if row is not None:
                if identifier in root_rows:
                    errors.append(f"Repeated observed seed root {identifier}")
                root_rows[identifier] = row
                root_id[row], depth[row] = identifier, 0
    children: list[list[int]] = [[] for _ in range(size)]
    for row, cell in enumerate(cells):
        x0, y0, x1, y1 = map(float, cell)
        mx, my = (x0 + x1) * 0.5, (y0 + y1) * 0.5
        # Identical four-child coordinate construction to the GPU split.
        boxes = ((x0, y0, mx, my), (mx, y0, x1, my), (x0, my, mx, y1), (mx, my, x1, y1))
        for box in boxes:
            child = lookup.get(_key(np.asarray(box)))
            if child is None or child == row:
                continue
            if parent[child] >= 0 and parent[child] != row:
                errors.append(f"Multiple parents for observed row {child}")
            if states[row] != 0:
                errors.append(f"Rejected row {row} has an observed descendant")
            parent[child] = row
            children[row].append(child)
    pending = deque(root_rows.values())
    visited: set[int] = set()
    while pending:
        row = pending.popleft()
        if row in visited:
            errors.append(f"Repeated hierarchy visit at row {row}")
            continue
        visited.add(row)
        for child in children[row]:
            root_id[child] = root_id[row]
            depth[child] = depth[row] + 1
            pending.append(child)
    missing = np.flatnonzero((root_id < 0) | (depth < 0))
    if len(missing):
        errors.append(f"{len(missing)} observed rows have no observed seed ancestry")
    return parent, root_id, depth, errors


def _write_trace(
    output: Path,
    config: Any,
    devices: list[Any],
    origin: float,
    summary: dict[str, Any] | None,
    error: str | None,
) -> None:
    devices = sorted(devices, key=lambda item: item.device)
    cells = (
        np.concatenate([part for d in devices for part in d.trace_cells])
        if any(d.trace_cells for d in devices)
        else np.empty((0, 4), dtype=np.float64)
    )
    states = (
        np.concatenate([part for d in devices for part in d.trace_states])
        if len(cells)
        else np.empty(0, np.int32)
    )
    flags = (
        np.concatenate([part for d in devices for part in d.trace_flags])
        if len(cells)
        else np.empty(0, np.int32)
    )
    valid = (
        np.concatenate([part for d in devices for part in d.trace_valid])
        if len(cells)
        else np.empty(0, bool)
    )
    gpu = (
        np.concatenate([np.full(d.observed_rows, d.device, np.int16) for d in devices])
        if devices
        else np.empty(0, np.int16)
    )
    roots = [record for d in devices for record in d.root_seeds]
    parent, root_id, depth, errors = _hierarchy(cells, states, roots)
    batch_ids = np.empty(len(cells), dtype=np.int64)
    batches, offset, batch_id = [], 0, 0
    graphs_dir = output / "pruning-graphs"
    graphs_dir.mkdir(exist_ok=True)
    graph_records = []
    for device in devices:
        for source in device.trace_batches:
            record = dict(source)
            record["row_start"] += offset
            record["row_end"] += offset
            record["batch_id"] = batch_id
            batches.append(record)
            batch_ids[record["row_start"] : record["row_end"]] = batch_id
            rows = root_id[record["row_start"] : record["row_end"]]
            if np.any((rows < record["root_begin"]) | (rows >= record["root_end"])):
                errors.append(f"Batch {batch_id} ancestry leaves its assigned root task")
            batch_id += 1
        for state, snapshot in sorted(device.graph_snapshots.items()):
            metadata = dict(snapshot["metadata"])
            metadata["trace_row"] = offset + metadata["device_row"]
            path = graphs_dir / f"gpu-{device.device}-state-{state}.npz"
            with path.open("xb") as stream:
                np.savez_compressed(
                    stream,
                    **snapshot["arrays"],
                    metadata_utf8=np.frombuffer(
                        json.dumps(metadata, allow_nan=False).encode(), dtype=np.uint8
                    ),
                )
            graph_records.append({"path": str(path.relative_to(output)), **metadata})
        offset += device.observed_rows
    root_cells = (
        np.concatenate([record["cells"] for record in roots])
        if roots
        else np.empty((0, 4), np.float64)
    )
    root_ids = (
        np.concatenate(
            [np.arange(record["begin"], record["end"], dtype=np.int64) for record in roots]
        )
        if roots
        else np.empty(0, np.int64)
    )
    root_gpu = (
        np.concatenate(
            [
                np.full(record["end"] - record["begin"], record["device"], np.int16)
                for record in roots
            ]
        )
        if roots
        else np.empty(0, np.int16)
    )
    manifest = {
        "schema": "ResidentPruningTraceV1",
        "observation_metadata_schema": "ResidentPruningObservationV2",
        "cell_columns": ["xmin", "ymin", "xmax", "ymax"],
        "crs": "EPSG:32618",
        "terminal_scoring_executed": False,
        "decision_execution": "Existing current distinct-marker GPU predicate, once per batch",
        "terminal_score_override": (
            "Only exact terminal rectangles are emitted; no pose score/status/residual is computed"
        ),
        "state_codes": {
            "0": "retained_possible",
            "1": "entire_cell_known_land",
            "2": "positive_marker_without_possible_object",
            "3": "too_few_distinct_possible_objects",
            "4": "no_injective_positive_marker_explanation",
        },
        "row_order": "Grouped by GPU, then per-GPU batch ordinal; not global chronology",
        "clock": (
            "Host perf_counter, seconds relative to recording start; not a cross-device CUDA clock"
        ),
        "timing_semantics": (
            "Includes synchronous device-to-host observation and recording overhead; "
            "never a GPU performance benchmark"
        ),
        "recording_origin_perf_counter_s": origin,
        "depth_semantics": (
            "Exact generation from observed seed roots and mechanical midpoint child "
            "rectangles, not width inference"
        ),
        "parent_id_semantics": "Zero-based observed row ID; -1 for observed roots",
        "root_id_semantics": "Original GPU seed index, root_id = x_index * ny + y_index",
        "batch_file": "pruning-batches.jsonl",
        "hierarchy_valid": not errors,
        "hierarchy_errors": errors,
        "representative_graphs": graph_records,
        "representative_graph_semantics": (
            "First available state0/2/3/4 per GPU with valid proof metadata and zero "
            "flags; retained graph feasibility is not a location certificate"
        ),
        "matched_prefix_semantics": (
            "Successfully augmented report prefix, not maximum partial cardinality on failure"
        ),
        "input_source_and_cache_preservation": (
            "Owned by the external launch wrapper; this recorder never writes prepared caches"
        ),
        "summary": summary,
        "recording_error": error,
        "config": {
            "bounds_en_m": config.bounds_en_m,
            "initial_size_m": config.initial_size_m,
            "terminal_size_m": config.terminal_size_m,
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    with (output / "pruning-trace.npz").open("xb") as stream:
        np.savez_compressed(
            stream,
            cells=cells,
            state=states,
            depth=depth,
            gpu=gpu,
            cell_id=np.arange(len(cells), dtype=np.int64),
            parent_id=parent,
            root_id=root_id,
            batch_id=batch_ids,
            proof_flags=flags,
            proof_valid=valid,
            seed_cells=root_cells,
            seed_root_id=root_ids,
            seed_gpu=root_gpu,
            manifest_utf8=np.frombuffer(
                json.dumps(manifest, allow_nan=False).encode(), dtype=np.uint8
            ),
        )
    with (output / "pruning-batches.jsonl").open("x") as stream:
        for record in batches:
            stream.write(json.dumps(record, allow_nan=False) + "\n")
    (output / "trace-manifest.json").write_text(
        json.dumps(manifest, indent=2, allow_nan=False) + "\n"
    )
    if errors:
        raise RuntimeError(
            "Recorded raw cells/states were saved, but hierarchy metadata failed validation"
        )


def recorded_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
    devices: list[Any] = []
    registry_lock = Lock()
    origin = perf_counter()

    class RecordingDevice(OptimizedResidentDevice):
        def __init__(self, *device_args: Any) -> None:
            super().__init__(*device_args)
            if not self.use_independent_chart_pruning:
                raise RuntimeError("This recorder requires current distinct-marker pruning")
            self.trace_cells, self.trace_states, self.trace_flags, self.trace_valid = (
                [],
                [],
                [],
                [],
            )
            self.trace_batches, self.root_seeds = [], []
            self.graph_snapshots: dict[int, dict[str, Any]] = {}
            self.observed_rows = 0
            self.batch_ordinal = 0
            self.task_batch_ordinal = 0
            self.task: tuple[int, int, int] | None = None
            with registry_lock:
                devices.append(self)

        def seed(self, begin: int, end: int, ny: int) -> Any:
            cells = super().seed(begin, end, ny)
            self.task = (begin, end, ny)
            self.task_batch_ordinal = 0
            # Actual GPU seed output supplies exact root geometry and identity.
            self.root_seeds.append(
                {
                    "begin": begin,
                    "end": end,
                    "ny": ny,
                    "device": self.device,
                    "cells": self.cp.asnumpy(cells),
                }
            )
            return cells

        def prune(self, cells: Any, bearings: Any, tolerances: Any) -> Any:
            if self.task is None:
                raise RuntimeError("A pruning batch must follow an observed seed task")
            begin, end, ny = self.task
            started = perf_counter()
            # The default optimized prune route is precisely this invocation.
            # return_details observes the SAME graph and solve; no rerun occurs.
            details = prune_distinct_chart_geometry(
                self,
                cells,
                bearings,
                tolerances,
                true_bearings=self.use_true_bearings,
                return_details=True,
            )
            returned = perf_counter()
            host_cells = self.cp.asnumpy(cells)
            host_states = self.cp.asnumpy(details["states"])
            host_flags = self.cp.asnumpy(details["flags"])
            host_valid = self.cp.asnumpy(details["proof_valid"])
            ready = perf_counter()
            wanted = [state for state in (0, 2, 3, 4) if state not in self.graph_snapshots]
            if wanted:
                candidates = host_valid & (host_flags == 0)
                for state in wanted:
                    rows = np.flatnonzero(candidates & (host_states == state))
                    if not len(rows):
                        continue
                    row = int(rows[0])
                    graph = details["graph"]
                    count = int(self.cp.asnumpy(graph["counts"])[row])
                    capacity = graph["object_ids"].shape[1]
                    if not 0 <= count <= capacity:
                        raise RuntimeError("A representative graph has invalid column count")
                    ids = self.cp.asnumpy(graph["object_ids"][row, :count])
                    if np.any((ids < 0) | (ids >= len(self.world.object_keys))):
                        raise RuntimeError("A representative graph has invalid object IDs")
                    self.graph_snapshots[state] = {
                        "metadata": {
                            "device": self.device,
                            "device_row": self.observed_rows + row,
                            "batch_ordinal": self.batch_ordinal,
                            "row_in_batch": row,
                            "state": state,
                            "flags": int(host_flags[row]),
                            "proof_valid": bool(host_valid[row]),
                            "object_count": count,
                            "matched_prefix": int(self.cp.asnumpy(details["matched_counts"])[row]),
                            "feasible": int(self.cp.asnumpy(details["feasible"])[row]),
                            "object_keys": [self.world.object_keys[int(i)] for i in ids],
                            "report_indices": "Zero-based filtered positive-aid bearing order",
                        },
                        "arrays": {
                            "cell": host_cells[row],
                            "incidence": self.cp.asnumpy(graph["incidence"][row, :count]),
                            "object_ids": ids,
                            "objects": self.world.objects[ids],
                            "bearings_deg": self.cp.asnumpy(bearings),
                            "tolerances_deg": self.cp.asnumpy(tolerances),
                        },
                    }
            finished = perf_counter()
            self.trace_cells.append(host_cells)
            self.trace_states.append(host_states)
            self.trace_flags.append(host_flags)
            self.trace_valid.append(host_valid)
            self.trace_batches.append(
                {
                    "device": self.device,
                    "batch_ordinal": self.batch_ordinal,
                    "task_batch_ordinal": self.task_batch_ordinal,
                    "root_begin": begin,
                    "root_end": end,
                    "ny": ny,
                    "row_start": self.observed_rows,
                    "row_end": self.observed_rows + len(host_cells),
                    "started_s": started - origin,
                    "predicate_returned_s": returned - origin,
                    "host_buffers_ready_s": ready - origin,
                    "observation_complete_s": finished - origin,
                    "observed_decision_rows": len(host_cells),
                }
            )
            self.observed_rows += len(host_cells)
            self.batch_ordinal += 1
            self.task_batch_ordinal += 1
            return details["states"]

        def score(self, cells: Any, bearings: Any, tolerances: Any) -> dict[str, Any]:
            return {"cells": cells}

    kwargs["device_factory"] = RecordingDevice
    output = Path(args[4] if len(args) > 4 else kwargs["output_dir"])
    config = args[1] if len(args) > 1 else kwargs["config"]
    try:
        result = _original_run(*args, **kwargs)
    except BaseException as error:
        if output.exists():
            _write_trace(output, config, devices, origin, None, repr(error))
        raise
    _write_trace(output, config, devices, origin, result, None)
    manifest_path = output / "manifest.json"
    search_manifest = json.loads(manifest_path.read_text())
    search_manifest["terminal_scoring_executed"] = False
    search_manifest["recording_only"] = True
    search_manifest["sample_semantics"] = (
        "Unscored terminal rectangles; no terminal status or residual computed"
    )
    manifest_path.write_text(json.dumps(search_manifest, indent=2) + "\n")
    return result


if __name__ == "__main__":
    if "--execute" in sys.argv and "--optimized" not in sys.argv:
        raise SystemExit("Pruning recording requires --execute --optimized")
    setattr(engine, "run_resident_search", recorded_run)
    cached_main()
