"""Prepare typed East Coast map layers and open vector coastlines for Typst figures."""

from __future__ import annotations

import csv
import json
import struct
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/eastern-seaboard-maps/data"
AIS_PATH = ROOT / "data/ais/eastern-seaboard_2023-07-15T1200Z.csv"
AIDS_PATH = ROOT / "data/chart-aids/eastern-seaboard-enc-aids.csv"
SITES_PATH = ROOT / "data/sites/b1-b5.csv"
NATURAL_EARTH_URL = "https://naturalearth.s3.amazonaws.com/10m_physical/ne_10m_land.zip"
NATURAL_EARTH_VERSION = "5.1.1"
BAND_PRIORITY = {
    "overview": 1,
    "general": 2,
    "coastal": 3,
    "approach": 4,
    "harbour": 5,
    "berthing": 6,
}
EXTENTS = {
    "east_coast": (-82.0, 24.5, -66.0, 45.5),
    "chesapeake": (-77.35, 36.2, -75.2, 40.0),
}


def ais_group(type_code: str) -> str:
    if not type_code:
        return "Unknown / other"
    code = int(type_code)
    if code == 30:
        return "Fishing"
    if code in (31, 32):
        return "Towing"
    if 33 <= code <= 35:
        return "Special operations"
    if code == 36:
        return "Sailing"
    if code == 37:
        return "Pleasure craft"
    if 40 <= code <= 49:
        return "High-speed craft"
    if 50 <= code <= 59:
        return "Service / special craft"
    if 60 <= code <= 69:
        return "Passenger"
    if 70 <= code <= 79:
        return "Cargo"
    if 80 <= code <= 89:
        return "Tanker"
    return "Unknown / other"


def aid_group(service_layer: str) -> str:
    name = service_layer.split(".", 1)[-1].removesuffix("_point")
    return name.split("_", 1)[0]


def deduplicated_aids() -> list[dict[str, str]]:
    selected: dict[tuple[str, float, float], dict[str, str]] = {}
    with AIDS_PATH.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            group = aid_group(row["service_layer"])
            lon, lat = float(row["longitude"]), float(row["latitude"])
            key = (group, round(lon, 5), round(lat, 5))
            previous = selected.get(key)
            if (
                previous is None
                or BAND_PRIORITY[row["scale_band"]] > BAND_PRIORITY[previous["scale_band"]]
            ):
                selected[key] = {**row, "aid_group": group}
    return sorted(
        selected.values(),
        key=lambda row: (row["aid_group"], row["latitude"], row["longitude"]),
    )


def write_layers() -> dict[str, int]:
    OUT.mkdir(parents=True, exist_ok=True)
    counts: Counter[str] = Counter()
    with (
        AIS_PATH.open(newline="", encoding="utf-8") as source,
        (OUT / "ais.csv").open("w", newline="", encoding="utf-8") as target,
    ):
        writer = csv.DictWriter(target, fieldnames=("longitude", "latitude", "type_code", "group"))
        writer.writeheader()
        for row in csv.DictReader(source):
            group = ais_group(row["vessel_type_code"])
            writer.writerow(
                {
                    "longitude": row["longitude_deg"],
                    "latitude": row["latitude_deg"],
                    "type_code": row["vessel_type_code"],
                    "group": group,
                }
            )
            counts[f"AIS / {group}"] += 1

    aids = deduplicated_aids()
    with (OUT / "chart-aids.csv").open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=("longitude", "latitude", "group"))
        writer.writeheader()
        for row in aids:
            writer.writerow(
                {
                    "longitude": row["longitude"],
                    "latitude": row["latitude"],
                    "group": row["aid_group"],
                }
            )
            counts[f"Aid / {row['aid_group']}"] += 1

    with (
        SITES_PATH.open(newline="", encoding="utf-8") as source,
        (OUT / "sites.csv").open("w", newline="", encoding="utf-8") as target,
    ):
        writer = csv.DictWriter(target, fieldnames=("site_id", "longitude", "latitude"))
        writer.writeheader()
        for row in csv.DictReader(source):
            writer.writerow(
                {
                    "site_id": row["site_id"],
                    "longitude": row["longitude_deg"],
                    "latitude": row["latitude_deg"],
                }
            )
    counts["AIS total"] = sum(value for key, value in counts.items() if key.startswith("AIS / "))
    counts["chart aids deduplicated total"] = len(aids)
    return dict(counts)


def fetch_natural_earth(work: Path) -> Path:
    archive = work / "ne_10m_land.zip"
    if not archive.exists():
        request = urllib.request.Request(
            NATURAL_EARTH_URL,
            headers={"User-Agent": "BearingOnlyMaritimeLocalization/1.0 (map figure build)"},
        )
        with urllib.request.urlopen(request, timeout=90) as response, archive.open("wb") as out:
            while chunk := response.read(1024 * 1024):
                out.write(chunk)
    return archive


def shapefile_polygons(shp: Path):
    with shp.open("rb") as stream:
        stream.seek(100)
        while header := stream.read(8):
            if len(header) != 8:
                break
            _, content_words = struct.unpack(">2i", header)
            content = stream.read(content_words * 2)
            shape_type = struct.unpack_from("<i", content, 0)[0]
            if shape_type != 5:
                continue
            xmin, ymin, xmax, ymax = struct.unpack_from("<4d", content, 4)
            if xmax < -83 or xmin > -65 or ymax < 23.5 or ymin > 46.5:
                continue
            part_count, point_count = struct.unpack_from("<2i", content, 36)
            parts_offset = 44
            parts = list(struct.unpack_from(f"<{part_count}i", content, parts_offset))
            points_offset = parts_offset + part_count * 4
            points = [
                struct.unpack_from("<2d", content, points_offset + index * 16)
                for index in range(point_count)
            ]
            starts = parts + [point_count]
            yield [points[starts[i] : starts[i + 1]] for i in range(part_count)]


def clip_ring(points, bounds):
    xmin, ymin, xmax, ymax = bounds
    vertices = points[:-1] if points and points[0] == points[-1] else points[:]
    edges = (
        (
            lambda p: p[0] >= xmin,
            lambda a, b: (xmin, a[1] + (b[1] - a[1]) * (xmin - a[0]) / (b[0] - a[0])),
        ),
        (
            lambda p: p[0] <= xmax,
            lambda a, b: (xmax, a[1] + (b[1] - a[1]) * (xmax - a[0]) / (b[0] - a[0])),
        ),
        (
            lambda p: p[1] >= ymin,
            lambda a, b: (a[0] + (b[0] - a[0]) * (ymin - a[1]) / (b[1] - a[1]), ymin),
        ),
        (
            lambda p: p[1] <= ymax,
            lambda a, b: (a[0] + (b[0] - a[0]) * (ymax - a[1]) / (b[1] - a[1]), ymax),
        ),
    )
    for inside, intersect in edges:
        if not vertices:
            return []
        output = []
        previous = vertices[-1]
        for current in vertices:
            previous_inside, current_inside = inside(previous), inside(current)
            if current_inside:
                if not previous_inside:
                    output.append(intersect(previous, current))
                output.append(current)
            elif previous_inside:
                output.append(intersect(previous, current))
            previous = current
        vertices = output
    if len(vertices) < 3:
        return []
    vertices.append(vertices[0])
    return vertices


def simplify_ring(points, tolerance=0.0025):
    """Apply iterative Ramer-Douglas-Peucker simplification to a closed ring."""
    points = points[:-1]
    if len(points) < 5:
        return points
    first = points[0]
    farthest_index = max(range(1, len(points)), key=lambda index: math_dist2(points[index], first))
    rotated = points[farthest_index:] + points[:farthest_index] + [points[farthest_index]]

    def simplify(segment):
        if len(segment) < 3:
            return segment
        start, end = segment[0], segment[-1]
        dx, dy = end[0] - start[0], end[1] - start[1]
        denom = dx * dx + dy * dy
        best_index, best_distance = 0, -1.0
        for index, point in enumerate(segment[1:-1], 1):
            if denom == 0:
                distance = math_dist2(point, start)
            else:
                t = max(
                    0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denom)
                )
                projection = (start[0] + t * dx, start[1] + t * dy)
                distance = math_dist2(point, projection)
            if distance > best_distance:
                best_index, best_distance = index, distance
        if best_distance <= tolerance * tolerance:
            return [start, end]
        return simplify(segment[: best_index + 1])[:-1] + simplify(segment[best_index:])

    result = simplify(rotated)
    return result[:-1]


def math_dist2(first, second):
    return (first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2


def ring_area(points):
    return (
        sum(
            first[0] * second[1] - second[0] * first[1]
            for first, second in zip(points, points[1:] + points[:1])
        )
        / 2
    )


def write_land(work: Path) -> dict[str, int]:
    archive = fetch_natural_earth(work)
    with zipfile.ZipFile(archive) as zipped:
        shp_name = next(name for name in zipped.namelist() if name.endswith(".shp"))
        shp_path = work / Path(shp_name).name
        with zipped.open(shp_name) as source, shp_path.open("wb") as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)

    results = {}
    for name, bounds in EXTENTS.items():
        polygons = []
        for rings in shapefile_polygons(shp_path):
            # Shapefile polygon exteriors are clockwise. Keep each island/exterior
            # and omit inland holes, which are not material at this map scale.
            exterior_rings = [ring for ring in rings if ring_area(ring) < 0]
            if not exterior_rings and rings:
                exterior_rings = [rings[0]]
            for ring in exterior_rings:
                clipped = clip_ring(ring, bounds)
                if not clipped:
                    continue
                simplified = simplify_ring(clipped)
                if len(simplified) >= 3:
                    polygons.append(simplified)
        destination = OUT / f"land-{name}.json"
        destination.write_text(
            json.dumps(
                {
                    "source": "Natural Earth 1:10m Land",
                    "version": NATURAL_EARTH_VERSION,
                    "bounds": bounds,
                    "polygons": polygons,
                },
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        results[name] = sum(len(ring) for ring in polygons)
    shp_path.unlink(missing_ok=True)
    archive.unlink(missing_ok=True)
    return results


def main() -> None:
    work = ROOT / "work/eastern-seaboard-maps"
    work.mkdir(parents=True, exist_ok=True)
    counts = write_layers()
    vertices = write_land(work)
    manifest = {
        "ais_source": "data/ais/eastern-seaboard_2023-07-15T1200Z.csv",
        "ais_window_utc": "2023-07-15T12:00:00Z to 12:01:00Z",
        "chart_aid_source": "data/chart-aids/eastern-seaboard-enc-aids.csv",
        "chart_aid_deduplication": (
            "feature family and coordinates rounded to five decimal degrees; "
            "prefer largest ENC scale band"
        ),
        "site_source": "data/sites/b1-b5.csv",
        "basemap_source": NATURAL_EARTH_URL,
        "basemap_citation": (
            f"Natural Earth {NATURAL_EARTH_VERSION}, 1:10m land vectors, public domain"
        ),
        "basemap_scale_note": (
            "Open vector land/water context; not a street-level OpenStreetMap rendering."
        ),
        "counts": counts,
        "land_polygon_vertices": vertices,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
