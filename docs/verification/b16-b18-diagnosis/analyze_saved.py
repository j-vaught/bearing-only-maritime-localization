"""Inspect saved B16/B18 data and profiles without localization searches. J.C. Vaught."""

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from pyproj import Geod, Transformer

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "docs/verification/b16-b18-diagnosis"
OUTPUT.mkdir(parents=True, exist_ok=True)
RUN = ROOT / "runs/b-location-suite-20cm-eight-20261029"
row = json.loads((RUN / "B18/result.json").read_text())
p = Path(row["attempt_directory"])
selected = json.loads((p / "estimate.json").read_text())["estimate"]
truth = json.loads((p / "truth.json").read_text())
obs = json.loads((p / "measurements.json").read_text())["measurements"]
diag = json.loads((p / "simulation-diagnostics.json").read_text())
shared = json.loads((RUN / "shared-preparation.json").read_text())
with np.load(shared["cache_paths"][1], allow_pickle=False) as data:
    header = json.loads(data["header"].tobytes())
    keys, objects = header["object_keys"], data["objects"]
lookup = dict(zip(keys, objects, strict=True))
groups = {g["observation_index"]: g for g in diag["groups"] if g["detected"]}
indexes = [i for i, o in enumerate(obs) if o["object_type"] == "physical_aid"]
generating = [groups[i]["representative_catalog_key"] for i in indexes]
points = np.asarray([lookup[k][2:4] for k in generating])
z = np.asarray([obs[i]["bearing_deg"] for i in indexes])
geod = Geod(ellps="WGS84")
angles, _, ranges = geod.inv(
    np.full(len(z), truth["longitude_deg"]),
    np.full(len(z), truth["latitude_deg"]),
    points[:, 0],
    points[:, 1],
)
noise = (z - angles + 180) % 360 - 180
selected_angles, _, _ = geod.inv(
    np.full(len(z), selected["longitude_deg"]),
    np.full(len(z), selected["latitude_deg"]),
    points[:, 0],
    points[:, 1],
)
selected_residual = (z - selected_angles + 180) % 360 - 180
az = np.deg2rad(angles)
east, north = ranges * np.sin(az), ranges * np.cos(az)
jacobian = np.column_stack((-north / ranges**2, east / ranges**2))
eigenvalues, vectors = np.linalg.eigh(jacobian.T @ jacobian)
axis_sigma = np.deg2rad(1 / 6) / np.sqrt(eigenvalues)
error_azimuth, _, distance = geod.inv(
    truth["longitude_deg"],
    truth["latitude_deg"],
    selected["longitude_deg"],
    selected["latitude_deg"],
)
error_vector = distance * np.asarray(
    [math.sin(math.radians(error_azimuth)), math.cos(math.radians(error_azimuth))]
)
consistent, residuals = [], []
for leaf in (p / "search").rglob("leaf-*.npz"):
    with np.load(leaf, allow_pickle=False) as data:
        mask = (data["status"] == 0) & (data["flags"] == 0)
        cells = data["cells"][mask]
        consistent.append((cells[:, :2] + cells[:, 2:]) / 2)
        residuals.append(data["residual_deg"][mask])
xy = np.concatenate(consistent)
residuals = np.concatenate(residuals)
lon, lat = Transformer.from_crs(32618, 4326, always_xy=True).transform(xy[:, 0], xy[:, 1])
_, _, distances = geod.inv(
    np.full(len(lon), truth["longitude_deg"]), np.full(len(lon), truth["latitude_deg"]), lon, lat
)
nearest = int(np.argmin(distances))
result: dict[str, Any] = {
    "author": "J.C. Vaught",
    "scope": (
        "Posthoc bearing geometry and saved candidate/profile inspection only. No "
        "search, simulation, visibility query, or assignment rerun."
    ),
    "B18": {
        "aid_count": len(z),
        "correct_generating_correspondences": sum(
            a == b for a, b in zip(generating, selected["matched_object_keys"], strict=True)
        ),
        "range_m_min_median_max": np.percentile(ranges, [0, 50, 100]).tolist(),
        "truth_generating_residual_sum_deg": float(np.abs(noise).sum()),
        "selected_residual_sum_deg_from_saved_positions": float(np.abs(selected_residual).sum()),
        "saved_selected_residual_sum_deg": selected["sum_absolute_bearing_residual_deg"],
        "max_absolute_angular_noise_deg": float(np.max(np.abs(noise))),
        "geometry_information_eigenvalues": eigenvalues.tolist(),
        "geometry_condition_ratio": float(eigenvalues[-1] / eigenvalues[0]),
        "linearized_geometry_sigma_axes_m": axis_sigma.tolist(),
        "linearized_geometry_note": (
            "Local small-angle unbounded-Gaussian model using underlying sigma1/6degree, "
            "known associations. Descriptive sensitivity, not a certificate or "
            "calibrated uncertainty interval."
        ),
        "error_weak_direction_alignment": float(
            abs(np.dot(error_vector, vectors[:, 0])) / distance
        ),
        "error_east_north_m": error_vector.tolist(),
        "consistent_centers": len(xy),
        "consistent_center_error_m_min_median_max": np.percentile(distances, [0, 50, 100]).tolist(),
        "nearest_truth_saved_consistent_center_error_m": float(distances[nearest]),
        "nearest_truth_saved_consistent_center_residual_sum_deg": float(residuals[nearest]),
        "consistent_center_extent_en_m": [*xy.min(axis=0), *xy.max(axis=0)],
    },
}
b16 = ROOT / "runs/b-location-suite-aids16-retry-20261029/B16/attempt-20261029T214235983174Z"
profiles = []
for path in sorted((b16 / "profile").glob("gpu-*-summary.json")):
    profile = json.loads(path.read_text())
    top = sorted(
        profile["by_label"], key=lambda r: r["event_stream_exclusive_ms_total"], reverse=True
    )[:6]
    profiles.append(
        {
            "device": profile["device"],
            "sampling": profile["sampling"],
            "total_sampled_stream_ms": profile["root_and_exclusive_totals"]["event_stream_ms"],
            "top_exclusive": [
                {k: r[k] for k in ("label", "count", "event_stream_exclusive_ms_total")}
                for r in top
            ],
        }
    )
last_progress = {}
for line in (b16 / "search/progress.jsonl").read_text().splitlines():
    d = json.loads(line)
    last_progress[d["device"]] = d
per_device = {i: 0 for i in (0, 1, 2)}
for leaf in (b16 / "search").rglob("leaf-*.npz"):
    device = int(leaf.parent.name.rsplit("-", 1)[1])
    with np.load(leaf, allow_pickle=False) as data:
        per_device[device] += len(data["status"])
result["B16"] = {
    "source": str(b16),
    "earlier_catalog_result": True,
    "summary": json.loads((b16 / "search/summary.json").read_text()),
    "terminal_centers_by_device": per_device,
    "last_progress_by_device": last_progress,
    "profiles": profiles,
}
for name in ("estimate.json", "truth.json", "measurements.json"):
    result.setdefault("B18_source_sha256", {})[name] = hashlib.sha256(
        (p / name).read_bytes()
    ).hexdigest()
(OUTPUT / "saved-data-diagnosis.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
