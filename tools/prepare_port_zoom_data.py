"""Prepare a reproducible 30 km context window for each major-port observer site."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "results/eastern-seaboard-maps/data"
SITES = ROOT / "data/sites/east-coast-major-ports-top150-2024.csv"
OUTPUT = DATA / "port-zooms.json"
HALF_SPAN_KM = 15.0
KM_PER_DEG_LAT = 111.32


def clip_edge(points: list[list[float]], inside, intersect) -> list[list[float]]:
    if not points:
        return []
    output: list[list[float]] = []
    previous = points[-1]
    previous_inside = inside(previous)
    for current in points:
        current_inside = inside(current)
        if current_inside != previous_inside:
            output.append(intersect(previous, current))
        if current_inside:
            output.append(current)
        previous, previous_inside = current, current_inside
    return output


def clip_ring(
    ring: list[list[float]], west: float, south: float, east: float, north: float
) -> list[list[float]]:
    points = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring[:]

    def vertical(a, b, x):
        ratio = (x - a[0]) / (b[0] - a[0]) if b[0] != a[0] else 0.0
        return [x, a[1] + ratio * (b[1] - a[1])]

    def horizontal(a, b, y):
        ratio = (y - a[1]) / (b[1] - a[1]) if b[1] != a[1] else 0.0
        return [a[0] + ratio * (b[0] - a[0]), y]

    points = clip_edge(points, lambda p: p[0] >= west, lambda a, b: vertical(a, b, west))
    points = clip_edge(points, lambda p: p[0] <= east, lambda a, b: vertical(a, b, east))
    points = clip_edge(points, lambda p: p[1] >= south, lambda a, b: horizontal(a, b, south))
    points = clip_edge(points, lambda p: p[1] <= north, lambda a, b: horizontal(a, b, north))
    if len(points) < 3:
        return []
    points.append(points[0])
    return points


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def main() -> None:
    sites = read_csv(SITES)
    ais = read_csv(DATA / "ais.csv")
    aids = read_csv(DATA / "chart-aids.csv")
    all_land = json.loads((DATA / "land-east_coast.json").read_text(encoding="utf-8"))
    result: list[dict[str, Any]] = []

    for site in sites:
        lat = float(site["latitude_deg"])
        lon = float(site["longitude_deg"])
        lat_span = HALF_SPAN_KM / KM_PER_DEG_LAT
        lon_span = HALF_SPAN_KM / (KM_PER_DEG_LAT * math.cos(math.radians(lat)))
        west, east = lon - lon_span, lon + lon_span
        south, north = lat - lat_span, lat + lat_span

        def select(rows: list[dict[str, str]]) -> list[list[str | float]]:
            selected = []
            for row in rows:
                x, y = float(row["longitude"]), float(row["latitude"])
                if west <= x <= east and south <= y <= north:
                    selected.append([row.get("group", row.get("type_code", "Unknown")), x, y])
            return selected

        clipped_land = []
        for ring in all_land["polygons"]:
            xs = [point[0] for point in ring]
            ys = [point[1] for point in ring]
            if max(xs) < west or min(xs) > east or max(ys) < south or min(ys) > north:
                continue
            clipped = clip_ring(ring, west, south, east, north)
            if clipped:
                clipped_land.append(clipped)
        result.append(
            {
                "id": site["site_id"],
                "port": site["port"],
                "rank": int(site["usace_2024_rank"]),
                "latitude": lat,
                "longitude": lon,
                "bounds": [west, south, east, north],
                "ais": select(ais),
                "aids": select(aids),
                "land": clipped_land,
            }
        )

    OUTPUT.write_text(
        json.dumps({"half_span_km": HALF_SPAN_KM, "sites": result}, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT.relative_to(ROOT)} with {len(result)} sites")
    print(
        "Nearby features: {} AIS, {} chart aids".format(
            sum(len(s["ais"]) for s in result), sum(len(s["aids"]) for s in result)
        )
    )
    print("Clipped coastline rings: {}".format(sum(len(s["land"]) for s in result)))


if __name__ == "__main__":
    main()
