"""Read saved remote inputs and export display data. No inference or drawing.

Only local data files are written. The remote program is supplied over SSH
standard input; it never changes the authoritative checkout or saved runs.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

REMOTE = "comech-2080"
ROOT = "/home/jvaught2/projects/bearing-only-maritime-localization"
OUTPUT = Path(__file__).resolve().parent / "data"

REMOTE_PROGRAM = r"""import csv, gc, gzip, hashlib, json, math, subprocess, sys
from collections import Counter
from pathlib import Path
import numpy as np
from pyproj import Geod, Transformer
from shapely.geometry import box, shape

ROOT = Path.cwd()
GEOD = Geod(ellps="WGS84")
FWD = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True)
CRS = "EPSG:32618"
hashes = {}


def sha(path):
    path = Path(path)
    key = str(path)
    if key not in hashes:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
                h.update(chunk)
        hashes[key] = h.hexdigest()
    return hashes[key]


def load(path):
    return json.loads(Path(path).read_text())


def emit(name, payload):
    print(
        json.dumps({"name": name, "payload": payload}, separators=(",", ":"), allow_nan=False),
        flush=True,
    )


def xy(lon, lat):
    return list(map(float, FWD.transform(lon, lat)))


def finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite(v) for v in value]
    return value


result_path = Path("docs/verification/charleston-coastwide-distinct-result.json")
result = load(result_path)
settings = result["settings"]
map_path = Path(settings["map"])
frame_path = Path("docs/verification/charleston-blind-measurements.json")
prep_path = Path("runs/charleston-canonical-blind-20261029T004504Z/preparation-check.json")
prep = load(prep_path)
diag = prep["simulation_diagnostics"]
frame = load(frame_path)
observer_ll = [result["accuracy"]["true_longitude_deg"], result["accuracy"]["true_latitude_deg"]]
observer = xy(*observer_ll)
estimate = result["estimate"]["estimate"]
estimate_xy = [estimate["east_m"], estimate["north_m"]]
source_meta = load("data/sources.json")
object_path = next(Path(p) for p in result["preservation"]["before"] if "aid-view-" in p)
with np.load(object_path, allow_pickle=False) as archive:
    objects = archive["objects"]
    object_header = json.loads(archive["header"].tobytes().decode())
    keys = object_header["object_keys"]
records = {}
for key, row in zip(keys, objects):
    source = "ais" if key.startswith("source:") else "chart"
    records[key] = {
        "key": key,
        "xy": row[:2].tolist(),
        "lonlat": row[2:4].tolist(),
        "kind": int(row[6]),
        "source": source,
    }
all_records = list(records.values())
with Path("data/observer_locations.csv").open() as f:
    sites = list(csv.DictReader(f))
sites = [
    dict(
        id=s["site_id"],
        name=s["port"],
        lonlat=[float(s["longitude_deg"]), float(s["latitude_deg"])],
        xy=xy(float(s["longitude_deg"]), float(s["latitude_deg"])),
        water_status=s["current_map_water_status"],
        map_archive_sha256=s["current_map_sha256"],
    )
    for s in sites
]
sources = {
    "result": {"path": str(result_path), "sha256": sha(result_path)},
    "measurements": {"path": str(frame_path), "sha256": sha(frame_path)},
    "canonical_object_sidecar": {"path": str(object_path), "sha256": sha(object_path)},
    "preparation_check": {"path": str(prep_path), "sha256": sha(prep_path)},
    "observers": {
        "path": "data/observer_locations.csv",
        "sha256": sha("data/observer_locations.csv"),
    },
}
emit("observers.json", dict(crs=CRS, observers=sites, metadata=sources["observers"]))
emit(
    "catalog-coast.json",
    dict(
        crs=CRS,
        objects=all_records,
        counts=dict(
            total=len(records),
            ais=sum(r["source"] == "ais" for r in all_records),
            chart=sum(r["source"] == "chart" for r in all_records),
        ),
        metadata=sources["canonical_object_sidecar"],
    ),
)
emit(
    "run-result.json",
    finite(
        dict(
            crs=CRS,
            observer=observer,
            observer_lonlat=observer_ll,
            estimate=estimate,
            search_summary=result["search_summary"],
            settings=settings,
            accuracy=result["accuracy"],
            counts={"evaluated": 64875, "rejected": 58118, "expanded": 6360, "terminal": 397},
            candidate_spread=result["candidate_spread"],
            pipeline_status=result["pipeline_status"],
            measurement_frame=dict(
                total=len(frame["measurements"]),
                types=dict(Counter(m["object_type"] for m in frame["measurements"])),
            ),
            metadata=sources,
        )
    ),
)

# Read the existing canonical archive only for retained source-component links.
canonical_path = Path("data/objects-chart-aids-canonical.json.gz")
with gzip.open(canonical_path, "rt") as f:
    canonical = json.load(f)
raw_wanted = {r["object_key"] for r in prep["rays"]}
raw_to_canonical = {}
triple = None
for rec in canonical["records"]:
    md = dict(rec["metadata"])
    for comp in md.get("source_components", []):
        if comp["key"] in raw_wanted:
            raw_to_canonical[comp["key"]] = rec["key"]
    if (
        abs(rec["latitude_deg"] - 32.7731847) < 1e-12
        and abs(rec["longitude_deg"] + 79.9054558) < 1e-12
    ):
        triple = dict(
            record=rec, xy=records[rec["key"]]["xy"], component_count=md["source_component_count"]
        )
for key in raw_wanted:
    if key in records:
        raw_to_canonical[key] = key
emit(
    "canonical-triple.json",
    dict(
        crs=CRS,
        observer=observer,
        example=triple,
        metadata={
            "path": str(canonical_path),
            "sha256": sha(canonical_path),
            "raw_source_rows": 64400,
            "canonical_locations": 32168,
            "physical_aids": 31925,
            "unknown": 243,
            "coordinate_rule": "exact parsed coordinate equality; no proximity rounding",
        },
    ),
)
del canonical
gc.collect()

ray_by_key = {}
for ray in prep["rays"]:
    key = raw_to_canonical.get(ray["object_key"])
    if key is not None and key not in ray_by_key:
        ray_by_key[key] = ray
in_range = []
for record in all_records:
    a, _, d = GEOD.inv(*observer_ll, *record["lonlat"])
    if d <= 10000:
        record = dict(record, distance_m=d, true_bearing_deg=a % 360)
        ray = ray_by_key.get(record["key"])
        record["saved_visibility"] = ray["cpu_indexed"] if ray else None
        in_range.append(record)
groups = []
truth_by_observation = {}
for index, g in enumerate(diag["groups"]):
    members = [records[key] for key in g["member_object_ids"]]
    rep = records[g["representative_object_id"]]
    a, _, distance = GEOD.inv(*observer_ll, *rep["lonlat"])
    group = dict(
        index=index,
        members=members,
        representative=rep,
        true_bearing_deg=a % 360,
        distance_m=distance,
        detected=g["detected"],
        observation_index=g["observation_index"],
    )
    groups.append(group)
    if group["observation_index"] is not None:
        truth_by_observation[group["observation_index"]] = group
marker_indices = [
    i for i, m in enumerate(frame["measurements"]) if m["object_type"] == "physical_aid"
]
selected_keys = estimate["matched_object_keys"]
assignment_by_observation = dict(zip(marker_indices, selected_keys))
reports = []
for index, m in enumerate(frame["measurements"]):
    item = dict(index=index, **m)
    endpoint = GEOD.fwd(*observer_ll, m["bearing_deg"], 10000)
    item["bearing_ray"] = [observer, xy(endpoint[0], endpoint[1])]
    truth = truth_by_observation.get(index)
    item["extra_vessel_report"] = truth is None
    if truth:
        item.update(
            true_object=truth["representative"],
            true_bearing_deg=truth["true_bearing_deg"],
            actual_error_deg=(m["bearing_deg"] - truth["true_bearing_deg"] + 180) % 360 - 180,
            true_group_index=truth["index"],
        )
    if index in assignment_by_observation:
        item["selected_assignment"] = records[assignment_by_observation[index]]
    reports.append(item)
merged = [g for g in groups if len(g["members"]) > 1]
clear = next(
    r for r in prep["rays"] if r["cpu_indexed"] == "clear" and r["target_water_status"] == "water"
)
blocked = next(
    r for r in prep["rays"] if r["cpu_indexed"] == "blocked" and r["target_water_status"] == "water"
)
for example in (clear, blocked):
    example["observer_en_m"] = observer
simulation = dict(
    crs=CRS,
    observer=observer,
    observer_lonlat=observer_ll,
    sensor=prep["sensor"],
    saved_counts={k: v for k, v in diag.items() if k != "groups"},
    reports=reports,
    groups=groups,
    objects_in_range=in_range,
    merged_groups=merged,
    land_examples=dict(clear=clear, blocked=blocked),
    exact_measurement_frame=frame,
    metadata=dict(
        sources,
        observation_semantics="Simulated observations on real NOAA geometry; saved frame only",
        extra_report_semantics="Bearing only; no physical geographic target is invented",
    ),
)
emit("simulation.json", simulation)
emit(
    "measurements.json",
    dict(
        crs=CRS,
        observer=observer,
        reports=reports,
        marker_observation_indices=marker_indices,
        metadata=sources["measurements"],
    ),
)
terminal_path = Path("docs/verification/charleston-coastwide-distinct-terminal-results.npz")
with np.load(terminal_path, allow_pickle=False) as archive:
    centers = (archive["cells"][:, :2] + archive["cells"][:, 2:]) * 0.5
    terminal = [
        dict(
            cell=c.tolist(),
            xy=p.tolist(),
            status=int(s),
            flags=int(f),
            residual_deg=float(r) if np.isfinite(r) else None,
            marker_rejection_reason=int(reason),
        )
        for c, p, s, f, r, reason in zip(
            archive["cells"],
            centers,
            archive["status"],
            archive["flags"],
            archive["residual_deg"],
            archive["marker_rejection_reason"],
        )
    ]
emit(
    "terminal-cells.json",
    dict(
        crs=CRS,
        observer=observer,
        estimate=estimate_xy,
        cells=terminal,
        bounds=[estimate_xy[0] - 16, estimate_xy[1] - 13, estimate_xy[0] + 16, estimate_xy[1] + 13],
        status_counts=dict(Counter(str(t["status"]) for t in terminal)),
        metadata=dict(
            path=str(terminal_path),
            sha256=sha(terminal_path),
            semantics="Scores describe terminal centers, not complete rectangles",
        ),
    ),
)

# Exact saved projected map. Geometry operations below are display-only.
print("Reading exact run projected map for display exports", file=sys.stderr, flush=True)
map_payload = load(map_path)
map_crs = map_payload["crs"]
assert str(map_crs).upper() == CRS, (map_crs, CRS)
land = shape(json.loads(map_payload["land_geometry_geojson"]))
coverage = shape(json.loads(map_payload["coverage_geometry_geojson"]))
unknown = shape(json.loads(map_payload["unknown_geometry_geojson"]))
del map_payload
gc.collect()
map_source = dict(
    path=str(map_path),
    sha256=sha(map_path),
    crs=CRS,
    role="Exact run geometry source; clipping/simplification applies only to these display exports",
)


def polygon_parts(g):
    if g.is_empty:
        return
    if g.geom_type == "Polygon":
        yield g
    elif hasattr(g, "geoms"):
        for child in g.geoms:
            yield from polygon_parts(child)


def rings(g, bounds, tolerance):
    clipped = g.intersection(box(*bounds))
    if tolerance:
        clipped = clipped.simplify(tolerance, preserve_topology=True)
    return [
        [list(map(list, p.exterior.coords)), *[list(map(list, r.coords)) for r in p.interiors]]
        for p in polygon_parts(clipped)
    ]


def contains(p, b):
    return b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3]


def map_export(name, bounds, at, tolerance, scale):
    local = [r for r in all_records if contains(r["xy"], bounds)]
    north = GEOD.fwd(*at["lonlat"], 0, 1000)
    local_sites = [s for s in sites if contains(s["xy"], bounds)]
    data = dict(
        name=name,
        crs=CRS,
        bounds=bounds,
        land=rings(land, bounds, tolerance),
        water=rings(coverage.difference(unknown), bounds, max(tolerance, 1)),
        unknown=rings(unknown, bounds, tolerance),
        aids=[r["xy"] for r in local if r["source"] == "chart" and r["kind"] == 1],
        ais=[r["xy"] for r in local if r["source"] == "ais"],
        unknown_objects=[r["xy"] for r in local if r["source"] == "chart" and r["kind"] == 0],
        observer=at["xy"],
        observers=local_sites,
        scale_m=scale,
        metadata=dict(
            source_map=map_source,
            source_catalog=sources["canonical_object_sidecar"],
            display_simplification_m=tolerance,
            water_semantics="Known coverage base fill; draw land over it, retaining polygon holes",
            north_ray=[at["xy"], xy(north[0], north[1])],
            counts=dict(
                physical_aids=sum(r["source"] == "chart" and r["kind"] == 1 for r in local),
                unknown_chart_locations=sum(
                    r["source"] == "chart" and r["kind"] == 0 for r in local
                ),
                ais=sum(r["source"] == "ais" for r in local),
            ),
        ),
    )
    if name == "charleston":
        data["range_circles"] = []
        for radius in (1000, 3000, 5000, 10000):
            path = []
            for angle in range(0, 361, 2):
                lon, lat, _ = GEOD.fwd(*observer_ll, angle, radius)
                path.append(xy(lon, lat))
            data["range_circles"].append(
                dict(
                    radius_m=radius,
                    xy=path,
                    semantics="Geodesic radius only, not land-blocked visibility",
                )
            )
    emit("map-" + name + ".json", data)


b17 = next(s for s in sites if s["id"] == "B17")
x, y = observer
map_export("charleston", [x - 12000, y - 10000, x + 12000, y + 10000], b17, 3, 5000)
sx0, sx1, sy0, sy1 = settings["search"]["bounds_en_m"]
map_export("coast", [sx0, sy0, sx1, sy1], b17, 400, 200000)
for site_id, name in (("B14", "norfolk"), ("B2", "portland")):
    site = next(s for s in sites if s["id"] == site_id)
    x, y = site["xy"]
    map_export(name, [x - 12000, y - 10000, x + 12000, y + 10000], site, 4, 5000)
x, y = triple["xy"]
map_export("canonical-triple", [x - 600, y - 600, x + 600, y + 600], b17, 0.4, 200)
merge_points = [observer] + [r["xy"] for g in merged for r in g["members"]]
mx = [p[0] for p in merge_points]
my = [p[1] for p in merge_points]
mb = [min(mx) - 250, min(my) - 250, max(mx) + 250, max(my) + 250]
map_export("merged-pair", mb, b17, 1, 1000)
blocked_xy = blocked["target_en_m"]
lb = [
    min(observer[0], blocked_xy[0]) - 400,
    min(observer[1], blocked_xy[1]) - 400,
    max(observer[0], blocked_xy[0]) + 400,
    max(observer[1], blocked_xy[1]) + 400,
]
map_export("land-los", lb, b17, 1, 1000)
emit(
    "sources.json",
    dict(
        sources=sources,
        source_metadata=source_meta,
        map=map_source,
        extraction_source_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        unavailable=[
            "Current full intermediate pruning geometry/graphs were not saved; no replay executed",
            "No current AIS-bearing objective comparison is available",
            "No new multiport/range simulator frames were generated",
        ],
        rendering_contract=dict(
            crs=CRS,
            bounds_order="xmin,ymin,xmax,ymax",
            polygons="Each polygon is [exterior ring, hole ring, ...]; all rings close explicitly",
            water="Known coverage base fill; draw land above it and preserve holes",
            point_positions="Exact saved canonical object-sidecar coordinates",
            shoreline="Display clipping and topology-preserving simplification only",
        ),
    ),
)
"""


CROP_PROGRAM = r"""import copy, json, sys
from shapely.geometry import GeometryCollection, LineString, Polygon, box

packet = json.load(sys.stdin)
base = packet["base"]


def parts(g):
    if g.is_empty:
        return
    if g.geom_type == "Polygon":
        yield g
    elif hasattr(g, "geoms"):
        for child in g.geoms:
            yield from parts(child)


def within(point, b):
    return b[0] <= point[0] <= b[2] and b[1] <= point[1] <= b[3]


out = {}
for view in packet["views"]:
    data = copy.deepcopy(base)
    bounds = view["bounds"]
    crop = box(*bounds)
    data.update(name=view["name"], bounds=bounds, scale_m=view["scale_m"])
    data.pop("range_circles", None)
    minimum_area = view.get("minimum_polygon_area_m2", 0)
    tolerance = view.get("secondary_simplification_m", 0)
    rounding = view.get("coordinate_rounding_m", 0)

    def coordinates(sequence):
        return [
            [round(v / rounding) * rounding for v in p] if rounding else list(p) for p in sequence
        ]

    dropped = {}
    for layer in ("land", "water", "unknown"):
        polygons = [Polygon(poly[0], poly[1:]) for poly in base[layer]]
        dropped[layer] = sum(p.area < minimum_area for p in polygons)
        geometry = GeometryCollection([p for p in polygons if p.area >= minimum_area])
        clipped = geometry if bounds == base["bounds"] else geometry.intersection(crop)
        if tolerance:
            clipped = clipped.simplify(tolerance, preserve_topology=True)
        data[layer] = [
            [coordinates(p.exterior.coords), *[coordinates(r.coords) for r in p.interiors]]
            for p in parts(clipped)
        ]
    for points in ("aids", "ais", "unknown_objects"):
        data[points] = coordinates([p for p in base[points] if within(p, bounds)])
    data["observers"] = [p for p in base["observers"] if within(p["xy"], bounds)]
    data["metadata"]["display_parent_export"] = "map-charleston.json"
    data["metadata"]["secondary_display_crop_only"] = True
    if minimum_area or tolerance or rounding:
        data["metadata"].update(
            coast_display_only=True,
            minimum_polygon_area_m2=minimum_area,
            secondary_simplification_m=tolerance,
            coordinate_rounding_m=rounding,
            dropped_small_polygon_counts=dropped,
            source_parent_simplification_m=base["metadata"]["display_simplification_m"],
            display_aggregation_note=(
                "Small polygon components omitted below approximate coast-pixel area; "
                "topology-preserving secondary simplification and display-coordinate "
                "rounding only. Exact objects remain in catalog-coast.json."
            ),
        )
    data["metadata"]["counts"] = {
        "physical_aids": len(data["aids"]),
        "ais": len(data["ais"]),
        "unknown_chart_locations": len(data["unknown_objects"]),
    }
    if "matched_rays" in view:
        rays = []
        for ray in view["matched_rays"]:
            ray = dict(ray)
            clipped = LineString([ray["from"], ray["to"]]).intersection(crop)
            ray["visible_ray"] = (
                list(map(list, clipped.coords)) if clipped.geom_type == "LineString" else []
            )
            rays.append(ray)
        data["matched_rays"] = rays
        data["estimate"] = view["estimate"]
        data["metadata"]["matched_ray_semantics"] = (
            "Selected estimate to actual assigned catalog object; clipped for display only"
        )
    out[view["filename"]] = data
print(json.dumps(out, separators=(",", ":"), allow_nan=False))
"""


def additional_views() -> None:
    """Crop existing display geometry without another source-data extraction."""
    base = json.loads((OUTPUT / "map-charleston.json").read_text())
    triple = json.loads((OUTPUT / "map-canonical-triple.json").read_text())
    (OUTPUT / "map-canonical.json").write_text(
        json.dumps(triple, separators=(",", ":"), allow_nan=False) + "\n"
    )
    simulation = json.loads((OUTPUT / "simulation.json").read_text())
    result = json.loads((OUTPUT / "run-result.json").read_text())
    observer = simulation["observer"]
    points = [observer] + [
        r["xy"] for group in simulation["merged_groups"] for r in group["members"]
    ]
    center = [
        (min(p[i] for p in points) + max(p[i] for p in points)) / 2 for i in (0, 1)
    ]
    selected = [result["estimate"]["east_m"], result["estimate"]["north_m"]]
    rays = [
        {
            "observation_index": report["index"],
            "key": report["selected_assignment"]["key"],
            "from": selected,
            "to": report["selected_assignment"]["xy"],
            "observed_bearing_deg": report["bearing_deg"],
        }
        for report in simulation["reports"]
        if "selected_assignment" in report
    ]
    packet = {
        "base": base,
        "views": [
            {
                "filename": "map-merge.json",
                "name": "merge",
                "bounds": [
                    center[0] - 750,
                    center[1] - 2600,
                    center[0] + 750,
                    center[1] + 2600,
                ],
                "scale_m": 1000,
            },
            {
                "filename": "map-result-harbor.json",
                "name": "result-harbor",
                "bounds": [
                    observer[0] - 2000,
                    observer[1] - 1600,
                    observer[0] + 2000,
                    observer[1] + 1600,
                ],
                "scale_m": 1000,
                "estimate": selected,
                "matched_rays": rays,
            },
        ],
    }
    process = subprocess.run(
        [
            "ssh",
            REMOTE,
            f"cd {ROOT} && PYTHONDONTWRITEBYTECODE=1 "
            f".venv/bin/python -c {shlex.quote(CROP_PROGRAM)}",
        ],
        input=json.dumps(packet, separators=(",", ":")),
        capture_output=True,
        check=True,
        text=True,
    )
    for name, payload in json.loads(process.stdout).items():
        path = OUTPUT / name
        path.write_text(
            json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n"
        )
        print(f"Saved {path.name} ({path.stat().st_size:,} bytes)", flush=True)


def coast_display() -> None:
    """Reduce only the wide coast view; exact exports remain available."""
    base = json.loads((OUTPUT / "map-coast.json").read_text())
    packet = {
        "base": base,
        "views": [
            {
                "filename": "map-coast-display.json",
                "name": "coast-display",
                "bounds": base["bounds"],
                "scale_m": base["scale_m"],
                "minimum_polygon_area_m2": 2_000_000,
                "secondary_simplification_m": 750,
                "coordinate_rounding_m": 1,
            }
        ],
    }
    process = subprocess.run(
        [
            "ssh",
            REMOTE,
            f"cd {ROOT} && PYTHONDONTWRITEBYTECODE=1 "
            f".venv/bin/python -c {shlex.quote(CROP_PROGRAM)}",
        ],
        input=json.dumps(packet, separators=(",", ":")),
        capture_output=True,
        check=True,
        text=True,
    )
    payload = json.loads(process.stdout)["map-coast-display.json"]
    payload["metadata"]["display_parent_export"] = "map-coast.json"
    path = OUTPUT / "map-coast-display.json"
    path.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n")
    print(f"Saved {path.name} ({path.stat().st_size:,} bytes)", flush=True)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            "ssh",
            REMOTE,
            f"cd {ROOT} && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -u -",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
    )
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(REMOTE_PROGRAM)
    process.stdin.close()
    for line in process.stdout:
        message = json.loads(line)
        name = message["name"]
        if Path(name).name != name:
            raise ValueError(f"Invalid export filename {name!r}.")
        path = OUTPUT / name
        path.write_text(
            json.dumps(message["payload"], separators=(",", ":"), allow_nan=False)
            + "\n"
        )
        print(f"Saved {path.name} ({path.stat().st_size:,} bytes)", flush=True)
    if process.wait() != 0:
        raise SystemExit(process.returncode)
    additional_views()
    coast_display()


if __name__ == "__main__":
    main()
