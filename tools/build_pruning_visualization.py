"""Build a reproducible Typst map sequence from a recorded pruning traversal."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path

import numpy as np
from pyproj import Transformer
from shapely import clip_by_rect
from shapely.geometry import shape


def polygon_rings(geometry, bounds):
    x0, y0, x1, y1 = bounds
    if geometry.is_empty:
        return []
    if geometry.geom_type not in {"Polygon", "MultiPolygon", "GeometryCollection"}:
        return []
    if geometry.geom_type == "Polygon":
        return [
            [
                [
                    [round((x - x0) / (x1 - x0), 6), round((y - y0) / (y1 - y0), 6)]
                    for x, y in ring.coords
                ]
                for ring in [geometry.exterior, *geometry.interiors]
            ]
        ]
    return [ring for part in geometry.geoms for ring in polygon_rings(part, bounds)]


def rectangles(cells, bounds):
    """Display subpixel cells at their correct positions, with a declared minimum size."""
    if not len(cells):
        return []
    bounds = np.asarray(bounds)
    origin, span = bounds[:2], bounds[2:] - bounds[:2]
    low, high = (cells[:, :2] - origin) / span, (cells[:, 2:] - origin) / span
    visible = np.all(high >= 0, axis=1) & np.all(low <= 1, axis=1)
    low, high = np.clip(low[visible], 0, 1), np.clip(high[visible], 0, 1)
    # Deduplicate coincident display pixels; exact rectangles remain in the NPZ.
    center = (low + high) * 0.5
    width = np.maximum(high - low, 1 / 650)
    display = np.column_stack((center - width / 2, center + width / 2))
    large = np.any(high - low >= 1 / 650, axis=1)
    small = display[~large]
    if len(small):
        bins = np.floor(center[~large] * 650).astype(np.int32)
        _, indices = np.unique(bins, axis=0, return_index=True)
        small = small[indices]
    return np.round(np.concatenate((display[large], small)), 6).tolist()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args()
    job = args.job
    trace = job / "recording/pruning-trace.npz"
    while not trace.exists():
        if (job / "recording/failed.json").exists():
            raise RuntimeError("Pruning recording failed; inspect recording.log")
        if not args.wait:
            raise FileNotFoundError(trace)
        time.sleep(5)
    source = Path((job / "source-run.txt").read_text().strip())
    output = job / "visualization"
    output.mkdir(exist_ok=True)
    settings = json.loads((job / "settings.json").read_text())
    with np.load(trace, allow_pickle=False) as data:
        cells, states, depth = data["cells"], data["state"], data["depth"]
    with np.load("docs/verification/charleston-coastwide-terminal-results.npz") as data:
        final_cells, final_status = data["cells"], data["status"]
    terminal = cells[(states == 0) & (np.max(cells[:, 2:] - cells[:, :2], axis=1) <= 1)]

    def ordered(array):
        return array[np.lexsort(array.T[::-1])]

    parity = {
        "evaluated_matches": len(cells) == 1337411,
        "pruned_matches": int(np.count_nonzero(states)) == 976768,
        "terminal_rectangles_exactly_match": np.array_equal(
            ordered(terminal), ordered(final_cells)
        ),
        "source_run": str(source),
        "trace_sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
        "terminal_scoring_replayed": False,
    }
    (output / "parity.json").write_text(json.dumps(parity, indent=2) + "\n")
    if not all(
        parity[k]
        for k in ["evaluated_matches", "pruned_matches", "terminal_rectangles_exactly_match"]
    ):
        raise RuntimeError("Recorded pruning differs from saved run; no figure published")
    print("Pruning parity verified", flush=True)
    raw_map = json.loads(Path(settings["map"]).read_text())
    land = shape(json.loads(raw_map["land_geometry_geojson"]))
    del raw_map
    overview_land = land.simplify(250, preserve_topology=False)
    x0, x1, y0, y1 = settings["search"]["bounds_en_m"]
    domain = [x0, y0, x1, y1]
    forward = Transformer.from_crs(4326, 32618, always_xy=True)
    city_coords = [
        ("Portland", -70.25, 43.66),
        ("Boston", -71.06, 42.36),
        ("New York", -74.01, 40.71),
        ("Norfolk", -76.29, 36.85),
        ("Charleston", -79.93, 32.78),
        ("Savannah", -81.09, 32.08),
        ("Jacksonville", -81.66, 30.33),
        ("Miami", -80.19, 25.76),
    ]
    frames = []

    def frame(title, subtitle, view_cells, rejected, bounds, **extra):
        # Equal-distance map aspect; padding derives only from the displayed set.
        bounds = np.asarray(bounds, dtype=float)
        center = (bounds[:2] + bounds[2:]) / 2
        span = bounds[2:] - bounds[:2]
        span = np.maximum(span, 30.0)
        target = 1.25
        span[0] = max(span[0], span[1] * target)
        span[1] = max(span[1], span[0] / target)
        bounds = np.r_[center - span * 0.55, center + span * 0.55]
        tolerance = max(span) / 1800
        display_land = land if tolerance < 50 else overview_land
        clipped = clip_by_rect(display_land, *bounds).simplify(tolerance, preserve_topology=False)
        labels = []
        for name, longitude, latitude in city_coords:
            east, north = forward.transform(longitude, latitude)
            p = (np.asarray([east, north]) - bounds[:2]) / (bounds[2:] - bounds[:2])
            if np.all(p > 0.04) and np.all(p < 0.92):
                labels.append([name, *np.round(p, 5).tolist()])
        print(f"Preparing frame {len(frames) + 1}: {title}", flush=True)
        frames.append(
            dict(
                title=title,
                subtitle=subtitle,
                land=polygon_rings(clipped, bounds),
                kept=rectangles(view_cells, bounds),
                removed=rectangles(rejected, bounds),
                bounds=bounds.tolist(),
                labels=labels,
                width_km=round((bounds[2] - bounds[0]) / 1000, 3),
                kept_count=len(view_cells),
                **extra,
            )
        )

    root_cells = cells[depth == 0]
    empty = np.empty((0, 4))
    total_area = (x1 - x0) * (y1 - y0)
    frame(
        "Full Eastern Seaboard",
        "39,435 initial cells | 10 km nominal width",
        root_cells,
        empty,
        domain,
    )
    # The initial root grid tiles a single rectangle without gaps.
    frames[0]["kept"] = rectangles(np.asarray([domain]), frames[0]["bounds"])
    statistics = []
    for level in sorted(np.unique(depth)):
        selected = depth == level
        retained = cells[selected & (states == 0)]
        rejected = cells[selected & (states != 0)]
        area = float(np.prod(retained[:, 2:] - retained[:, :2], axis=1).sum())
        width = 10000 / 2 ** int(level)
        scale = f"{width / 1000:g} km" if width >= 1000 else f"{width:.3g} m"
        row = dict(
            depth=int(level),
            nominal_width_m=width,
            evaluated=int(selected.sum()),
            retained=len(retained),
            rejected=len(rejected),
            retained_area_m2=area,
            state_counts={str(s): int(np.count_nonzero(states[selected] == s)) for s in range(4)},
        )
        statistics.append(row)
        bounds = np.r_[retained[:, :2].min(0), retained[:, 2:].max(0)] if len(retained) else domain
        area_label = f"{area / 1e6:,.3f} km²" if area >= 1e6 else f"{area:,.1f} m²"
        frame(
            f"Pruning at {scale}",
            (
                f"{len(retained):,} retained | {len(rejected):,} rejected at this scale | "
                f"remaining area {area_label}"
            ),
            retained,
            rejected,
            bounds,
            retained_area_fraction=area / total_area,
        )
    consistent, unresolved = final_cells[final_status == 0], final_cells[final_status == 2]
    rejected_centers = final_cells[np.isin(final_status, [1, 3])]
    frame(
        "Saved terminal center scores",
        "148 consistent centers | 15,441 unresolved centers remain coast-wide",
        np.concatenate((consistent, unresolved)),
        rejected_centers,
        domain,
        score_note="Center scores do not reject entire terminal rectangles.",
    )
    local_bounds = np.r_[consistent[:, :2].min(0), consistent[:, 2:].max(0)]
    frame(
        "Charleston consistent-center cluster",
        "148 consistent centers | local detail; unresolved coast-wide candidates remain",
        consistent,
        rejected_centers,
        local_bounds,
        score_note="Zoom chosen from consistent-center extent, without observer truth.",
    )
    for f in frames[-2:]:
        f["consistent"] = rectangles(consistent, f["bounds"])
        f["kept"] = rectangles(unresolved, f["bounds"])
    data = dict(
        schema="PruningMapSequenceV1",
        source_run=str(source),
        parity=parity,
        statistics=statistics,
        frames=frames,
        display_note=(
            "Subpixel rectangles enlarged and deduplicated for visibility; "
            "exact cells in pruning trace."
        ),
    )
    (output / "stages.json").write_text(json.dumps(data, separators=(",", ":")) + "\n")
    template = Path(__file__).with_name("pruning-sequence.typ")
    (output / "pruning-sequence.typ").write_text(template.read_text())
    subprocess.run(
        [
            "typst",
            "compile",
            str(output / "pruning-sequence.typ"),
            str(output / "pruning-sequence.pdf"),
        ],
        check=True,
    )
    subprocess.run(
        [
            "typst",
            "compile",
            "--format",
            "png",
            "--ppi",
            "110",
            str(output / "pruning-sequence.typ"),
            str(output / "stage-{0p}.png"),
        ],
        check=True,
    )
    print(json.dumps({"complete": True, "frames": len(frames), "output": str(output)}), flush=True)
    (job / "visualization-complete.json").write_text(
        json.dumps({"complete": True, "frames": len(frames), "parity": parity}, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
