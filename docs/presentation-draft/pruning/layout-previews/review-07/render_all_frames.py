"""Render every recorded stage as still frames with progressive camera zoom."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import importlib.util
import json
import math
import subprocess

import numpy as np
from pyproj import Geod, Transformer

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]


def write(path, data):
    path.write_text(json.dumps(data, separators=(",", ":")) + "\n")


def north_angle(bounds):
    """Return true-north direction at the displayed projected center."""
    x = (bounds[0] + bounds[2]) / 2
    y = (bounds[1] + bounds[3]) / 2
    inverse = Transformer.from_crs(32618, 4326, always_xy=True)
    forward = Transformer.from_crs(4326, 32618, always_xy=True)
    lon, lat = inverse.transform(x, y)
    nlon, nlat, _ = Geod(ellps="WGS84").fwd(lon, lat, 0, 1000)
    nx, ny = forward.transform(nlon, nlat)
    return math.degrees(math.atan2(ny - y, nx - x))


def main():
    spec = importlib.util.spec_from_file_location(
        "recorded_movie", ROOT / "pruning/build_pruning_movie.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load the saved-data rendering helpers")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    with np.load(ROOT / "pruning/pruning-trace.npz", allow_pickle=False) as archive:
        cells, states, depth = archive["cells"], archive["state"], archive["depth"]
    harbor = json.loads((ROOT / "data/map-charleston.json").read_text())
    ref = np.asarray(harbor["bounds"], dtype=float)
    center = (ref[:2] + ref[2:]) / 2
    base_span = ref[2:] - ref[:2]
    backgrounds = HERE / "backgrounds"
    backgrounds.mkdir(exist_ok=True)
    frames = HERE / "frames"
    frames.mkdir(exist_ok=True)
    tasks, rows = [], []
    for index in range(17):
        source = ROOT / f"pruning/movie/stages/stage-{index:02d}.json"
        stage = json.loads(source.read_text())
        generation = 14 if stage["final"] else stage["generation"]
        zoom = 1 if generation is None else 2 ** max(0, generation - 4)
        span = base_span / zoom
        bounds = np.r_[center - span / 2, center + span / 2].tolist()
        bgjson = backgrounds / f"zoom-{zoom}.json"
        bgsvg = backgrounds / f"zoom-{zoom}.svg"
        if not bgjson.exists():
            write(bgjson, helper.clip_map(harbor, bounds))
            tasks.append((bgjson, bgsvg))
        stage["harbor"]["bounds"] = bounds
        stage["harbor"]["background"] = helper.project_path(bgsvg)
        stage["harbor"]["north_angle_deg"] = north_angle(bounds)
        stage["coast"]["north_angle_deg"] = north_angle(stage["coast"]["bounds"])
        g = 14 if generation is None else generation
        current = depth == g
        if generation is None:
            retained = cells[depth == 0]
            rejected = np.empty((0, 4))
            width = float(np.max(retained[:, 2:] - retained[:, :2]))
        else:
            retained = cells[current & (states == 0)]
            rejected = cells[current & (states != 0)]
            width = float(np.max(cells[current, 2:] - cells[current, :2]))
        for label, data in [("retained", retained), ("rejected", rejected)]:
            stage["harbor"][label], aggregation = helper.display_rectangles(
                data,
                bounds,
                ppi=152.4,
                width_mm=152,
                height_mm=148,
            )
            stage["display_aggregation"]["harbor"][label] = aggregation
        stage["cell_size_label"] = helper.format_width(width)
        stage["display_counts"] = {"retained": len(retained), "rejected": len(rejected)}
        centers = (retained[:, :2] + retained[:, 2:]) / 2
        visible = np.all((centers >= bounds[:2]) & (centers <= bounds[2:]), axis=1)
        if stage["final"]:
            stage["terminal_centers"] = [
                p
                for p in stage["terminal_centers"]
                if bounds[0] <= p["xy"][0] <= bounds[2]
                and bounds[1] <= p["xy"][1] <= bounds[3]
            ]
        stage["view_metadata"] = {
            "zoom": zoom,
            "extent_m": span.tolist(),
            "center_en_m": center.tolist(),
            "true_position_marker": "Saved simulated observer, for presentation only",
            "retained_centers_inside_charleston_crop": int(visible.sum()),
            "retained_centers_outside_charleston_crop": int((~visible).sum()),
            "counts_scope": "Full coast, not only the Charleston crop",
            "source_stage": str(source.relative_to(ROOT)),
            "search_executed": False,
            "video_created": False,
        }
        datafile = HERE / f"stage-{index:02d}.json"
        write(datafile, stage)
        rows.append(
            {
                "index": index,
                "generation": generation,
                "initial": generation is None,
                "final_overlay": stage["final"],
                "cell_size_m": width,
                "zoom": zoom,
                "counts": stage["display_counts"],
                "charleston_bounds": bounds,
                "visible_centers": int(visible.sum()),
                "outside_centers": int((~visible).sum()),
                "data": helper.project_path(datafile),
                "image": f"frames/frame-{index + 1:02d}.png",
            }
        )

    def background(task):
        datafile, svg = task
        subprocess.run(
            [
                "typst",
                "compile",
                "--root",
                str(ROOT),
                "--input",
                "mode=background",
                "--input",
                f"data-file={helper.project_path(datafile)}",
                str(HERE / "pruning-movie.typ"),
                str(svg),
            ],
            check=True,
            capture_output=True,
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(background, tasks))
    write(HERE / "storyboard.json", {"stages": [r["data"] for r in rows]})
    subprocess.run(
        [
            "typst",
            "compile",
            "--root",
            str(ROOT),
            "--input",
            "mode=storyboard",
            "--input",
            f"data-file={helper.project_path(HERE / 'storyboard.json')}",
            str(HERE / "pruning-movie.typ"),
            str(HERE / "all-frames.pdf"),
        ],
        check=True,
    )
    subprocess.run(
        [
            "pdftoppm",
            "-png",
            "-r",
            "152.4",
            str(HERE / "all-frames.pdf"),
            str(frames / "frame"),
        ],
        check=True,
    )
    subprocess.run(
        ["pdfseparate", str(HERE / "all-frames.pdf"), str(frames / "frame-%d.pdf")],
        check=True,
    )
    write(
        HERE / "frame-manifest.json",
        {
            "author": "J.C. Vaught",
            "scope": "Still frames only; no video or search",
            "truth": harbor["observer"],
            "trace_sha256": hashlib.sha256(
                (ROOT / "pruning/pruning-trace.npz").read_bytes()
            ).hexdigest(),
            "frames": rows,
        },
    )
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
