"""Approved regional Charleston gate, then coast-wide Charleston only if it wins.

J.C. Vaught. All trials share frozen observations and preloaded GPU maps.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics
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
    args = parser.parse_args()
    if not args.execute:
        print(
            json.dumps(
                {
                    "executed": False,
                    "regional_pairs": 5,
                    "coast_gate": (
                        "identical outputs, median speedup >5%, wins >=4/5 pairs, actual handoffs"
                    ),
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
    frozen = REPO / "docs/verification/charleston-persistent-pair-20261030/simulation"
    measurements = runner.read(frozen / "measurements.json")
    bearings = np.asarray(
        [
            m["bearing_deg"]
            for m in measurements["measurements"]
            if m["object_type"] == "physical_aid"
        ]
    )
    tolerances = np.full(len(bearings), settings["marker_tolerance_deg"])
    runner.save(output / "measurements.json", measurements)
    runner.save(
        output / "protocol.json",
        {
            "source_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            "observations_sha256": runner.digest(frozen / "measurements.json"),
            "gate": (
                "All saved arrays and selected estimates identical; "
                "median paired speedup >1.05; at least4/5 wins; at leastone real handoff"
            ),
            "warmups_per_stage": 2,
            "pairs_per_stage": 5,
            "order": "Alternating fixed/dynamic and dynamic/fixed",
            "devices": [0, 1, 2],
            "land_pruning_changed": False,
        },
    )

    def stage(name: str, search: dict[str, Any]):
        directory = output / name
        directory.mkdir()
        search = {**search, "devices": (0, 1, 2), "share_terminal_work": True}
        config = ResidentConfig(**search)
        runner.save(directory / "config.json", asdict(config))
        records: list[dict[str, Any]] = []

        def factory(prepared, cfg, device):
            return ProfiledResidentDevice(prepared, cfg, device, sample_every=10)

        with ResidentSearchSession(world, config, device_factory=factory) as session:
            preload = directory / "preload"
            preload.mkdir()
            with session.request(world, config):
                session.execute_workers(lambda device, resident: None, preload / "search", Event())
            reference_search = None
            reference_estimate = None
            order = [("warm-fixed", False, None), ("warm-dynamic", True, None)]
            for pair in range(5):
                modes = (False, True) if pair % 2 == 0 else (True, False)
                order.extend(
                    (f"pair-{pair + 1}-{'dynamic' if mode else 'fixed'}", mode, pair + 1)
                    for mode in modes
                )
            for label, sharing, pair in order:
                trial = directory / label
                trial.mkdir()
                runner.save(output / "status.json", {"stage": name, "trial": label})
                summary = run_resident_search(
                    world,
                    config,
                    bearings,
                    tolerances,
                    trial / "search",
                    session=session,
                    sharing=sharing,
                )
                assert summary["complete"]
                assert all(d["reused"] for d in summary["resident_session"]["devices"])
                runner.bounded_command(
                    runner.command(
                        "tools.select_resident_result",
                        trial / "search",
                        trial / "estimate.json",
                        "--device",
                        "0",
                    ),
                    trial,
                    "selection",
                    120,
                )
                runner.save(
                    trial / "estimate-freeze.json",
                    {"estimate_sha256": runner.digest(trial / "estimate.json")},
                )
                accuracy = runner.geodesic_after_freeze(
                    trial / "estimate.json", trial / "estimate-freeze.json", frozen / "truth.json"
                )
                runner.save(trial / "accuracy.json", accuracy)
                estimate = runner.read(trial / "estimate.json")["estimate"]
                if reference_search is None:
                    reference_search, reference_estimate = trial / "search", estimate
                else:
                    assert estimate == reference_estimate, "Selected estimate changed"
                    compare_terminal_outputs(reference_search, trial / "search")
                counts = {d: 0 for d in config.devices}
                for leaf in (trial / "search").rglob("leaf-*.npz"):
                    with np.load(leaf, allow_pickle=False) as data:
                        counts[int(leaf.parent.name.rsplit("-", 1)[1])] += len(data["status"])
                record = {
                    "trial": label,
                    "pair": pair,
                    "dynamic": sharing,
                    "search_s": summary["elapsed_s"],
                    "error_m": accuracy["position_error_m"],
                    "terminal_by_device": counts,
                    "outputs_identical": True,
                    "sharing": summary.get("dynamic_score_sharing", {}),
                }
                records.append(record)
                runner.save(directory / "results.json", records)
                print(
                    json.dumps(
                        {
                            k: record[k]
                            for k in ("trial", "search_s", "error_m", "terminal_by_device")
                        }
                    ),
                    flush=True,
                )
            fixed = [r for r in records if r["pair"] is not None and not r["dynamic"]]
            dynamic = [r for r in records if r["pair"] is not None and r["dynamic"]]
            wins = sum(
                next(r["search_s"] for r in dynamic if r["pair"] == old["pair"]) < old["search_s"]
                for old in fixed
            )
            old_median = statistics.median(r["search_s"] for r in fixed)
            new_median = statistics.median(r["search_s"] for r in dynamic)
            moved = sum(r["sharing"].get("handoffs", 0) for r in dynamic)
            gate = {
                "stage": name,
                "fixed_median_s": old_median,
                "dynamic_median_s": new_median,
                "speedup": old_median / new_median,
                "pair_wins": wins,
                "actual_handoffs": moved,
                "outputs_identical": True,
                "passed": old_median / new_median > 1.05 and wins >= 4 and moved > 0,
            }
            runner.save(directory / "gate.json", gate)
            return gate

    try:
        regional = runner.read(REPO / "docs/verification/charleston-blind-regional-result.json")[
            "search_config"
        ]
        regional.update(batch_cells=512, roots_per_task=128, checkpoint_every_batches=100)
        first = stage("regional", regional)
        gates = [first]
        if first["passed"]:
            gates.append(stage("coastwide", settings["search"]))
        runner.save(
            output / "decision.json",
            {
                "gates": gates,
                "coastwide_executed": first["passed"],
                "default_scheduler_changed": False,
                "new_land_pruning": False,
            },
        )
        runner.save(output / "status.json", {"stage": "finished", "gates": gates})
    finally:
        after = fingerprints()
        runner.save(output / "cache-after.json", after)
        assert before == after, "Saved cache changed"


if __name__ == "__main__":
    main()
