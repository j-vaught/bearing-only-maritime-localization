"""Bounded old/new resident comparison on a shared spare GPU.

This validates a small fixed workload, not full-coast localization or an
uncontended speed claim. The existing search and prepared cache are read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from dataclasses import replace
from math import ceil
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any

import numpy as np
from pyproj import Geod, Transformer

from maritime.schemas.inputs_v2 import MeasurementSetV2
from maritime.schemas.object_types import normalize_object_type
from maritime.search.gpu.resident.cache import load_or_prepare
from maritime.search.gpu.resident.engine import ResidentConfig, ResidentDevice
from maritime.search.gpu.resident.optimized import OptimizedResidentDevice
from maritime.serialization import read_contract

ROOT = Path(__file__).resolve().parents[1]
JOB = ROOT / "runs/resident-charleston-refining-20261028-job"


def hardware() -> str:
    return subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=index,utilization.gpu,memory.used,power.draw",
            "--format=csv,noheader",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def running() -> dict[str, Any]:
    pid = int((JOB / "search.pid").read_text())
    stat = Path(f"/proc/{pid}/stat")
    progress = ROOT / "runs/resident-charleston-refining-20261028/progress.jsonl"
    last = progress.read_text().splitlines()[-1] if progress.exists() else "{}"
    return {"pid": pid, "alive": stat.exists(), "progress": json.loads(last)}


def baseline_hashes() -> dict[str, str]:
    paths = [
        "maritime/search/gpu/resident/engine.py",
        "maritime/search/gpu/resident/kernels.cu",
        "maritime/search/gpu/resident/preparation.py",
        "maritime/search/gpu/batch_assignment.py",
    ]
    return {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "docs/verification/resident-optimization-short.json"
    )
    args = parser.parse_args()
    if args.device in (0, 1, 2):
        raise ValueError("The existing run uses GPUs0,1,2; choose GPU3 for this bounded check.")
    import cupy as cp

    report: dict[str, Any] = {
        "scope": "Short fixed-workload correctness/performance comparison; not coast-wide accuracy",
        "device": args.device,
        "shared_gpu_timings": True,
        "timing_caveat": "Shared GPU; medians reflect contention, not isolated hardware speed.",
        "input_stream_dependency": "Caller stream synchronized before resident streams",
        "repeats": args.repeats,
        "hardware_before": hardware(),
        "existing_run_before": running(),
        "baseline_hashes_before": baseline_hashes(),
        "phases": {},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save() -> None:
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")

    def stage(name: str) -> None:
        print(json.dumps({"stage": name}), flush=True)
        save()

    stage("load_same_saved_cache")
    proof = json.loads((JOB / "cache-verification.json").read_text())
    cache_file = Path(proof["cache_path"])
    cache_stat = cache_file.stat()

    def forbidden():
        raise RuntimeError("Benchmark must not rebuild the prepared cache")

    t = perf_counter()
    world, hit = load_or_prepare(cache_file.parent, proof["cache_key"], forbidden)
    report["cache_load_s"] = perf_counter() - t
    report["cache_hit"] = hit
    settings = json.loads((JOB / "settings.json").read_text())
    config = replace(
        ResidentConfig(**settings["search"]),
        devices=(args.device,),
        batch_cells=8,
        roots_per_task=1,
    )
    measured = read_contract(ROOT / settings["measurements"], MeasurementSetV2)
    markers = [
        m.bearing_deg
        for m in measured.measurements
        if normalize_object_type(m.object_type) == "physical_aid"
    ]
    report["marker_reports"] = len(markers)
    geod = Geod(ellps="WGS84")
    inverse = Transformer.from_crs("EPSG:32618", "EPSG:4326", always_xy=True)
    x, y = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform(-79.905, 32.77)
    t = perf_counter()
    old = ResidentDevice(world, config, args.device)
    new = OptimizedResidentDevice(world, config, args.device)
    report["device_setup_s"] = perf_counter() - t
    devices = {"old": old, "new": new}

    def paired(name: str, functions: dict[str, Any]) -> dict[str, Any]:
        # Inputs are created on the caller stream; resident streams are
        # non-blocking and need an explicit dependency before reading them.
        with cp.cuda.Device(args.device):
            cp.cuda.get_current_stream().synchronize()
        stage(name + "_warmup")
        warm = {}
        for label in ("old", "new"):
            device = devices[label]
            with cp.cuda.Device(args.device), device.stream:
                t0 = perf_counter()
                functions[label]()
                device.stream.synchronize()
                warm[label] = perf_counter() - t0
        stage(name + "_timing")
        measurements = {"old": [], "new": []}
        last = {}
        for repeat in range(args.repeats):
            order = ("old", "new") if repeat % 2 == 0 else ("new", "old")
            for label in order:
                with cp.cuda.Device(args.device), devices[label].stream:
                    start, end = cp.cuda.Event(), cp.cuda.Event()
                    t0 = perf_counter()
                    start.record()
                    result = functions[label]()
                    end.record()
                    end.synchronize()
                    elapsed = perf_counter() - t0
                    measurements[label].append(
                        {
                            "wall_s": elapsed,
                            "cuda_stream_span_ms": float(cp.cuda.get_elapsed_time(start, end)),
                        }
                    )
                    last[label] = result
        stats = {"warmup_including_compilation_s": warm, "paired_samples": measurements}
        for label in ("old", "new"):
            stats[label + "_median_wall_s"] = median(m["wall_s"] for m in measurements[label])
        stats["observed_old_over_new_wall_ratio"] = (
            stats["old_median_wall_s"] / stats["new_median_wall_s"]
        )
        report["phases"][name] = stats
        save()
        return last

    def score_signature(result: dict[str, Any]) -> dict[str, Any]:
        host = {k: cp.asnumpy(v) for k, v in result.items()}
        counts = np.sum(host["matched_records"] >= 0, axis=1)
        objective = []
        for cell, assignments in zip(host["cells"], host["matched_records"], strict=True):
            lon, lat = inverse.transform((cell[0] + cell[2]) / 2, (cell[1] + cell[3]) / 2)
            total = 0.0
            for bearing, record in zip(markers, assignments, strict=True):
                if record < 0:
                    continue
                row = world.objects[record]
                predicted, _, _ = geod.inv(lon, lat, row[2], row[3])
                total += abs((bearing - predicted + 180) % 360 - 180)
            objective.append(total)
        return {
            "status": host["status"].tolist(),
            "flags": host["flags"].tolist(),
            "matched_counts": counts.tolist(),
            "partial_objective_deg": objective,
            "records": host["matched_records"].tolist(),
        }

    with cp.cuda.Device(args.device):
        obs = cp.asarray(markers)
        tolerance = cp.full(len(markers), settings["marker_tolerance_deg"])
        cells = cp.asarray(
            [
                [x + dx - 10, y + dy - 10, x + dx + 10, y + dy + 10]
                for dx, dy in ((0.0, 0.0), (50.0, 0.0), (0.0, 50.0), (200.0, 200.0))
            ]
        )
        stage("known_location_is_only_a_validation_witness")
        witness = cp.asarray(
            [
                [x - w / 2, y - w / 2, x + w / 2, y + w / 2]
                for w in (1.0, 10.0, 100.0, 1000.0, 10000.0)
            ]
        )
        cp.cuda.get_current_stream().synchronize()
        with new.stream:
            state = new.prune(witness, obs, tolerance)
            new.stream.synchronize()
            assert np.all(cp.asnumpy(state) == 0), "New pruning rejected a valid Charleston witness"
        report["valid_charleston_containing_cells_retained_widths_m"] = [
            1.0,
            10.0,
            100.0,
            1000.0,
            10000.0,
        ]
        result = paired(
            "score_four_charleston_candidates",
            {
                label: (lambda device=device: device.score(cells, obs, tolerance))
                for label, device in devices.items()
            },
        )
        signatures = {label: score_signature(value) for label, value in result.items()}
        for key in ("status", "flags", "matched_counts"):
            assert signatures["old"][key] == signatures["new"][key], key
        np.testing.assert_allclose(
            signatures["old"]["partial_objective_deg"],
            signatures["new"]["partial_objective_deg"],
            rtol=0,
            atol=1e-5,
        )
        report["known_center_accuracy_limitation"] = {
            "candidate": "Known Charleston center, used only as a validation case",
            "marker_reports": len(markers),
            "old_status": signatures["old"]["status"][0],
            "new_status": signatures["new"]["status"][0],
            "old_flags": signatures["old"]["flags"][0],
            "new_flags": signatures["new"]["flags"][0],
            "old_matched_reports": signatures["old"]["matched_counts"][0],
            "new_matched_reports": signatures["new"]["matched_counts"][0],
            "full_match_at_known_center": signatures["new"]["matched_counts"][0] == len(markers),
            "localization_accuracy_established": False,
        }
        report["phases"]["score_four_charleston_candidates"]["correctness"] = {
            "status_flags_cardinality_equal": True,
            "partial_objective_max_abs_difference_deg": float(
                np.max(
                    np.abs(
                        np.asarray(signatures["old"]["partial_objective_deg"])
                        - np.asarray(signatures["new"]["partial_objective_deg"])
                    )
                )
            ),
            "signatures": signatures,
        }
        save()

        x0, x1, y0, y1 = config.bounds_en_m
        ny = ceil((y1 - y0) / config.initial_size_m)
        total = ceil((x1 - x0) / config.initial_size_m) * ny
        ids = cp.arange(256, dtype=cp.int64) * ((total - 1) // 255)
        xx = x0 + (ids // ny) * config.initial_size_m
        yy = y0 + (ids % ny) * config.initial_size_m
        coast = cp.stack(
            (xx, yy, cp.minimum(xx + 10000.0, x1), cp.minimum(yy + 10000.0, y1)), axis=1
        )
        result = paired(
            "prune_256_fixed_coast_zones",
            {
                label: (lambda device=device: device.prune(coast, obs, tolerance))
                for label, device in devices.items()
            },
        )
        report["phases"]["prune_256_fixed_coast_zones"]["outcomes"] = {
            label: {
                "pruned": int(cp.count_nonzero(state).item()),
                "retained": int(cp.count_nonzero(state == 0).item()),
            }
            for label, state in result.items()
        }
        assert np.all((cp.asnumpy(result["old"]) == 0) | (cp.asnumpy(result["new"]) != 0))
        save()

        def bounded_search(device: ResidentDevice) -> dict[str, int]:
            queue = device.seed(0, 8, ny)
            evaluated = pruned = terminal = 0
            while len(queue):
                batch, queue = queue[:8], queue[8:]
                states = device.prune(batch, obs, tolerance)
                survivor = batch[states == 0]
                small = cp.max(survivor[:, 2:] - survivor[:, :2], axis=1) <= 2500.0
                leaves, expand = survivor[small], survivor[~small]
                queue = cp.concatenate((queue, device.split(expand)))
                if len(leaves):
                    device.score(leaves, obs, tolerance)
                evaluated += len(batch)
                pruned += int(cp.count_nonzero(states).item())
                terminal += len(leaves)
            return {
                "evaluated": evaluated,
                "pruned": pruned,
                "terminal": terminal,
                "initial_roots": 8,
                "terminal_width_m": 2500,
            }

        result = paired(
            "eight_root_automatic_search",
            {
                label: (lambda device=device: bounded_search(device))
                for label, device in devices.items()
            },
        )
        report["phases"]["eight_root_automatic_search"]["outcomes"] = result
        save()

    # Overflow is included as a possible regression, not filtered out of results.
    overflow_config = replace(config, object_capacity=8)
    overflow_devices = {
        "old": ResidentDevice(world, overflow_config, args.device),
        "new": OptimizedResidentDevice(world, overflow_config, args.device),
    }
    original_devices = devices
    devices = overflow_devices
    with cp.cuda.Device(args.device):
        result = paired(
            "score_dense_overflow_capacity8",
            {
                label: (lambda device=device: device.score(cells, obs, tolerance))
                for label, device in devices.items()
            },
        )
        a, b = score_signature(result["old"]), score_signature(result["new"])
        for key in ("status", "flags", "matched_counts"):
            assert a[key] == b[key], "overflow " + key
        report["phases"]["score_dense_overflow_capacity8"]["correctness"] = {
            "status_flags_cardinality_equal": True,
            "old": a,
            "new": b,
        }
    devices = original_devices
    after = cache_file.stat()
    report["cache_preservation_check"] = "Archive size and nanosecond modification time"
    report["cache_unchanged"] = (after.st_size, after.st_mtime_ns) == (
        cache_stat.st_size,
        cache_stat.st_mtime_ns,
    )
    report["baseline_hashes_after"] = baseline_hashes()
    report["baseline_sources_unchanged"] = (
        report["baseline_hashes_before"] == report["baseline_hashes_after"]
    )
    report["existing_run_after"] = running()
    report["hardware_after"] = hardware()
    assert report["cache_unchanged"] and report["baseline_sources_unchanged"]
    assert report["existing_run_before"]["alive"] and report["existing_run_after"]["alive"]
    report["complete"] = True
    save()
    print(
        json.dumps(
            {
                "output": str(args.output),
                "complete": True,
                "phases": {
                    name: {
                        "ratio": value["observed_old_over_new_wall_ratio"],
                        "old_s": value["old_median_wall_s"],
                        "new_s": value["new_median_wall_s"],
                    }
                    for name, value in report["phases"].items()
                },
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
