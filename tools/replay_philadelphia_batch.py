"""Replay real saved Philadelphia batches for non-admin source profiling.

J.C. Vaught. All trials share frozen observations and preloaded GPU maps.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--cells-per-batch", type=int, default=64)
    parser.add_argument("--instrumented", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print(
            json.dumps(
                {
                    "executed": False,
                    "search_executed": False,
                    "saved_site": "B11",
                    "batches": 3,
                    "cells_per_batch": args.cells_per_batch,
                    "device": 0,
                }
            )
        )
        return
    if not 1 <= args.cells_per_batch <= 256:
        raise ValueError("Select 1 to 256 cells per saved batch")
    os.chdir(REPO)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location(
        "dynamic_benchmark_suite", REPO / "docs/verification/b-location-suite/run_suite.py"
    )
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    import numpy as np

    from maritime.schemas.inputs_v2 import CatalogV2
    from maritime.search.gpu.resident import aid_view
    from maritime.search.gpu.resident.cache import load_or_prepare, preparation_identity
    from maritime.search.gpu.resident.engine import ResidentConfig
    from maritime.serialization import read_contract

    runner.resource_guard()
    settings = runner.read(runner.REFERENCE)["settings"]
    key = preparation_identity(
        Path(settings["map"]), Path(settings["catalog"]), settings["preparation"]
    )
    base = (REPO / ".cache/resident-world").resolve()
    sidecar = base.parent / "resident-aid-objects"
    runner.save(output / "status.json", {"stage": "cache_loading"})
    world, hit = load_or_prepare(base, key, runner.forbid_build)
    original = aid_view._build_view
    aid_view._build_view = runner.forbid_build
    try:
        world, object_hit, object_key = aid_view.load_or_canonicalize(
            sidecar, key, world, read_contract(Path(settings["catalog"]), CatalogV2)
        )
    finally:
        aid_view._build_view = original
    assert hit and object_hit
    cache_paths = [base / f"{key}.npz", sidecar / f"aid-view-{object_key}.npz"]

    def fingerprints():
        return {
            str(p): {
                "sha256": runner.digest(p),
                "size": p.stat().st_size,
                "mtime_ns": p.stat().st_mtime_ns,
            }
            for p in cache_paths
        }

    before = fingerprints()
    runner.save(output / "cache-before.json", before)
    from maritime.search.gpu.resident.optimized import OptimizedResidentDevice
    from maritime.search.gpu.resident.parallel_prediction import _module

    launch = runner.read(REPO / ".agent-work/dynamic-all-eligible-launch.json")
    suite = Path(launch["output"])
    assert runner.read(suite / "status.json")["stage"] == "finished"
    reference = suite / "B11"
    config = ResidentConfig(**runner.read(suite / "config.json"))
    prior = next(row for row in runner.read(suite / "results.json") if row["site"] == "B11")
    observations = runner.read(Path(prior["saved_attempt"]) / "measurements.json")
    bearings = np.asarray(
        [
            m["bearing_deg"]
            for m in observations["measurements"]
            if m["object_type"] == "physical_aid"
        ]
    )
    tolerances = np.full(len(bearings), settings["marker_tolerance_deg"])
    leaves = sorted((reference / "search").rglob("leaf-*.npz"))
    assert len(leaves) >= 3
    selected = [leaves[index] for index in (0, len(leaves) // 2, len(leaves) - 1)]
    runner.save(
        output / "protocol.json",
        {
            "source_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            "search_executed": False,
            "device": 0,
            "saved_site": "B11",
            "reference": str(reference),
            "batches": [str(p) for p in selected],
            "cells_per_batch_limit": args.cells_per_batch,
            "instrumented": args.instrumented,
            "full_search_error_m_previously_verified": prior["position_error_m"],
            "timing_scope": "Saved terminal-cell replay; not full-search performance",
        },
    )
    resident = None
    records: list[dict[str, Any]] = []
    try:
        resident = OptimizedResidentDevice(world, config, 0)
        cp = resident.cp
        with cp.cuda.Device(0), resident.stream:
            module = _module(resident)
            raw = module.get_function("pp_pairs")
            attributes = raw.attributes
            active_blocks = cp.cuda.driver.occupancyMaxActiveBlocksPerMultiprocessor(
                raw.kernel.ptr, 128, 0
            )
            limits = cp.cuda.runtime.getDeviceProperties(0)
            metadata = {
                "kernel": "pp_pairs",
                "attributes": attributes,
                "threads_per_block": 128,
                "theoretical_active_blocks_per_sm": active_blocks,
                "theoretical_active_threads_per_sm": active_blocks * 128,
                "max_threads_per_sm": limits["maxThreadsPerMultiProcessor"],
                "theoretical_occupancy_percent": 100
                * active_blocks
                * 128
                / limits["maxThreadsPerMultiProcessor"],
                "multiprocessors": limits["multiProcessorCount"],
                "occupancy_is_static_limit_not_measured_runtime_occupancy": True,
            }
            runner.save(output / "kernel-resources.json", metadata)
            print(json.dumps(metadata), flush=True)
            spans = []

            class ModuleProxy:
                def get_function(self, name):
                    kernel = module.get_function(name)
                    if name != "pp_pairs":
                        return kernel

                    def call(grid, block, values):
                        start, finish = cp.cuda.Event(), cp.cuda.Event()
                        start.record(resident.stream)
                        kernel(grid, block, values)
                        finish.record(resident.stream)
                        spans.append((start, finish, int(values[3]), grid, block))

                    return call

            setattr(resident, "_parallel_prediction_module", ModuleProxy())
            db, dt = cp.asarray(bearings), cp.asarray(tolerances)
            for index, leaf in enumerate(selected):
                with np.load(leaf, allow_pickle=False) as saved:
                    count = min(args.cells_per_batch, len(saved["cells"]))
                    indices = np.linspace(0, len(saved["cells"]) - 1, count, dtype=np.int64)
                    expected = {key: saved[key][indices].copy() for key in saved.files}
                cells = cp.asarray(expected["cells"])
                scored = resident.score(cells, db, dt)
                resident.stream.synchronize()
                assert scored.keys() == expected.keys()
                actual = {key: cp.asnumpy(value) for key, value in scored.items()}
                for key in expected:
                    np.testing.assert_equal(
                        actual[key], expected[key], err_msg=f"Replay differs for {key}"
                    )
                np.savez_compressed(output / f"batch-{index}-reference.npz", **expected)
                np.savez_compressed(output / f"batch-{index}-replayed.npz", **actual)
                start, finish, pairs, grid, block = spans[-1]
                record = {
                    "batch": index,
                    "leaf": str(leaf),
                    "cells": count,
                    "object_pairs": pairs,
                    "kernel_grid": grid,
                    "kernel_block": block,
                    "kernel_event_ms": cp.cuda.get_elapsed_time(start, finish),
                    "instrumented_timing": args.instrumented,
                    "all_saved_fields_identical": True,
                }
                records.append(record)
                runner.save(output / "results.json", records)
                print(json.dumps(record), flush=True)
        runner.save(
            output / "status.json",
            {
                "stage": "finished",
                "search_executed": False,
                "batches": len(records),
                "cells": sum(t["cells"] for t in records),
                "all_replay_outputs_identical": True,
            },
        )
    finally:
        if resident is not None:
            resident.close()
        after = fingerprints()
        runner.save(output / "cache-after.json", after)
        assert before == after, "Saved cache changed"


if __name__ == "__main__":
    main()
