"""Measure true-north versus full-map-frame ray directions for frozen catalog inputs."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from math import ceil
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from pyproj import Geod, Transformer

from maritime_localization_shared.geospatial.coordinates import LocalFrame

ROOT = Path("/home/jvaught2/projects/bearing-only-maritime-localization")
FROZEN_INPUTS = ROOT / ".agent-work/outputs/a26-wedge-complete-07d8036/static-inputs"
_GEOD = Geod(ellps="WGS84")


@dataclass(frozen=True, slots=True)
class DirectionError:
    """Signed true-bearing minus projected-grid-bearing audit row."""

    source_key: str
    source_lon_deg: float
    source_lat_deg: float
    true_bearing_deg: float
    ray_initial_bearing_deg: float
    grid_bearing_deg: float
    signed_error_deg: float
    endpoint_lon_deg: float
    endpoint_lat_deg: float


def _wrapped_angle(angle_deg: NDArray[np.float64]) -> NDArray[np.float64]:
    return (angle_deg + 180.0) % 360.0 - 180.0


def _event_stats(rows: list[DirectionError], *, claimed_bound_deg: float = 0.5) -> dict[str, Any]:
    if not rows:
        return {"count": 0, "min_signed_deg": None, "max_signed_deg": None, "p95_abs_deg": None}
    errors = np.asarray([row.signed_error_deg for row in rows], dtype=np.float64)
    order = np.argsort(np.abs(errors), kind="stable")
    p95_index = min(len(rows) - 1, ceil(0.95 * len(rows)) - 1)
    p95_row = rows[int(order[p95_index])]
    max_row = rows[int(order[-1])]
    return {
        "count": len(rows),
        "min_signed_deg": float(np.min(errors)),
        "max_signed_deg": float(np.max(errors)),
        "max_abs_deg": float(abs(max_row.signed_error_deg)),
        "max_abs_event": asdict(max_row),
        "p95_abs_deg_nearest_rank": float(abs(p95_row.signed_error_deg)),
        "p95_event_nearest_rank": asdict(p95_row),
        "bound_violations": int(np.count_nonzero(np.abs(errors) > claimed_bound_deg)),
    }


def _bearing_rows(
    *,
    frame: LocalFrame,
    keys: tuple[str, ...],
    lon_deg: NDArray[np.float64],
    lat_deg: NDArray[np.float64],
    true_bearing_deg: float,
    ray_distance_m: float,
) -> list[DirectionError]:
    endpoint_lon, endpoint_lat, _ = _GEOD.fwd(
        lon_deg,
        lat_deg,
        np.full(lon_deg.shape, true_bearing_deg),
        np.full(lon_deg.shape, ray_distance_m),
    )
    true_bearing_to_source, _, _ = _GEOD.inv(endpoint_lon, endpoint_lat, lon_deg, lat_deg)
    true_bearing_to_source = np.asarray(true_bearing_to_source, dtype=np.float64) % 360.0
    transformer = Transformer.from_crs("EPSG:4326", frame.projected_crs, always_xy=True)
    source_x, source_y = transformer.transform(lon_deg, lat_deg)
    endpoint_x, endpoint_y = transformer.transform(endpoint_lon, endpoint_lat)
    grid_bearing = np.degrees(np.arctan2(source_x - endpoint_x, source_y - endpoint_y)) % 360.0
    errors = _wrapped_angle(true_bearing_to_source - grid_bearing)
    return [
        DirectionError(
            source_key=key,
            source_lon_deg=float(lon),
            source_lat_deg=float(lat),
            true_bearing_deg=float(true_at_endpoint),
            ray_initial_bearing_deg=float(true_bearing_deg),
            grid_bearing_deg=float(grid),
            signed_error_deg=float(error),
            endpoint_lon_deg=float(end_lon),
            endpoint_lat_deg=float(end_lat),
        )
        for key, lon, lat, true_at_endpoint, grid, error, end_lon, end_lat in zip(
            keys,
            lon_deg,
            lat_deg,
            true_bearing_to_source,
            grid_bearing,
            errors,
            endpoint_lon,
            endpoint_lat,
            strict=True,
        )
    ]


def audit_projection(
    *,
    frame: LocalFrame,
    catalog_rows: list[dict[str, Any]],
    measured_bearings_deg: list[float],
    ray_distance_m: float = 10_000.0,
    claimed_bound_deg: float = 0.5,
) -> dict[str, Any]:
    """Measure catalog-site north convergence and finite reverse-ray direction errors.

    Inputs contain geographic catalog positions and observed true-north bearings
    only. This diagnostic computes no observer pose or truth association.
    """
    if not catalog_rows:
        raise ValueError("catalog_rows must not be empty.")
    if not measured_bearings_deg:
        raise ValueError("measured_bearings_deg must not be empty.")
    if ray_distance_m <= 0.0 or claimed_bound_deg < 0.0:
        raise ValueError("ray_distance_m must be positive and claimed_bound_deg nonnegative.")
    keys = tuple(str(row["key"]) for row in catalog_rows)
    lon_deg = np.asarray([row["longitude_deg"] for row in catalog_rows], dtype=np.float64)
    lat_deg = np.asarray([row["latitude_deg"] for row in catalog_rows], dtype=np.float64)
    if not np.all(np.isfinite(lon_deg)) or not np.all(np.isfinite(lat_deg)):
        raise ValueError("catalog coordinates must be finite.")

    # Approximate the grid-north orientation locally at each source with a 1 m
    # true-north geodesic step, retaining the source location's signed error.
    north_lon, north_lat, _ = _GEOD.fwd(
        lon_deg, lat_deg, np.zeros(lon_deg.shape), np.ones(lon_deg.shape)
    )
    transformer = Transformer.from_crs("EPSG:4326", frame.projected_crs, always_xy=True)
    source_x, source_y = transformer.transform(lon_deg, lat_deg)
    north_x, north_y = transformer.transform(north_lon, north_lat)
    grid_north = np.degrees(np.arctan2(north_x - source_x, north_y - source_y)) % 360.0
    north_errors = _wrapped_angle(-grid_north)
    north_rows = [
        DirectionError(
            source_key=key,
            source_lon_deg=float(lon),
            source_lat_deg=float(lat),
            true_bearing_deg=0.0,
            ray_initial_bearing_deg=0.0,
            grid_bearing_deg=float(grid),
            signed_error_deg=float(error),
            endpoint_lon_deg=float(end_lon),
            endpoint_lat_deg=float(end_lat),
        )
        for key, lon, lat, grid, error, end_lon, end_lat in zip(
            keys, lon_deg, lat_deg, grid_north, north_errors, north_lon, north_lat, strict=True
        )
    ]
    north_stats = _event_stats(north_rows, claimed_bound_deg=claimed_bound_deg)

    # Reverse each observed bearing 180 degrees at every catalog source, then
    # compare the true geodesic ray endpoint with the straight projected chord.
    ray_groups = [
        _bearing_rows(
            frame=frame,
            keys=keys,
            lon_deg=lon_deg,
            lat_deg=lat_deg,
            true_bearing_deg=(bearing + 180.0) % 360.0,
            ray_distance_m=ray_distance_m,
        )
        for bearing in measured_bearings_deg
    ]
    all_ray_rows = [row for group in ray_groups for row in group]
    ray_stats = _event_stats(all_ray_rows, claimed_bound_deg=claimed_bound_deg)
    by_measurement = [
        {
            "measurement_bearing_deg": float(bearing),
            "reverse_true_bearing_deg": float((bearing + 180.0) % 360.0),
            **_event_stats(group, claimed_bound_deg=claimed_bound_deg),
        }
        for bearing, group in zip(measured_bearings_deg, ray_groups, strict=True)
    ]
    return {
        "frame_origin_lonlat_deg": frame.origin_lonlat,
        "frame_crs": frame.projected_crs.to_string(),
        "catalog_count": len(catalog_rows),
        "measurement_count": len(measured_bearings_deg),
        "ray_distance_m": float(ray_distance_m),
        "claimed_bound_deg": float(claimed_bound_deg),
        "source_grid_north": north_stats,
        "reverse_ray_endpoints": ray_stats,
        "by_measurement": by_measurement,
        "scope": "geometry diagnostic only; no observer truth or association used",
    }


def audit_frozen_inputs(
    catalog_path: Path,
    measurements_path: Path,
    scope_manifest_path: Path,
    *,
    ray_distance_m: float = 10_000.0,
    claimed_bound_deg: float = 0.5,
) -> dict[str, Any]:
    """Audit the frozen port-001/full-catalog input and CLI frame construction."""
    catalog = json.loads(catalog_path.read_text())
    measurements = json.loads(measurements_path.read_text())
    scope = json.loads(scope_manifest_path.read_text())
    xmin, xmax, ymin, ymax = scope["bounds_xy_m"]
    to_geo = Transformer.from_crs(scope["projected_crs"], "EPSG:4326", always_xy=True)
    origin_lon, origin_lat = to_geo.transform((xmin + xmax) / 2.0, (ymin + ymax) / 2.0)
    frame = LocalFrame((float(origin_lon), float(origin_lat)))
    result = audit_projection(
        frame=frame,
        catalog_rows=catalog["records"],
        measured_bearings_deg=[item["bearing_deg"] for item in measurements["measurements"]],
        ray_distance_m=ray_distance_m,
        claimed_bound_deg=claimed_bound_deg,
    )
    result["frozen_case"] = measurements.get("trial_id")
    result["scope_geographic_scope"] = scope.get("geographic_scope")
    result["catalog_path"] = str(catalog_path)
    result["measurements_path"] = str(measurements_path)
    result["scope_manifest_path"] = str(scope_manifest_path)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=FROZEN_INPUTS / "catalog.json")
    parser.add_argument("--measurements", type=Path, default=FROZEN_INPUTS / "measurements.json")
    parser.add_argument(
        "--scope-manifest", type=Path, default=FROZEN_INPUTS / "scope-manifest.json"
    )
    parser.add_argument("--ray-distance-m", type=float, default=10_000.0)
    parser.add_argument("--claimed-bound-deg", type=float, default=0.5)
    args = parser.parse_args()
    result = audit_frozen_inputs(
        args.catalog,
        args.measurements,
        args.scope_manifest,
        ray_distance_m=args.ray_distance_m,
        claimed_bound_deg=args.claimed_bound_deg,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
