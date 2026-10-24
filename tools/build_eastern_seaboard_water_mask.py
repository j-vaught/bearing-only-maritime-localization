"""Rasterize the versioned Natural Earth coast polygons into a water mask.

The output uses the same local equirectangular metre frame as the East Coast
search diagnostics. A 500 m scanline raster supports coarse candidate pruning
and land-blocked line-of-sight experiments; it is not a navigation chart.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "results/eastern-seaboard-maps/data/land-east_coast.json"
OUTPUT = ROOT / "data/maps/eastern-seaboard-water-mask-500m.npz"
MANIFEST = ROOT / "data/maps/eastern-seaboard-water-mask-500m.manifest.json"
R_EARTH_M = 6_371_008.8
ORIGIN_LAT_DEG = 38.0
ORIGIN_LON_DEG = -76.2


def project(lon_deg: float, lat_deg: float) -> tuple[float, float]:
    east = (
        R_EARTH_M
        * math.radians(lon_deg - ORIGIN_LON_DEG)
        * math.cos(math.radians(ORIGIN_LAT_DEG))
    )
    north = R_EARTH_M * math.radians(lat_deg - ORIGIN_LAT_DEG)
    return east, north


def rasterize(cell_size_m: float = 500.0) -> tuple[np.ndarray, float, float]:
    land = json.loads(SOURCE.read_text(encoding="utf-8"))
    west_lon, south_lat, east_lon, north_lat = land["bounds"]
    west_m, south_m = project(west_lon, south_lat)
    east_m, north_m = project(east_lon, north_lat)
    width = math.ceil((east_m - west_m) / cell_size_m)
    height = math.ceil((north_m - south_m) / cell_size_m)
    scanlines: list[list[float]] = [[] for _ in range(height)]

    # Store edge intersections by raster row. The half-open vertical test avoids
    # counting polygon vertices twice. Cell centers receive the even-odd fill.
    for polygon in land["polygons"]:
        ring = [project(float(lon), float(lat)) for lon, lat in polygon]
        for index, (x1, y1) in enumerate(ring):
            x2, y2 = ring[(index + 1) % len(ring)]
            if y1 == y2:
                continue
            low_y, high_y = sorted((y1, y2))
            first_row = max(0, math.ceil((low_y - south_m) / cell_size_m - 0.5))
            last_row = min(height, math.ceil((high_y - south_m) / cell_size_m - 0.5))
            for row in range(first_row, last_row):
                y_center = south_m + (row + 0.5) * cell_size_m
                fraction = (y_center - y1) / (y2 - y1)
                scanlines[row].append(x1 + fraction * (x2 - x1))

    water = np.ones((height, width), dtype=np.bool_)
    for row, intersections in enumerate(scanlines):
        intersections.sort()
        if len(intersections) % 2:
            raise ValueError(f"Odd number of land-boundary crossings on raster row {row}.")
        for left, right in zip(intersections[::2], intersections[1::2], strict=True):
            first_col = max(0, math.ceil((left - west_m) / cell_size_m - 0.5))
            last_col = min(width, math.ceil((right - west_m) / cell_size_m - 0.5))
            if last_col > first_col:
                water[row, first_col:last_col] = False
    return water, west_m, south_m


def main() -> None:
    cell_size_m = 500.0
    water, west_m, south_m = rasterize(cell_size_m)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        OUTPUT,
        water=water,
        west_m=west_m,
        south_m=south_m,
        cell_size_m=cell_size_m,
        crs="local-equirectangular-origin-38N-76.2W",
        source="Natural Earth 5.1.1 1:10m land polygons",
    )
    manifest = {
        "source": "results/eastern-seaboard-maps/data/land-east_coast.json",
        "source_citation": "Natural Earth 5.1.1 1:10m land polygons, public domain",
        "raster_cell_size_m": cell_size_m,
        "raster_shape_rows_south_to_north": list(water.shape),
        "crs": "local-equirectangular-origin-38N-76.2W",
        "bounds_projected_m": [
            west_m,
            south_m,
            west_m + water.shape[1] * cell_size_m,
            south_m + water.shape[0] * cell_size_m,
        ],
        "water_cell_count": int(water.sum()),
        "land_cell_count": int(water.size - water.sum()),
        "limitations": [
            "Coastline detail is limited by Natural Earth 1:10m source geometry.",
            "500 m cells are for experimental search pruning, not navigation.",
            "The local equirectangular projection is a coast-wide diagnostic approximation.",
        ],
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
