"""Explain saved slow-site profiles without searches or visibility queries. J.C. Vaught."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from pyproj import Geod, Transformer

ROOT = Path(__file__).resolve().parents[3]
RUN = ROOT / "runs/b-location-suite-preloaded-20261030T005811Z"
OUTPUT = ROOT / "docs/verification/slow-preloaded-sites-20261030"
rows = json.loads((RUN / "results.json").read_text())
shared = json.loads((RUN / "shared-preparation.json").read_text())
with np.load(shared["cache_paths"][0], allow_pickle=False) as data:
    barrier_count = len(data["barriers"])
with np.load(shared["cache_paths"][1], allow_pickle=False) as data:
    header = json.loads(data["header"].tobytes())
    lookup = dict(zip(header["object_keys"], data["objects"], strict=True))
geod = Geod(ellps="WGS84")
inverse = Transformer.from_crs(32618, 4326, always_xy=True)
records: list[dict[str, Any]] = []
for row in rows:
    attempt = Path(row["attempt_directory"])
    progress = [
        json.loads(line) for line in (attempt / "search/progress.jsonl").read_text().splitlines()
    ]
    per_device: dict[int, dict[str, Any]] = {
        d: {"device": d, "terminal_centers": 0} for d in (0, 1, 2)
    }
    centers, states, flags, reasons = [], [], [], []
    unresolved_nominal_complete = 0
    for leaf in (attempt / "search").rglob("leaf-*.npz"):
        device = int(leaf.parent.name.rsplit("-", 1)[1])
        with np.load(leaf, allow_pickle=False) as data:
            per_device[device]["terminal_centers"] += len(data["status"])
            centers.append((data["cells"][:, :2] + data["cells"][:, 2:]) / 2)
            states.append(data["status"])
            flags.append(data["flags"])
            reasons.append(data["marker_rejection_reason"])
            unresolved = data["status"] == 2
            ids = data["matched_records"]
            complete = np.all(ids >= 0, axis=1)
            unresolved_nominal_complete += int(np.count_nonzero(unresolved & complete))
    xy, status, flag, reason = map(np.concatenate, (centers, states, flags, reasons))
    assert len(xy) == row["terminal"]
    truth = json.loads((attempt / "truth.json").read_text())
    longitude, latitude = inverse.transform(xy[:, 0], xy[:, 1])
    _, _, distances = geod.inv(
        np.full(len(xy), truth["longitude_deg"]),
        np.full(len(xy), truth["latitude_deg"]),
        longitude,
        latitude,
    )
    measurements = json.loads((attempt / "measurements.json").read_text())["measurements"]
    diagnostics = json.loads((attempt / "simulation-diagnostics.json").read_text())
    groups = {g["observation_index"]: g for g in diagnostics["groups"] if g["detected"]}
    aid_indices = [i for i, m in enumerate(measurements) if m["object_type"] == "physical_aid"]
    targets = np.asarray(
        [lookup[groups[i]["representative_catalog_key"]][2:4] for i in aid_indices]
    )
    bearings, _, ranges = geod.inv(
        np.full(len(targets), truth["longitude_deg"]),
        np.full(len(targets), truth["latitude_deg"]),
        targets[:, 0],
        targets[:, 1],
    )
    azimuth = np.sort(bearings % 360)
    covering_arc = 360 - float(np.max(np.diff(np.r_[azimuth, azimuth[0] + 360])))
    for device, metrics in per_device.items():
        last = next(p for p in reversed(progress) if p["device"] == device)
        metrics["last_batch_elapsed_s"] = last["elapsed_s"]
        profile = json.loads((attempt / f"profile/gpu-{device}-summary.json").read_text())
        total = profile["root_and_exclusive_totals"]["event_stream_ms"]
        labels = {p["label"]: p["event_stream_exclusive_ms_total"] for p in profile["by_label"]}
        metrics["sampled_stream_ms"] = total
        metrics["prediction_pair_pct_of_sampled_stream"] = (
            100 * labels.get("kernel.prediction.pp_pairs", 0) / total
        )
        metrics["assignment_pct_of_sampled_stream"] = (
            100 * labels.get("assignment.cooperative", 0) / total
        )
        metrics["sampled_prediction_cells"] = 0
        metrics["sampled_candidate_object_pairs"] = 0
        for line in (attempt / f"profile/gpu-{device}-spans.jsonl").read_text().splitlines():
            span = json.loads(line)
            if span["label"] != "kernel.prediction.pp_pairs":
                continue
            arrays = {a["index"]: a["shape"] for a in span["dimensions"]["arrays"]}
            metrics["sampled_prediction_cells"] += arrays[0][0]
            metrics["sampled_candidate_object_pairs"] += arrays[1][0]
        n = metrics["sampled_prediction_cells"]
        metrics["sampled_gathered_objects_per_center"] = (
            metrics["sampled_candidate_object_pairs"] / n if n else None
        )
    bins = np.floor(xy / 5000).astype(np.int64)
    _, membership, counts = np.unique(bins, axis=0, return_inverse=True, return_counts=True)
    clusters = []
    for index in np.argsort(counts)[-3:][::-1]:
        east, north = xy[membership == index].mean(axis=0)
        lon, lat = inverse.transform(east, north)
        clusters.append(
            {
                "terminal_centers": int(counts[index]),
                "mean_latitude_deg": float(lat),
                "mean_longitude_deg": float(lon),
            }
        )
    critical = max(per_device.values(), key=lambda p: p["last_batch_elapsed_s"])
    flags_unresolved = Counter(map(int, flag[status == 2]))
    records.append(
        {
            "site": row["site_id"],
            "port": row["port"],
            "search_s": row["search_s"],
            "aids": row["positive_marker_reports"],
            "terminal_centers": len(xy),
            "consistent_centers": row["consistent"],
            "unresolved_centers": row["unresolved"],
            "unresolved_flag_histogram": dict(flags_unresolved),
            "unresolved_nominal_complete_assignments": unresolved_nominal_complete,
            "consistent_centers_farther_than_1km_from_truth": int(
                np.count_nonzero((status == 0) & (distances > 1000))
            ),
            "largest_projected_5km_bins": clusters,
            "center_rejection_histogram": dict(Counter(map(int, reason))),
            "terminal_distance_to_truth_m_min_median_max": np.percentile(
                distances, [0, 50, 100]
            ).tolist(),
            "terminal_centers_within_1km_of_truth": int(np.count_nonzero(distances <= 1000)),
            "terminal_xy_span_m": np.ptp(xy, axis=0).tolist(),
            "detected_aid_range_m_min_median_max": np.percentile(ranges, [0, 50, 100]).tolist(),
            "detected_aid_minimum_covering_arc_deg": covering_arc,
            "critical_gpu": critical["device"],
            "critical_gpu_terminal_fraction": critical["terminal_centers"] / len(xy),
            "devices": list(per_device.values()),
        }
    )
OUTPUT.mkdir(exist_ok=True, parents=True)
result = {
    "author": "J.C. Vaught",
    "scope": (
        "Saved data and source inspection only. No searches, ray queries, "
        "assignment or simulation reruns."
    ),
    "barrier_rectangles_in_saved_map_cache": barrier_count,
    "profiling_semantics": (
        "Every tenth batch; CUDA stream elapsed includes host launch gaps "
        "and is not pure kernel busy time."
    ),
    "sites": sorted(records, key=lambda row: row["search_s"], reverse=True),
}
(OUTPUT / "diagnosis.json").write_text(json.dumps(result, indent=2) + "\n")
for r in result["sites"]:
    if r["search_s"] < 4 and r["site"] != "B17":
        continue
    print(json.dumps(r))
print("Saved early barrier rectangle count", barrier_count)
