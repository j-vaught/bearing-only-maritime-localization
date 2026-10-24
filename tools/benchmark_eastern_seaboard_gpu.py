#!/usr/bin/env python3
"""Benchmark coast-wide candidate scoring against the committed AIS snapshot.

The benchmark uses a seeded B1 simulated bearing set and a 10 km range gate.
It times only the batched bearing-score stage over a rectangular East Coast
candidate grid; candidate generation, water filtering, and ray tracing are not
included.
"""

from __future__ import annotations

import argparse
import csv
import math
import platform
import runpy
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MIN_LAT, MAX_LAT = 24.5, 45.5
MIN_LON, MAX_LON = -82.0, -66.0
R_EARTH_M = 6_371_008.8
VISIBILITY_RANGE_M = 10_000.0
UNMATCHED_PENALTY_DEG = 12.0


def candidate_grid(spacing_m: float) -> tuple[np.ndarray, np.ndarray]:
    """Return cell-center latitude/longitude arrays at near-uniform spacing."""
    lat_step = spacing_m / 111_195.0
    latitudes = np.arange(MIN_LAT + lat_step / 2, MAX_LAT, lat_step, dtype=np.float32)
    all_lats: list[np.ndarray] = []
    all_lons: list[np.ndarray] = []
    for latitude in latitudes:
        lon_step = spacing_m / (111_195.0 * math.cos(math.radians(float(latitude))))
        longitudes = np.arange(MIN_LON + lon_step / 2, MAX_LON, lon_step, dtype=np.float32)
        all_lats.append(np.full(len(longitudes), latitude, dtype=np.float32))
        all_lons.append(longitudes)
    return np.concatenate(all_lats), np.concatenate(all_lons)


def load_inputs() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Load the AIS catalog and deterministic B1 sensor reports."""
    with (ROOT / "data/ais/eastern-seaboard_2023-07-15T1200Z.csv").open(
        newline="", encoding="utf-8"
    ) as stream:
        ais_rows = list(csv.DictReader(stream))
    with (ROOT / "data/chart-aids/chesapeake-enc-aids.csv").open(
        newline="", encoding="utf-8"
    ) as stream:
        aid_rows = list(csv.DictReader(stream))

    ais_lats = np.asarray([float(row["latitude_deg"]) for row in ais_rows], dtype=np.float32)
    ais_lons = np.asarray([float(row["longitude_deg"]) for row in ais_rows], dtype=np.float32)
    aid_lats = np.asarray([float(row["latitude_deg"]) for row in aid_rows], dtype=np.float32)
    aid_lons = np.asarray([float(row["longitude_deg"]) for row in aid_rows], dtype=np.float32)
    object_lats = np.concatenate((ais_lats, aid_lats))
    object_lons = np.concatenate((ais_lons, aid_lons))
    object_types = np.concatenate(
        (np.zeros(len(ais_lats), dtype=np.int64), np.ones(len(aid_lats), dtype=np.int64))
    )

    module = runpy.run_path(str(ROOT / "tools/run_ais_enc_pilot.py"))
    scene, map_input, sites, _ = module["load_inputs"]()
    simulator = module["run_simulator"]
    ship_pose = module["ShipPose"]
    simulated = simulator(
        scene,
        ship_pose(*sites["B1"], 0.0),
        seed=module["SEED_BASE"],
        trial_id="B1-east-coast-gpu-benchmark",
        angular_resolution_deg=module["ANGULAR_RESOLUTION_DEG"],
        map_input=map_input,
    )
    observations = simulated.measurements.observations
    bearings = np.asarray([item.bearing_deg for item in observations], dtype=np.float32)
    observation_types = np.asarray(
        [0 if item.object_type == "vessel" else 1 for item in observations], dtype=np.int64
    )
    if len(bearings) < 2:
        raise RuntimeError("The seeded B1 case must contain at least two detections.")
    return object_lats, object_lons, object_types, np.column_stack((bearings, observation_types))


def run_gpu_grid(
    latitudes: np.ndarray,
    longitudes: np.ndarray,
    object_latitudes: np.ndarray,
    object_longitudes: np.ndarray,
    object_types: np.ndarray,
    observations: np.ndarray,
    *,
    device,
    batch_size: int,
) -> tuple[np.ndarray, float]:
    """Score every candidate with spherical bearings and a 10 km range gate."""
    torch = __import__("torch")
    candidate_lat = torch.as_tensor(latitudes, dtype=torch.float32)
    candidate_lon = torch.as_tensor(longitudes, dtype=torch.float32)
    target_lat = torch.as_tensor(object_latitudes, dtype=torch.float32, device=device).deg2rad()
    target_lon = torch.as_tensor(object_longitudes, dtype=torch.float32, device=device).deg2rad()
    target_type = torch.as_tensor(object_types, dtype=torch.int64, device=device)
    obs_bearing = torch.as_tensor(observations[:, 0], dtype=torch.float32, device=device)
    obs_type = torch.as_tensor(observations[:, 1], dtype=torch.int64, device=device)
    torch.cuda.synchronize(device)
    started = time.perf_counter()
    candidate_lat = candidate_lat.to(device)
    candidate_lon = candidate_lon.to(device)
    scores_device = torch.empty((len(latitudes),), dtype=torch.float32, device=device)
    for lo in range(0, len(latitudes), batch_size):
        hi = min(lo + batch_size, len(latitudes))
        lat1 = candidate_lat[lo:hi].deg2rad()[:, None]
        lon1 = candidate_lon[lo:hi].deg2rad()[:, None]
        dlat = target_lat[None, :] - lat1
        dlon = target_lon[None, :] - lon1
        haversine = (
            torch.sin(dlat / 2).square()
            + torch.cos(lat1) * torch.cos(target_lat[None, :]) * torch.sin(dlon / 2).square()
        ).clamp_(0.0, 1.0)
        distance_m = 2.0 * R_EARTH_M * torch.asin(torch.sqrt(haversine))
        y = torch.sin(dlon) * torch.cos(target_lat[None, :])
        x = torch.cos(lat1) * torch.sin(target_lat[None, :]) - (
            torch.sin(lat1) * torch.cos(target_lat[None, :]) * torch.cos(dlon)
        )
        predicted = torch.remainder(torch.rad2deg(torch.atan2(y, x)), 360.0)
        in_range = distance_m <= VISIBILITY_RANGE_M
        score = torch.zeros((hi - lo,), dtype=torch.float32, device=device)
        for bearing, kind in zip(obs_bearing, obs_type, strict=True):
            valid = in_range & (target_type[None, :] == kind)
            error = torch.abs(torch.remainder(predicted - bearing + 180.0, 360.0) - 180.0)
            error = error.masked_fill(~valid, float("inf"))
            best = torch.amin(error, dim=1)
            score += torch.where(
                torch.isfinite(best),
                torch.clamp(best, max=UNMATCHED_PENALTY_DEG),
                UNMATCHED_PENALTY_DEG,
            )
        scores_device[lo:hi] = score
    torch.cuda.synchronize(device)
    scores = scores_device.cpu().numpy()
    torch.cuda.synchronize(device)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return scores, elapsed_ms


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument(
        "--spacings-m", nargs="+", type=float, default=[10_000, 5_000, 2_000, 1_000]
    )
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "results/eastern-seaboard-gpu/scaling.csv",
    )
    args = parser.parse_args()
    torch = __import__("torch")
    device = torch.device(args.device)
    if device.type != "cuda":
        raise ValueError("This benchmark requires a CUDA device.")
    object_lats, object_lons, object_types, observations = load_inputs()
    print(
        f"HOST {platform.node()} DEVICE {torch.cuda.get_device_name(device)} "
        f"AIS_OBJECTS {len(object_lats) - 427} LOCAL_CHART_AIDS 427 "
        f"TOTAL_CATALOG {len(object_lats)} B1_REPORTS {len(observations)}",
        flush=True,
    )

    warm_lat = np.asarray([38.0, 38.1], dtype=np.float32)
    warm_lon = np.asarray([-76.5, -76.4], dtype=np.float32)
    run_gpu_grid(
        warm_lat,
        warm_lon,
        object_lats,
        object_lons,
        object_types,
        observations,
        device=device,
        batch_size=args.batch_size,
    )

    rows: list[dict[str, object]] = []
    for spacing_m in args.spacings_m:
        latitudes, longitudes = candidate_grid(spacing_m)
        repeat_times: list[float] = []
        final_scores: np.ndarray | None = None
        for repeat in range(args.repeats):
            scores, elapsed = run_gpu_grid(
                latitudes,
                longitudes,
                object_lats,
                object_lons,
                object_types,
                observations,
                device=device,
                batch_size=args.batch_size,
            )
            repeat_times.append(elapsed)
            final_scores = scores
            print(
                f"SPACING_M {spacing_m:g} CANDIDATES {len(scores):,} "
                f"REPEAT {repeat + 1} GPU_SCORE_MS {elapsed:.3f}",
                flush=True,
            )
        assert final_scores is not None
        rows.append(
            {
                "spacing_m": spacing_m,
                "candidate_count": len(latitudes),
                "object_count": len(object_lats),
                "observation_count": len(observations),
                "batch_size": args.batch_size,
                "visibility_range_m": VISIBILITY_RANGE_M,
                "median_gpu_score_ms": float(np.median(repeat_times)),
                "p95_gpu_score_ms": float(np.percentile(repeat_times, 95)),
                "repeat_times_ms": ";".join(f"{value:.3f}" for value in repeat_times),
                "best_score_deg": float(final_scores.min()),
            }
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"CSV {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
