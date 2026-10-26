"""Replay one archived B1 legacy pairwise case against a selected checkout."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from time import perf_counter

INPUTS = (
    "data/ais/chesapeake_2023-07-15T1200Z.csv",
    "data/chart-aids/chesapeake-enc-aids.csv",
    "data/sites/b1-b5.csv",
    "data/maps/chesapeake-water-mask-500m.npz",
)
SOURCES = (
    "tools/run_ais_enc_pilot.py",
    "bearing_localization/scene.py",
    "bearing_localization/simulator/runner.py",
    "bearing_localization/search/__init__.py",
    "bearing_localization/search/pairwise.py",
    "bearing_localization/algorithms.py",
    "bearing_localization/search/association.py",
    "maritime_localization_shared/geospatial/raster_los.py",
)


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_sha(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location("_pilot", root / "tools/run_ais_enc_pilot.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load the archived B1 pilot helpers.")
    pilot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pilot)
    from bearing_localization.scene import ShipPose
    from bearing_localization.search import PairwiseSearchConfig, run_pairwise_search
    from bearing_localization.simulator.runner import run_simulator

    scene, map_input, sites, bounds = pilot.load_inputs()
    east, north = sites["B1"]
    if not pilot.RasterVisibility(map_input).is_water(east, north):
        raise RuntimeError("B1 is not water in the original mask")
    trial = pilot.replace(scene, config=pilot.replace(scene.config, visibility_radius_m=10000.0))
    simulated = run_simulator(
        trial,
        ShipPose(east, north, 0.0),
        seed=pilot.SEED_BASE,
        trial_id="B1-r1",
        angular_resolution_deg=0.1,
        map_input=map_input,
    )
    aids = tuple(x for x in trial.landmarks if not x.has_ais)
    catalog = pilot.candidate_snapshot(simulated, aids)
    measurement_sha = stable_sha(
        {
            "trial": simulated.measurements.trial_id,
            "timestamp": simulated.measurements.timestamp_utc,
            "observations": [
                {"type": x.object_type, "bearing": x.bearing_deg, "sigma": x.bearing_sigma_deg}
                for x in simulated.measurements.observations
            ],
        }
    )
    catalog_sha = stable_sha(
        {
            "timestamp": catalog.timestamp_utc,
            "crs": catalog.crs,
            "records": [
                {
                    "id": x.record_id,
                    "east": x.east_m,
                    "north": x.north_m,
                    "type": x.ais_class,
                }
                for x in catalog.records
            ],
        }
    )
    cfg = PairwiseSearchConfig(
        visibility_radius_m=10000.0, use_types=True, penalize_missing_ais=True, max_hypotheses=20000
    )
    start = perf_counter()
    try:
        # Search receives no truth, identity labels, or observer pose.
        estimate = run_pairwise_search(
            simulated.measurements,
            catalog,
            bounds,
            cfg,
            visibility=pilot.RasterVisibility(map_input),
        )
        status, error = "returned_estimate", None
    except Exception as exc:
        estimate, status, error = None, "failed", {"type": type(exc).__name__, "message": str(exc)}
    wall_s = perf_counter() - start
    result = {
        "repo_root": str(root),
        "commit": subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip(),
        "site": "B1",
        "replicate": 1,
        "seed": pilot.SEED_BASE,
        "method": "public_v1_run_pairwise_search",
        "status": status,
        "error": error,
        "settings": {
            "range_m": 10000.0,
            "sigma_deg": 0.5,
            "merge_deg": 0.1,
            "types": True,
            "unmatched_penalty_deg": cfg.unmatched_penalty_deg,
            "min_crossing_angle_deg": cfg.min_crossing_angle_deg,
            "max_hypotheses": cfg.max_hypotheses,
            "max_visibility_hypotheses": cfg.max_visibility_hypotheses,
            "refine_starts": cfg.refine_starts,
            "max_pair_sources": cfg.max_pair_sources,
            "max_neighbors_per_source": cfg.max_neighbors_per_source,
            "bounds_xy_m": list(bounds),
            "backend": "CPU",
            "objective": "legacy corrected absolute wrapped residual, injective assignment",
        },
        "counts": {
            "measurements": len(simulated.measurements.observations),
            "catalog": len(catalog.records),
            "ais": len(simulated.ais_snapshot.records),
            "aids": len(aids),
            "visible_ais_reports": sum(
                x.ais_record_id is not None and x.observation_index is not None
                for x in simulated.truth.objects
            ),
            "visible_aid_reports": sum(
                x.object_type == "aid" and x.observation_index is not None
                for x in simulated.truth.objects
            ),
        },
        "fingerprints": {
            "measurements": measurement_sha,
            "catalog": catalog_sha,
            "files": {p: sha(root / p) for p in INPUTS},
            "sources": {p: sha(root / p) for p in SOURCES},
        },
        "search_wall_elapsed_s": wall_s,
    }
    if estimate is not None:
        m = pilot.metrics_row(estimate, simulated, "B1", 1, pilot.SEED_BASE, "pairwise_hypothesis")
        result["estimate"] = {
            "east_m": estimate.east_m,
            "north_m": estimate.north_m,
            "legacy_score": estimate.score,
            "algorithm_elapsed_s": estimate.elapsed_s,
            "position_error_m": m["position_error_m"],
            "correct_ais_associations": m["correct_ais_associations"],
            "matched_chart_aids": m["matched_chart_aids"],
            "false_ais_associations": m["false_ais_associations"],
            "success_250m_3_ais": m["success_250m_3_ais"],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
