"""Render the approved recorded pruning trace with Typst and assemble its movie.

This program reads saved arrays only. It contains no remote, simulator, search,
GPU, or recording entry point. Python prepares geometry; Typst authors every
map, rectangle, marker, legend, title, and exported poster.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import numpy as np
from shapely.geometry import GeometryCollection, Polygon, box
from pyproj import Geod, Transformer

HERE = Path(__file__).resolve().parent
if (HERE.parent / "data/map-coast-display.json").exists():
    WORKSPACE = HERE.parent
    DEFAULT_DATA = WORKSPACE / "data"
else:
    WORKSPACE = HERE.parents[1]
    DEFAULT_DATA = WORKSPACE / "work/presentation-draft/data"
EXPECTED = {
    "evaluated": 64875,
    "rejected": 58118,
    "expanded": 6360,
    "terminal": 397,
    "roots": 39435,
}


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n")


def project_path(path: Path) -> str:
    return "/" + path.resolve().relative_to(WORKSPACE).as_posix()


def sorted_cells(cells: np.ndarray) -> np.ndarray:
    return cells[np.lexsort(cells.T[::-1])]


def read_trace(path: Path, terminals: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    with np.load(path, allow_pickle=False) as archive:
        required = (
            "cells",
            "state",
            "depth",
            "gpu",
            "cell_id",
            "parent_id",
            "root_id",
            "batch_id",
            "seed_cells",
            "seed_root_id",
            "seed_gpu",
            "proof_flags",
            "proof_valid",
        )
        if missing := set(required) - set(archive.files):
            raise ValueError(
                f"Current exact-lineage trace required; missing {sorted(missing)}."
            )
        trace = {name: archive[name] for name in required}
        manifest = json.loads(archive["manifest_utf8"].tobytes().decode())
    cells, states, depths = trace["cells"], trace["state"], trace["depth"]
    n = len(cells)
    if (
        cells.shape != (n, 4)
        or not np.isfinite(cells).all()
        or np.any(cells[:, 2:] <= cells[:, :2])
    ):
        raise ValueError("Trace rectangles must have finite, strictly ordered bounds.")
    for name in required[1:8] + ("proof_flags", "proof_valid"):
        if trace[name].shape != (n,):
            raise ValueError(f"Trace {name} must align with cells.")
    if np.any((states < 0) | (states > 4)) or np.any(depths < 0):
        raise ValueError("Unknown state or invalid exact generation depth.")
    identities = trace["cell_id"]
    if len(np.unique(identities)) != n:
        raise ValueError("Trace cell identities must be unique.")
    positions = {int(value): i for i, value in enumerate(identities)}
    root_rows = np.flatnonzero(trace["parent_id"] == -1)
    if np.any(depths[root_rows] != 0):
        raise ValueError("Every root must have exact generation zero.")
    child_counts = np.zeros(n, dtype=np.int32)
    for i in np.flatnonzero(trace["parent_id"] != -1):
        parent = positions.get(int(trace["parent_id"][i]))
        if parent is None:
            raise ValueError("A child references a missing evaluated parent.")
        if depths[i] != depths[parent] + 1 or states[parent] != 0:
            raise ValueError(
                "Lineage depth or retained-parent decision is inconsistent."
            )
        if trace["root_id"][i] != trace["root_id"][parent]:
            raise ValueError("A child changed root identity.")
        if np.any(cells[i, :2] < cells[parent, :2] - 1e-7) or np.any(
            cells[i, 2:] > cells[parent, 2:] + 1e-7
        ):
            raise ValueError("A child lies outside its actual parent.")
        child_counts[parent] += 1
    terminal_size = float(manifest.get("config", {}).get("terminal_size_m", 1.0))
    widths = np.max(cells[:, 2:] - cells[:, :2], axis=1)
    terminal_mask = (states == 0) & (widths <= terminal_size)
    expanded = (states == 0) & ~terminal_mask
    if np.any(child_counts[expanded] != 4) or np.any(child_counts[~expanded] != 0):
        raise ValueError(
            "Every expanded actual cell must have four children, all other cells none."
        )
    if not np.array_equal(
        sorted_cells(cells[root_rows]), sorted_cells(trace["seed_cells"])
    ):
        raise ValueError("Recorded seed rectangles differ from evaluated roots.")
    with np.load(terminals, allow_pickle=False) as archive:
        frozen = {
            name: archive[name] for name in ("cells", "status", "flags", "residual_deg")
        }
    if not np.array_equal(
        sorted_cells(cells[terminal_mask]), sorted_cells(frozen["cells"])
    ):
        raise ValueError(
            "Recorded retained terminal rectangles differ from frozen current results."
        )
    counts = {
        "evaluated": n,
        "rejected": int(np.count_nonzero(states)),
        "expanded": int(np.count_nonzero(expanded)),
        "terminal": int(np.count_nonzero(terminal_mask)),
        "roots": len(root_rows),
    }
    if counts != EXPECTED:
        raise ValueError(
            f"Current recorded traversal parity failed: {counts} != {EXPECTED}."
        )
    trace.update(
        manifest=manifest,
        terminal_mask=terminal_mask,
        widths=widths,
        root_rows=root_rows,
    )
    return trace, {
        "counts": counts,
        "frozen": frozen,
        "terminal_size_m": terminal_size,
        "lineage_verified": True,
        "frozen_terminal_rectangles_bitwise_identical": True,
    }


def polygons(geometry: Any):
    if geometry.is_empty:
        return
    if geometry.geom_type == "Polygon":
        yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from polygons(part)


def clip_map(source: dict[str, Any], bounds: list[float]) -> dict[str, Any]:
    crop = box(*bounds)
    result: dict[str, Any] = {"bounds": bounds}
    for layer in ("water", "land", "unknown"):
        geometry = GeometryCollection([Polygon(p[0], p[1:]) for p in source[layer]])
        result[layer] = [
            [
                list(map(list, p.exterior.coords)),
                *[list(map(list, ring.coords)) for ring in p.interiors],
            ]
            for p in polygons(geometry.intersection(crop))
        ]
    for name in ("aids", "ais", "unknown_objects"):
        result[name] = [
            p
            for p in source.get(name, [])
            if bounds[0] <= p[0] <= bounds[2] and bounds[1] <= p[1] <= bounds[3]
        ]
    result["native_width_mm"] = 140
    result["native_height_mm"] = 140 * (bounds[3] - bounds[1]) / (bounds[2] - bounds[0])
    return result


def merge_rows(rectangles: np.ndarray) -> np.ndarray:
    """Merge actual adjacent rectangles with identical row bounds for display."""
    if not len(rectangles):
        return np.empty((0, 4), dtype=float)
    order = np.lexsort((rectangles[:, 0], rectangles[:, 3], rectangles[:, 1]))
    merged: list[list[float]] = []
    for x0, y0, x1, y1 in rectangles[order]:
        if (
            merged
            and y0 == merged[-1][1]
            and y1 == merged[-1][3]
            and abs(x0 - merged[-1][2]) <= 1e-7
        ):
            merged[-1][2] = float(x1)
        else:
            merged.append([float(x0), float(y0), float(x1), float(y1)])
    return np.asarray(merged)


def display_rectangles(
    cells: np.ndarray,
    bounds: list[float],
    *,
    ppi: float,
    width_mm: float,
    height_mm: float,
    min_pixels: int = 1,
) -> tuple[list[list[float]], dict[str, int]]:
    """Clip, merge neighboring row cells, and merge enlarged subpixel displays."""
    b = np.asarray(bounds)
    visible = cells[
        (cells[:, 0] < b[2])
        & (cells[:, 2] > b[0])
        & (cells[:, 1] < b[3])
        & (cells[:, 3] > b[1])
    ].copy()
    if not len(visible):
        return [], {
            "input_cells": len(cells),
            "clipped_cells": 0,
            "row_merged": 0,
            "display_rectangles": 0,
            "subpixel_enlarged_rectangles": 0,
        }
    visible[:, :2] = np.maximum(visible[:, :2], b[:2])
    visible[:, 2:] = np.minimum(visible[:, 2:], b[2:])
    merged = merge_rows(visible)
    mm_per_m = min(width_mm / (b[2] - b[0]), height_mm / (b[3] - b[1]))
    pixels_per_m = mm_per_m * ppi / 25.4
    enlarged = 0
    displays = set()
    for row in merged:
        row = row.copy()
        small = False
        for axis in (0, 1):
            if (row[axis + 2] - row[axis]) * pixels_per_m < min_pixels:
                center = ((row[axis] + row[axis + 2]) / 2 - b[axis]) * pixels_per_m
                pixel = math.floor(center - min_pixels / 2)
                row[axis] = b[axis] + pixel / pixels_per_m
                row[axis + 2] = b[axis] + (pixel + min_pixels) / pixels_per_m
                small = True
        enlarged += int(small)
        displays.add(tuple(map(float, row)))
    return [list(row) for row in sorted(displays)], {
        "input_cells": len(cells),
        "clipped_cells": len(visible),
        "row_merged": len(merged),
        "display_rectangles": len(displays),
        "subpixel_enlarged_rectangles": enlarged,
    }


def enclosing_bounds(cells: np.ndarray) -> list[float]:
    return [
        float(cells[:, 0].min()),
        float(cells[:, 1].min()),
        float(cells[:, 2].max()),
        float(cells[:, 3].max()),
    ]


def right_bounds(frontier: np.ndarray, harbor: list[float]) -> tuple[list[float], str]:
    current = enclosing_bounds(frontier)
    if any(current[i] < harbor[i] for i in (0, 1)) or any(
        current[i] > harbor[i] for i in (2, 3)
    ):
        return harbor, "Charleston reference"
    ratio = 152 / 132
    w = max((current[2] - current[0]) * 1.2, 30.0)
    h = max((current[3] - current[1]) * 1.2, 30.0)
    w, h = max(w, h * ratio), max(h, w / ratio)
    cx, cy = (current[0] + current[2]) / 2, (current[1] + current[3]) / 2
    candidate = [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]
    if any(candidate[i] < harbor[i] for i in (0, 1)) or any(
        candidate[i] > harbor[i] for i in (2, 3)
    ):
        return harbor, "Charleston reference"
    return candidate, "All surviving regions"


def north_angle(bounds: list[float]) -> float:
    """True-north direction at the displayed map center in EPSG 32618."""
    x = (bounds[0] + bounds[2]) / 2
    y = (bounds[1] + bounds[3]) / 2
    inverse = Transformer.from_crs(32618, 4326, always_xy=True)
    forward = Transformer.from_crs(4326, 32618, always_xy=True)
    lon, lat = inverse.transform(x, y)
    lon_north, lat_north, _ = Geod(ellps="WGS84").fwd(lon, lat, 0, 1000)
    nx, ny = forward.transform(lon_north, lat_north)
    return math.degrees(math.atan2(ny - y, nx - x))


def format_width(width: float) -> str:
    return f"{width / 1000:g} km" if width >= 1000 else f"{width:g} m"


def build(args: argparse.Namespace) -> tuple[list[tuple[Path, Path]], dict[str, Any]]:
    trace, parity = read_trace(args.trace, args.terminals)
    coast = json.loads(args.coast.read_text())
    harbor = json.loads(args.harbor.read_text())
    result = json.loads((DEFAULT_DATA / "run-result.json").read_text())
    if coast["crs"] != "EPSG:32618" or harbor["crs"] != "EPSG:32618":
        raise ValueError(
            "Display exports must use the exact run's projected EPSG:32618 coordinates."
        )
    output = args.output
    background_dir = output / "backgrounds"
    stage_dir = output / "stages"
    background_dir.mkdir(parents=True, exist_ok=True)
    stages: list[dict[str, Any]] = []
    basemaps: dict[tuple[float, ...], dict[str, Any]] = {}
    coast_data = dict(
        bounds=coast["bounds"],
        **{k: coast[k] for k in ("water", "land", "unknown")},
        aids=[],
        ais=[],
        unknown_objects=[],
        native_width_mm=140,
        native_height_mm=140
        * (coast["bounds"][3] - coast["bounds"][1])
        / (coast["bounds"][2] - coast["bounds"][0]),
    )
    write(background_dir / "coast.json", coast_data)
    background_specs = [(background_dir / "coast.json", background_dir / "coast.svg")]
    cells, state, depth = trace["cells"], trace["state"], trace["depth"]
    indices = [(None, trace["seed_cells"], np.empty((0, 4)))]
    for d in sorted(np.unique(depth)):
        current = depth == d
        retained = cells[current & (state == 0)]
        earlier_terminal = cells[(depth < d) & trace["terminal_mask"]]
        frontier = np.concatenate((retained, earlier_terminal))
        rejected = cells[current & (state != 0)]
        indices.append((int(d), frontier, rejected))
    indices.append(("final", cells[trace["terminal_mask"]], np.empty((0, 4))))
    for index, (generation, kept, rejected) in enumerate(indices):
        if not len(kept):
            raise ValueError(
                "Current verified trace unexpectedly has no surviving frontier."
            )
        bounds, view_title = right_bounds(kept, harbor["bounds"])
        if generation is None:
            bounds, view_title = harbor["bounds"], "Charleston reference"
        key = tuple(bounds)
        if key not in basemaps:
            suffix = f"harbor-{len(basemaps):02d}"
            data_path, svg_path = (
                background_dir / f"{suffix}.json",
                background_dir / f"{suffix}.svg",
            )
            write(data_path, clip_map(harbor, bounds))
            background_specs.append((data_path, svg_path))
            basemaps[key] = {"path": project_path(svg_path), "bounds": bounds}
        current_rows = (
            depth == generation
            if isinstance(generation, int)
            else np.zeros(len(cells), dtype=bool)
        )
        stage: dict[str, Any] = {
            "index": index,
            "generation": generation,
            "final": generation == "final",
            "title": "Recorded terminal centers"
            if generation == "final"
            else "Initial root cells"
            if generation is None
            else "Whole-cell pruning",
            "width_label": "Frozen center scores"
            if generation == "final"
            else format_width(float(trace["widths"][current_rows].max()))
            if generation is not None
            else format_width(
                float(np.max(trace["seed_cells"][:, 2:] - trace["seed_cells"][:, :2]))
            )
            + " roots",
            "counts": {
                "retained": len(kept),
                "rejected": len(rejected),
                "evaluated": int(np.count_nonzero(current_rows)),
                "cumulative_rejected": int(
                    np.count_nonzero((state != 0) & (depth <= generation))
                )
                if isinstance(generation, int)
                else 0,
            },
            "reason_counts": {
                str(int(k)): int(v)
                for k, v in zip(
                    *np.unique(state[current_rows], return_counts=True), strict=True
                )
            },
            "right_title": view_title,
            "frontier_bounds": enclosing_bounds(kept),
            "observer": harbor["observer"],
            "estimate": [result["estimate"]["east_m"], result["estimate"]["north_m"]],
            "display_aggregation": {},
            "coast": {
                "bounds": coast["bounds"],
                "background": project_path(background_dir / "coast.svg"),
            },
            "harbor": {"bounds": bounds, "background": basemaps[key]["path"]},
            "terminal_centers": [],
        }
        if generation == "final":
            widths = trace["widths"][trace["terminal_mask"]]
            last_generation = depth == depth.max()
            stage["display_counts"] = {
                "retained": len(kept),
                "rejected": int(np.count_nonzero(last_generation & (state != 0))),
            }
            stage["display_metric_scope"] = (
                "Last geometric pruning generation; final colors use frozen center classifications"
            )
        elif generation is None:
            widths = np.max(
                trace["seed_cells"][:, 2:] - trace["seed_cells"][:, :2], axis=1
            )
            stage["display_counts"] = stage["counts"]
        else:
            widths = trace["widths"][current_rows]
            stage["display_counts"] = stage["counts"]
        stage["cell_size_label"] = format_width(float(widths.max()))
        for panel_name in ("coast", "harbor"):
            stage[panel_name]["north_angle_deg"] = north_angle(
                stage[panel_name]["bounds"]
            )
        for name, pane, w in (
            ("coast", stage["coast"], 130),
            ("harbor", stage["harbor"], 152),
        ):
            pane["retained"], ka = display_rectangles(
                kept,
                cast(list[float], pane["bounds"]),
                ppi=args.ppi,
                width_mm=w,
                height_mm=132,
                min_pixels=4 if name == "coast" and generation is not None else 1,
            )
            pane["rejected"], ra = display_rectangles(
                rejected,
                cast(list[float], pane["bounds"]),
                ppi=args.ppi,
                width_mm=w,
                height_mm=132,
            )
            stage["display_aggregation"][name] = {"retained": ka, "rejected": ra}
        if generation == "final":
            frozen = parity["frozen"]
            centers = (frozen["cells"][:, :2] + frozen["cells"][:, 2:]) / 2
            stage["terminal_centers"] = [
                {"xy": p.tolist(), "status": int(s)}
                for p, s in zip(centers, frozen["status"], strict=True)
            ]
            stage["status_counts"] = {
                str(int(k)): int(v)
                for k, v in zip(
                    *np.unique(frozen["status"], return_counts=True), strict=True
                )
            }
        path = stage_dir / f"stage-{index:02d}.json"
        stage["data_file"] = project_path(path)
        write(path, stage)
        stages.append(stage)
    counts = parity["counts"]
    body_path = output / "body-poster.json"
    write(body_path, stages[-1])
    manifest = {
        "schema": "CurrentPruningMovieV1",
        "crs": "EPSG:32618",
        "authoring_source": {
            "python": {
                "path": project_path(Path(__file__)),
                "sha256": digest(Path(__file__)),
            },
            "typst": {
                "path": project_path(HERE / "pruning-movie.typ"),
                "sha256": digest(HERE / "pruning-movie.typ"),
            },
        },
        "parity": {k: v for k, v in parity.items() if k != "frozen"},
        "expected": EXPECTED,
        "trace_source": {
            "path": project_path(args.trace),
            "sha256": digest(args.trace),
        },
        "terminal_source": {
            "path": project_path(args.terminals),
            "sha256": digest(args.terminals),
        },
        "map_sources": [
            {"path": project_path(p), "sha256": digest(p)}
            for p in (args.coast, args.harbor)
        ],
        "source_metadata": {"coast": coast["metadata"], "harbor": harbor["metadata"]},
        "movie": {
            "duration_s": args.duration,
            "fps": args.fps,
            "chronological_execution": False,
            "ordering": "Exact generation/scale order, not GPU dispatch order",
            "measured_search_runtime": False,
        },
        "zoom": {
            "rule": "Fixed harbor until all globally surviving rectangles fit; thereafter include every surviving bound",
            "minimum_span_m": 30,
        },
        "aggregation": {
            "exact_trace_preserved": True,
            "adjacent_row_merge_tolerance_m": 1e-7,
            "subpixel": "Coast retained rectangles magnified to at least four pixels after the initial frame; others at least one pixel. Coincident display rectangles merge. Exact source cells remain in the trace.",
            "cells_are_not_center_scores": True,
            "final_overlay": "Frozen center statuses, not whole-cell feasibility",
        },
        "totals": counts,
        "stages": stages,
        "background_specs": [
            {"data": project_path(a), "svg": project_path(b)}
            for a, b in background_specs
        ],
    }
    write(output / "movie-manifest.json", manifest)
    write(output / "storyboard.json", {"stages": [s["data_file"] for s in stages]})
    return background_specs, manifest


def compile_typst(
    data: Path, output: Path, args: argparse.Namespace, mode: str
) -> None:
    subprocess.run(
        [
            "typst",
            "compile",
            "--root",
            str(WORKSPACE),
            "--input",
            f"mode={mode}",
            "--input",
            f"data-file={project_path(data)}",
            "--ppi",
            str(args.ppi),
            str(HERE / "pruning-movie.typ"),
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def render(
    args: argparse.Namespace, backgrounds: list[Any], manifest: dict[str, Any]
) -> None:
    output = args.output
    (output / "frames").mkdir(exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [
            pool.submit(compile_typst, a, b, args, "background") for a, b in backgrounds
        ]
        for future in concurrent.futures.as_completed(futures):
            future.result()
    compile_typst(
        output / "storyboard.json", output / "storyboard.pdf", args, "storyboard"
    )
    compile_typst(
        output / "storyboard.json", output / "frames/frame-{p}.png", args, "storyboard"
    )
    final_index = len(manifest["stages"])
    shutil.copyfile(output / f"frames/frame-{final_index}.png", output / "poster.png")
    compile_typst(output / "body-poster.json", output / "body-poster.pdf", args, "body")
    compile_typst(output / "body-poster.json", output / "body-poster.png", args, "body")
    n = len(manifest["stages"])
    middle_hold = (args.duration - 3 - 5) / (n - 2)
    listing = output / "frames/concat.txt"
    holds = [3] + [middle_hold] * (n - 2) + [5]
    listing.write_text(
        "".join(
            f"file 'frame-{i + 1}.png'\nduration {hold:.9f}\n"
            for i, hold in enumerate(holds)
        )
        + f"file 'frame-{n}.png'\n"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-vf",
            f"fps={args.fps}",
            "-fps_mode",
            "cfr",
            "-t",
            str(args.duration),
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output / "pruning-playback.mp4"),
        ],
        check=True,
    )
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,avg_frame_rate,duration,nb_frames",
            "-of",
            "json",
            str(output / "pruning-playback.mp4"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    actual_duration = float(stream["duration"])
    if abs(actual_duration - args.duration) > 1 / args.fps:
        raise ValueError(
            f"Encoded duration {actual_duration}s differs from requested {args.duration}s."
        )
    manifest["movie"]["encoded_video"] = stream
    manifest["artifacts"] = {
        name: {"bytes": (output / name).stat().st_size, "sha256": digest(output / name)}
        for name in (
            "pruning-playback.mp4",
            "poster.png",
            "storyboard.pdf",
            "body-poster.pdf",
            "body-poster.png",
        )
    }
    manifest["movie"]["stage_holds_s"] = holds
    write(output / "movie-manifest.json", manifest)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, default=HERE / "pruning-trace.npz")
    parser.add_argument(
        "--terminals", type=Path, default=HERE / "frozen-terminal-results.npz"
    )
    parser.add_argument(
        "--coast", type=Path, default=DEFAULT_DATA / "map-coast-display.json"
    )
    parser.add_argument(
        "--harbor", type=Path, default=DEFAULT_DATA / "map-charleston.json"
    )
    parser.add_argument("--output", type=Path, default=HERE / "movie")
    parser.add_argument("--duration", type=float, default=38)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--ppi", type=float, default=152.4)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--data-only", action="store_true")
    args = parser.parse_args()
    if (
        not 30 <= args.duration <= 45
        or args.fps <= 0
        or args.ppi <= 0
        or args.jobs <= 0
    ):
        parser.error("Duration must be 30–45s; fps, ppi, and jobs must be positive.")
    args.output = args.output.resolve()
    backgrounds, manifest = build(args)
    print(
        json.dumps(
            {
                "stage": "data_ready",
                "stages": len(manifest["stages"]),
                "parity": manifest["totals"],
            }
        ),
        flush=True,
    )
    if not args.data_only:
        render(args, backgrounds, manifest)
    print(f"Saved {args.output}", flush=True)


if __name__ == "__main__":
    main()
