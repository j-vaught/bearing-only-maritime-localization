"""Render four zoom previews from the recorded trace, without rebuilding video."""

from pathlib import Path
import hashlib
import importlib.util
import json
import subprocess

import numpy as np
from pyproj import Geod, Transformer
import math

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]


def write(path, data):
    path.write_text(json.dumps(data, separators=(",", ":")) + "\n")


def north_angle(bounds):
    x = (bounds[0] + bounds[2]) / 2
    y = (bounds[1] + bounds[3]) / 2
    inverse = Transformer.from_crs(32618, 4326, always_xy=True)
    forward = Transformer.from_crs(4326, 32618, always_xy=True)
    lon, lat = inverse.transform(x, y)
    nlon, nlat, _ = Geod(ellps="WGS84").fwd(lon, lat, 0, 1000)
    nx, ny = forward.transform(nlon, nlat)
    return math.degrees(math.atan2(ny - y, nx - x))


def main():
    source = ROOT / "pruning/build_pruning_movie.py"
    spec = importlib.util.spec_from_file_location("recorded_movie", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("The saved movie builder could not be loaded")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    harbor = json.loads((ROOT / "data/map-charleston.json").read_text())
    reference = np.asarray(harbor["bounds"], dtype=float)
    center = (reference[:2] + reference[2:]) / 2
    base_span = reference[2:] - reference[:2]
    with np.load(ROOT / "pruning/pruning-trace.npz", allow_pickle=False) as archive:
        cells, states, depth = archive["cells"], archive["state"], archive["depth"]
    records = []
    for generation, zoom, name in [
        (9, 16, "19p53125m-16x"),
        (9, 32, "19p53125m-32x"),
        (10, 16, "9p765625m-16x"),
        (10, 32, "9p765625m-32x"),
    ]:
        stage_path = ROOT / f"pruning/movie/stages/stage-{generation + 1:02}.json"
        stage = json.loads(stage_path.read_text())
        span = base_span / zoom
        bounds = np.r_[center - span / 2, center + span / 2].tolist()
        background_json = HERE / f"{name}-background.json"
        background_svg = HERE / f"{name}-background.svg"
        write(background_json, builder.clip_map(harbor, bounds))
        subprocess.run(
            [
                "typst",
                "compile",
                "--root",
                str(ROOT),
                "--input",
                "mode=background",
                "--input",
                f"data-file={builder.project_path(background_json)}",
                str(HERE / "pruning-movie.typ"),
                str(background_svg),
            ],
            check=True,
        )
        stage["harbor"]["bounds"] = bounds
        stage["harbor"]["background"] = builder.project_path(background_svg)
        stage["harbor"]["north_angle_deg"] = north_angle(bounds)
        stage["coast"]["north_angle_deg"] = north_angle(stage["coast"]["bounds"])
        for label, state_mask in [("retained", states == 0), ("rejected", states != 0)]:
            stage["harbor"][label], aggregation = builder.display_rectangles(
                cells[(depth == generation) & state_mask],
                bounds,
                ppi=152.4,
                width_mm=152,
                height_mm=148,
            )
            stage["display_aggregation"]["harbor"][label] = aggregation
        stage["cell_size_label"] = stage["width_label"]
        stage["display_counts"] = stage["counts"]
        stage["right_title"] = "Charleston"
        stage["preview_metadata"] = {
            "zoom_relative_to_reference": zoom,
            "extent_m": span.tolist(),
            "center_en_m": center.tolist(),
            "counts_scope": "Entire recorded coast-wide generation",
            "source_stage": str(stage_path.relative_to(ROOT)),
            "full_video_rebuilt": False,
        }
        data_path = HERE / f"{name}.json"
        write(data_path, stage)
        for suffix in ["png", "pdf"]:
            subprocess.run(
                [
                    "typst",
                    "compile",
                    "--root",
                    str(ROOT),
                    "--input",
                    "mode=frame",
                    "--input",
                    f"data-file={builder.project_path(data_path)}",
                    "--ppi",
                    "152.4",
                    str(HERE / "pruning-movie.typ"),
                    str(HERE / f"{name}.{suffix}"),
                ],
                check=True,
            )
        records.append(
            {
                "name": name,
                "generation": generation,
                "zoom": zoom,
                "bounds": bounds,
                "counts": stage["counts"],
            }
        )
    write(
        HERE / "preview-manifest.json",
        {
            "scope": "Four static frames only, no search or video rebuild",
            "trace_sha256": hashlib.sha256(
                (ROOT / "pruning/pruning-trace.npz").read_bytes()
            ).hexdigest(),
            "views": records,
        },
    )
    print(json.dumps(records, indent=2))


if __name__ == "__main__":
    main()
