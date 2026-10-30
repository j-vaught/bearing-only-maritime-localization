"""Cache-only dynamic full-coast evaluation of every previously eligible B site.

J.C. Vaught. All trials share frozen observations and preloaded GPU maps.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from threading import Event
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--site", default=None)
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument("--hardware-profile", action="store_true")
    parser.add_argument(
        "--saved-suite",
        type=Path,
        default=REPO / "runs/b-location-suite-preloaded-20261030T005811Z",
    )
    args = parser.parse_args()
    if not args.execute:
        print(
            json.dumps(
                {
                    "executed": False,
                    "scope": "Eligible sites, full-coast dynamic search, saved observations",
                    "site": args.site,
                    "hardware_profile": args.hardware_profile,
                }
            )
        )
        return
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
    from maritime.search.gpu.resident.engine import ResidentConfig, run_resident_search
    from maritime.search.gpu.resident.profiling import ProfiledResidentDevice
    from maritime.search.gpu.resident.session import ResidentSearchSession
    from maritime.serialization import read_contract
    from tools.check_persistent_charleston_pair import compare_terminal_outputs

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
    saved = runner.read(args.saved_suite / "results.json")
    rows = [r for r in saved if r["positive_marker_reports"] > 15]
    assert len(rows) == 20 and all(r["status"] == "completed" for r in rows)
    if args.site is not None:
        rows = [r for r in rows if r["site_id"] == args.site]
        if len(rows) != 1:
            raise ValueError("--site must select a previously eligible site")
    for row in rows:
        frozen = Path(row["attempt_directory"])
        assert (frozen / "measurements.json").is_file()
        assert (frozen / "search/summary.json").is_file()
    search: dict[str, Any] = {
        **settings["search"],
        "devices": (0, 1, 2),
        "roots_per_task": 128,
        "batch_cells": 512,
        "share_terminal_work": True,
    }
    config = ResidentConfig(**search)
    runner.save(output / "config.json", asdict(config))
    runner.save(
        output / "protocol.json",
        {
            "source_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            "saved_suite": str(args.saved_suite.resolve()),
            "eligible_sites": [r["site_id"] for r in rows],
            "minimum_positive_aids": 16,
            "scope": "Full eastern seaboard at every site",
            "observations_regenerated": False,
            "hardware_profile": args.hardware_profile,
            "searches_per_site": 1,
            "warmup_excluded": not args.no_warmup,
            "devices": [0, 1, 2],
            "one_resident_session_for_all_sites": True,
        },
    )
    records = []

    def factory(prepared, cfg, device):
        return ProfiledResidentDevice(prepared, cfg, device, sample_every=10)

    def observations(row):
        frozen = Path(row["attempt_directory"])
        measurements = runner.read(frozen / "measurements.json")
        bearings = np.asarray(
            [
                m["bearing_deg"]
                for m in measurements["measurements"]
                if m["object_type"] == "physical_aid"
            ]
        )
        assert len(bearings) == row["positive_marker_reports"]
        return frozen, bearings, np.full(len(bearings), settings["marker_tolerance_deg"])

    try:
        with ResidentSearchSession(world, config, device_factory=factory) as session:
            runner.save(output / "status.json", {"stage": "gpu_preload"})
            preload = output / "gpu-preload"
            preload.mkdir()
            with session.request(world, config):
                session.execute_workers(lambda device, resident: None, preload / "search", Event())
            if not args.no_warmup:
                warm = output / "warmup"
                warm.mkdir()
                row = next(r for r in saved if r["site_id"] == "B17")
                _, bearings, tolerances = observations(row)
                runner.save(output / "status.json", {"stage": "excluded_charleston_warmup"})
                result = run_resident_search(
                    world,
                    config,
                    bearings,
                    tolerances,
                    warm / "search",
                    session=session,
                    sharing=True,
                )
                assert result["complete"]
            for row in rows:
                site = row["site_id"]
                directory = output / site
                directory.mkdir()
                frozen, bearings, tolerances = observations(row)
                runner.save(
                    output / "status.json",
                    {
                        "stage": "search",
                        "site": site,
                        "completed_sites": len(records),
                        "total_sites": len(rows),
                    },
                )
                print(
                    json.dumps({"stage": "search", "site": site, "aids": len(bearings)}), flush=True
                )
                result = run_resident_search(
                    world,
                    config,
                    bearings,
                    tolerances,
                    directory / "search",
                    session=session,
                    sharing=True,
                )
                assert result["complete"]
                assert all(d["reused"] for d in result["resident_session"]["devices"])
                runner.save(
                    output / "status.json", {"stage": "saved_output_verification", "site": site}
                )
                runner.bounded_command(
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
                    {"estimate_sha256": runner.digest(directory / "estimate.json")},
                )
                accuracy = runner.geodesic_after_freeze(
                    directory / "estimate.json",
                    directory / "estimate-freeze.json",
                    frozen / "truth.json",
                )
                runner.save(directory / "accuracy.json", accuracy)
                assert (
                    runner.read(directory / "estimate.json")["estimate"]
                    == runner.read(frozen / "estimate.json")["estimate"]
                ), "Selected estimate changed"
                compare_terminal_outputs(frozen / "search", directory / "search")
                assert accuracy["position_error_m"] == row["position_error_m"]
                counts = {d: 0 for d in config.devices}
                for leaf in (directory / "search").rglob("leaf-*.npz"):
                    with np.load(leaf, allow_pickle=False) as data:
                        counts[int(leaf.parent.name.rsplit("-", 1)[1])] += len(data["status"])
                sharing = result["dynamic_score_sharing"]
                assert sharing["map_bytes_moved"] == 0
                assert sharing["candidate_bytes_moved"] <= sharing["byte_budget"]
                record = {
                    "site": site,
                    "port": row["port"],
                    "aids": len(bearings),
                    "old_preloaded_search_s": row["search_s"],
                    "dynamic_search_s": result["elapsed_s"],
                    "time_reduction_percent": 100 * (1 - result["elapsed_s"] / row["search_s"]),
                    "position_error_m": accuracy["position_error_m"],
                    "old_position_error_m": row["position_error_m"],
                    "terminal_by_device": counts,
                    "terminal_outputs_identical": True,
                    "estimate_identical": True,
                    "gpu_buffers_reused": True,
                    "observation_sha256": runner.digest(frozen / "measurements.json"),
                    "saved_attempt": str(frozen),
                    "sharing": sharing,
                }
                records.append(record)
                runner.save(output / "results.json", records)
                print(json.dumps({k: v for k, v in record.items() if k != "sharing"}), flush=True)
        runner.save(
            output / "status.json",
            {
                "stage": "finished",
                "completed_sites": len(records),
                "hardware_profile": args.hardware_profile,
            },
        )
    except BaseException as error:
        runner.save(output / "status.json", {"stage": "failed", "error": str(error)})
        raise
    finally:
        after = fingerprints()
        runner.save(output / "cache-after.json", after)
        assert before == after, "Saved cache changed"


if __name__ == "__main__":
    main()
