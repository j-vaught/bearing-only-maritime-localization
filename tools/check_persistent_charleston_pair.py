"""Two explicitly approved Charleston searches using one retained GPU session.

J.C. Vaught. Without --execute, no input loading or CUDA work occurs.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print(
            json.dumps(
                {
                    "searches": 2,
                    "site": "B17",
                    "devices": [0, 1, 2],
                    "executed": False,
                    "scope": "Full coast, identical corrected observations",
                }
            )
        )
        return
    os_root = REPO / "docs/verification/b-location-suite/run_suite.py"
    spec = importlib.util.spec_from_file_location("pair_suite", os_root)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    from maritime.search.gpu.resident.service import PersistentSearchService

    os.chdir(REPO)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    runner.resource_guard()
    settings = runner.read(runner.REFERENCE)["settings"]
    settings.pop("measurements")
    settings["search"]["roots_per_task"] = 128
    settings["search"]["batch_cells"] = max(128, settings["search"]["batch_cells"])
    runner.save(output / "settings.json", settings)
    runner.save(
        output / "approval.json",
        {
            "scope": "Exactly two full-coast Charleston requests on GPUs 0,1,2",
            "observations": "One corrected simulator frame, reused without regeneration",
            "source_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            "runner_sha256": runner.digest(Path(__file__)),
            "created_utc": runner.now(),
        },
    )
    runner.save(output / "status.json", {"stage": "cache_loading", "utc": runner.now()})
    world, layers, scene, shared = runner.prepare(settings, refresh_aid_view=False)

    def fingerprints() -> dict[str, Any]:
        return {
            name: {
                "sha256": runner.digest(name),
                "size": Path(name).stat().st_size,
                "mtime_ns": Path(name).stat().st_mtime_ns,
            }
            for name in shared["cache_paths"]
        }

    before = fingerprints()
    runner.save(output / "cache-before.json", before)
    runner.save(output / "shared-preparation.json", shared)
    runner.save(
        output / "catalog-alignment.json", runner.normalized_catalog_alignment(world, scene)
    )
    simulation = output / "simulation"
    simulation.mkdir()
    sim_settings = runner.read(runner.SIM_SETTINGS)
    runner.save(simulation / "settings.json", sim_settings)
    row = next(row for row in runner.sites() if row["site_id"] == "B17")
    markers, reports = runner.simulate(row, layers, scene, sim_settings, simulation)
    observations_sha256 = runner.digest(simulation / "measurements.json")
    service = PersistentSearchService(world, settings["search"], output)
    records: list[dict[str, Any]] = []
    try:
        for index in range(2):
            directory = output / f"run-{index + 1}"
            directory.mkdir()
            shutil.copyfile(simulation / "measurements.json", directory / "measurements.json")
            runner.save(
                directory / "settings.json",
                {**settings, "measurements": str(directory / "measurements.json")},
            )
            runner.resource_guard(owned_worker_pid=service.pid)
            runner.save(
                output / "status.json", {"stage": "search", "run": index + 1, "utc": runner.now()}
            )
            print(
                json.dumps({"stage": "search", "run": index + 1, "detected_aids": len(markers)}),
                flush=True,
            )
            started = time.perf_counter()
            response = service.search(
                markers,
                settings["marker_tolerance_deg"],
                directory,
                timeout_s=600,
                progress_check=lambda: runner.check_search_resource_ceiling(directory),
            )
            roundtrip_s = time.perf_counter() - started
            summary, metadata = response["summary"], response["metadata"]
            assert summary["complete"]
            diagnostics = summary["resident_session"]["devices"]
            assert {d["device"] for d in diagnostics} == {0, 1, 2}
            assert all(d["reused"] == bool(index) for d in diagnostics)
            assert all(d["created"] == (index == 0) for d in diagnostics)
            runner.save(
                output / "status.json",
                {"stage": "selection", "run": index + 1, "utc": runner.now()},
            )
            selection_s = runner.bounded_command(
                runner.command(
                    "tools.select_resident_result",
                    directory / "search",
                    directory / "estimate.json",
                    "--device",
                    "0",
                ),
                directory,
                "selection",
                120,
            )
            runner.save(
                directory / "estimate-freeze.json",
                {
                    "frozen_utc": runner.now(),
                    "estimate_sha256": runner.digest(directory / "estimate.json"),
                },
            )
            accuracy = runner.geodesic_after_freeze(
                directory / "estimate.json",
                directory / "estimate-freeze.json",
                simulation / "truth.json",
            )
            runner.save(directory / "accuracy.json", accuracy)
            record = {
                "run": index + 1,
                "search_s": summary["elapsed_s"],
                "api_wall_s": metadata["api_wall_s"],
                "request_roundtrip_s": roundtrip_s,
                "worker_startup_s": service.startup_s if index == 0 else 0,
                "gpu_buffers_reused": metadata["gpu_buffers_reused"],
                "worker_pid": metadata["worker_pid"],
                "devices": diagnostics,
                "selection_s": selection_s,
                "position_error_m": accuracy["position_error_m"],
                "positive_aid_reports": len(markers),
                "total_reports": reports,
                "observations_sha256": runner.digest(directory / "measurements.json"),
                "estimate": runner.read(directory / "estimate.json")["estimate"],
                "summary": summary,
            }
            assert record["observations_sha256"] == observations_sha256
            records.append(record)
            runner.save(output / "results.json", records)
            print(
                json.dumps(
                    {
                        k: record[k]
                        for k in (
                            "run",
                            "search_s",
                            "position_error_m",
                            "gpu_buffers_reused",
                            "worker_pid",
                        )
                    }
                ),
                flush=True,
            )
    finally:
        try:
            service.close()
            if service.pid is not None:
                runner.clear_group(service.pid)
            runner.save(
                output / "worker-cleanup.json",
                {
                    "pid": service.pid,
                    "live_group_members": runner.group_members(service.pid) if service.pid else [],
                },
            )
        finally:
            after = fingerprints()
            runner.save(output / "cache-after.json", after)
            assert before == after, "Saved cache changed"
    assert len(records) == 2
    assert records[0]["worker_pid"] == records[1]["worker_pid"]
    threads = [{d["device"]: d["owner_thread_id"] for d in r["devices"]} for r in records]
    assert threads[0] == threads[1]
    assert records[0]["estimate"] == records[1]["estimate"], "Selected estimates differ"
    compare_terminal_outputs(output / "run-1/search", output / "run-2/search")
    runner.save(
        output / "comparison.json",
        {
            "passed": True,
            "identical_observations": True,
            "identical_selected_estimates": True,
            "identical_terminal_records": True,
            "same_worker_pid": True,
            "same_device_owner_threads": True,
            "second_request_reused_all_devices": True,
            "saved_cache_unchanged": True,
            "first_search_s": records[0]["search_s"],
            "second_search_s": records[1]["search_s"],
            "first_over_second_ratio": records[0]["search_s"] / records[1]["search_s"],
            "position_error_m": records[1]["position_error_m"],
            "shared_preparation_s": shared["shared_preparation_s"],
        },
    )
    runner.save(output / "status.json", {"stage": "finished", "passed": True, "utc": runner.now()})


def compare_terminal_outputs(first: Path, second: Path) -> None:
    """Compare saved terminal arrays after both estimates have been frozen."""
    import numpy as np

    def load(root: Path) -> dict[str, Any]:
        parts: dict[str, list[Any]] = {}
        for path in sorted(root.rglob("leaf-*.npz")):
            with np.load(path, allow_pickle=False) as data:
                for key in data.files:
                    parts.setdefault(key, []).append(data[key])
        arrays = {key: np.concatenate(value) for key, value in parts.items()}
        cells = arrays["cells"]
        order = np.lexsort(tuple(cells[:, i] for i in (3, 2, 1, 0)))
        return {key: value[order] for key, value in arrays.items()}

    a, b = load(first), load(second)
    assert a.keys() == b.keys()
    for key in a:
        np.testing.assert_equal(a[key], b[key], err_msg=f"Saved terminal output differs for {key}")


if __name__ == "__main__":
    main()
