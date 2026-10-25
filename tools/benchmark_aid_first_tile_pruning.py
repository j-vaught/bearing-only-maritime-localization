"""Benchmark chart-aid-first tile screening followed by combined refinement."""

from __future__ import annotations

import argparse
import csv
import json
import math
import runpy
from dataclasses import replace
from pathlib import Path
from time import perf_counter

import numpy as np

from bearing_localization.algorithms import associate
from bearing_localization.scene import AISRecord as SceneAISRecord
from bearing_localization.scene import Landmark, Scene, SceneConfig, ShipPose
from bearing_localization.search import HierarchicalSearchConfig, run_hierarchical_search
from bearing_localization.simulator.runner import run_simulator
from maritime_localization_shared.contracts import AISSnapshot
from maritime_localization_shared.geometry.angular_grouping import (
    DetectedObject,
    group_angular_detections,
)
from maritime_localization_shared.geospatial.raster_los import raster_line_of_sight
from maritime_localization_shared.geospatial.spatial_index import SpatialIndex2D
from maritime_localization_shared.geospatial.water_mask import WaterMask

ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "results/eastern-seaboard-aid-first"
RANGE_M, ANGULAR_DEG, SIGMA_DEG, PENALTY_DEG = 10_000.0, 0.1, 0.5, 12.0
COARSE_M, TILE_M = 10_000.0, 100_000.0


class Visibility:
    def __init__(self, mask: WaterMask) -> None:
        self.mask = mask

    def is_water(self, east_m: float, north_m: float) -> bool:
        return self.mask.is_water(east_m, north_m)

    def has_line_of_sight(
        self,
        observer_east_m: float,
        observer_north_m: float,
        target_east_m: float,
        target_north_m: float,
    ) -> bool:
        return raster_line_of_sight(
            self.mask,
            (observer_east_m, observer_north_m),
            (target_east_m, target_north_m),
        )


def group_visible(x, y, xy, classes, indices):
    if len(indices) == 0:
        return indices, []
    delta = xy[indices] - np.asarray([x, y])
    ranges = np.hypot(delta[:, 0], delta[:, 1])
    bearings = np.degrees(np.arctan2(delta[:, 0], delta[:, 1]))
    groups = group_angular_detections(
        [
            DetectedObject(
                str(int(idx)),
                classes[int(idx)] or "unknown",
                float(bearings[local]),
                float(ranges[local]),
            )
            for local, idx in enumerate(indices)
        ],
        ANGULAR_DEG,
    )
    local_by_id = {int(idx): local for local, idx in enumerate(indices)}
    reps = [
        min(
            (local_by_id[int(idx)] for idx in group),
            key=lambda local: (float(ranges[local]), int(indices[local])),
        )
        for group in groups.truth_groups
    ]
    selected = indices[np.asarray(reps, dtype=int)]
    return selected, [classes[int(idx)] for idx in selected]


def rank_tiles(observations, xy, classes, bounds, visibility):
    xmin, xmax, ymin, ymax = bounds
    index = SpatialIndex2D(xy, cell_size_m=RANGE_M)
    scores = {}
    evaluations = 0
    for x in np.arange(xmin, xmax + COARSE_M * 0.5, COARSE_M):
        for y in np.arange(ymin, ymax + COARSE_M * 0.5, COARSE_M):
            x, y = float(x), float(y)
            if not visibility.is_water(x, y):
                continue
            nearby = index.query_radius(x, y, RANGE_M)
            visible = np.asarray(
                [
                    i
                    for i in nearby
                    if visibility.has_line_of_sight(x, y, float(xy[i, 0]), float(xy[i, 1]))
                ],
                dtype=int,
            )
            predicted, kinds = group_visible(x, y, xy, classes, visible)
            score, _ = associate(
                np.asarray([x, y]),
                observations,
                xy[predicted],
                kinds,
                SIGMA_DEG,
                PENALTY_DEG,
                True,
                float("inf"),
                True,
            )
            tile = (math.floor((x - xmin) / TILE_M), math.floor((y - ymin) / TILE_M))
            scores[tile] = min(scores.get(tile, float("inf")), float(score))
            evaluations += 1
    return scores, evaluations


def tile_bounds(tile, bounds):
    xmin, xmax, ymin, ymax = bounds
    x, y = xmin + tile[0] * TILE_M, ymin + tile[1] * TILE_M
    return max(xmin, x), min(xmax, x + TILE_M), max(ymin, y), min(ymax, y + TILE_M)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tiles", type=int, choices=(8, 32), default=32)
    keep_tiles = parser.parse_args().tiles
    out = OUT_ROOT / f"top{keep_tiles}"
    pilot = runpy.run_path(str(ROOT / "tools/run_ais_enc_pilot.py"))
    bench = runpy.run_path(str(ROOT / "tools/benchmark_eastern_seaboard_search.py"))
    _, _, sites, _ = pilot["load_inputs"]()
    _, _, snapshots, bounds, _ = bench["build_inputs"]()
    catalog: AISSnapshot = snapshots["all_scales_deduplicated"]
    aids = tuple(r for r in catalog.records if r.ais_class == "aid")
    vessels = tuple(r for r in catalog.records if r.ais_class == "vessel")
    xy = np.asarray([(r.east_m, r.north_m) for r in catalog.records])
    aid_xy = np.asarray([(r.east_m, r.north_m) for r in aids])
    aid_classes = [r.ais_class for r in aids]

    packed = np.load(ROOT / "data/maps/eastern-seaboard-water-mask-500m.npz", allow_pickle=False)
    mask = WaterMask(
        packed["water"].astype(bool),
        float(packed["west_m"]),
        float(packed["south_m"]),
        float(packed["cell_size_m"]),
    )
    visibility = Visibility(mask)
    landmarks = tuple(
        Landmark(i, r.east_m, r.north_m, r.ais_class or "unknown", r.ais_class == "vessel")
        for i, r in enumerate(catalog.records)
    )
    scene_ais = tuple(
        SceneAISRecord(i, r.east_m, r.north_m, r.ais_class or "unknown")
        for i, r in enumerate(catalog.records)
        if r.ais_class == "vessel"
    )
    scene = Scene(
        ais=scene_ais,
        landmarks=landmarks,
        config=SceneConfig(
            width_m=bounds[1] - bounds[0],
            height_m=bounds[3] - bounds[2],
            object_count=len(landmarks),
            ais_fraction=len(scene_ais) / len(landmarks),
            visibility_radius_m=RANGE_M,
            bearing_noise_sigma_deg=SIGMA_DEG,
            report_type_labels=True,
            object_types=("vessel", "aid"),
        ),
    )
    with (ROOT / "results/eastern-seaboard-pruning/b1-b5-pruning.csv").open(
        newline="", encoding="utf-8"
    ) as f:
        baseline = {r["site"]: r for r in csv.DictReader(f)}

    rows, details = [], {}
    for si, (site, (tx, ty)) in enumerate(sites.items()):
        sim = run_simulator(
            scene,
            ShipPose(tx, ty, 0.0),
            seed=pilot["SEED_BASE"] + si * 100,
            trial_id=f"{site}-coast-pruning",
            angular_resolution_deg=ANGULAR_DEG,
            map_input=mask,
        )
        aid_obs = [
            (o.object_type, o.bearing_deg)
            for o in sim.measurements.observations
            if o.object_type == "aid"
        ]
        t0 = perf_counter()
        scores, evals = rank_tiles(aid_obs, aid_xy, aid_classes, bounds, visibility)
        screen_ms = (perf_counter() - t0) * 1000
        ranked = sorted(scores.items(), key=lambda v: (v[1], v[0]))
        chosen = [tile for tile, _ in ranked[:keep_tiles]]
        true_tile = (math.floor((tx - bounds[0]) / TILE_M), math.floor((ty - bounds[2]) / TILE_M))
        true_rank = next((i for i, (tile, _) in enumerate(ranked, 1) if tile == true_tile), None)
        if len(aid_obs) < 2:
            chosen = [tile for tile, _ in ranked]

        t1, candidates, regional = perf_counter(), [], []
        for tile in chosen:
            local_bounds = tile_bounds(tile, bounds)
            x0, x1, y0, y1 = local_bounds
            subset = (
                (xy[:, 0] >= x0 - RANGE_M)
                & (xy[:, 0] <= x1 + RANGE_M)
                & (xy[:, 1] >= y0 - RANGE_M)
                & (xy[:, 1] <= y1 + RANGE_M)
            )
            local_catalog = replace(
                catalog, records=tuple(r for r, keep in zip(catalog.records, subset) if keep)
            )
            try:
                estimate = run_hierarchical_search(
                    sim.measurements,
                    local_catalog,
                    local_bounds,
                    HierarchicalSearchConfig(
                        coarse_spacing_m=20_000,
                        refinement_levels=6,
                        beam_width=32,
                        bearing_sigma_deg=SIGMA_DEG,
                        unmatched_penalty_deg=PENALTY_DEG,
                        visibility_radius_m=RANGE_M,
                        use_types=True,
                        penalize_missing_ais=True,
                        angular_resolution_deg=ANGULAR_DEG,
                    ),
                    visibility=visibility,
                )
            except ValueError as exc:
                regional.append({"tile": list(tile), "error": str(exc)})
                continue
            error = math.hypot(estimate.east_m - tx, estimate.north_m - ty)
            candidates.append((estimate.score, estimate.east_m, estimate.north_m, error, tile))
            regional.append(
                {
                    "tile": list(tile),
                    "score": estimate.score,
                    "error_m": error,
                    "elapsed_ms": estimate.elapsed_s * 1000,
                    "catalog_subset_size": len(local_catalog.records),
                }
            )
        refine_ms = (perf_counter() - t1) * 1000
        if not candidates:
            raise RuntimeError(f"{site}: no valid regional refinement")
        best = min(candidates, key=lambda c: c[0])
        row = {
            "site": site,
            "aid_reports": len(aid_obs),
            "total_reports": len(sim.measurements.observations),
            "coarse_candidates": evals,
            "tiles_scored": len(scores),
            "shortlist_tiles": len(chosen),
            "true_tile_rank": true_rank,
            "true_tile_retained": true_tile in chosen,
            "aid_screen_ms": screen_ms,
            "combined_refinement_ms": refine_ms,
            "staged_total_ms": screen_ms + refine_ms,
            "staged_error_m": best[3],
            "staged_success_250m": best[3] <= 250,
            "baseline_global_error_m": float(baseline[site]["position_error_m"]),
            "baseline_global_ms": float(baseline[site]["search_ms"]),
            "best_tile": f"{best[4][0]},{best[4][1]}",
        }
        rows.append(row)
        details[site] = {
            "selected_tiles": [list(t) for t in chosen],
            "top_tile_scores": [{"tile": list(t), "score": s} for t, s in ranked[:20]],
            "regional_refinements": regional,
            "estimate": {"east_m": best[1], "north_m": best[2], "error_m": best[3]},
        }
        print(
            f"{site}: aid_reports={len(aid_obs)}/{len(sim.measurements.observations)} "
            f"true_tile_rank={true_rank} retained={true_tile in chosen} "
            f"error={best[3]:,.0f}m screen={screen_ms:,.0f}ms "
            f"refine={refine_ms:,.0f}ms total={screen_ms + refine_ms:,.0f}ms",
            flush=True,
        )

    out.mkdir(parents=True, exist_ok=True)
    with (out / "b1-b5-aid-first.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (out / "tile-details.json").write_text(json.dumps(details, indent=2) + "\n", encoding="utf-8")
    metadata = {
        "chart_aid_records": len(aids),
        "ais_vessel_records": len(vessels),
        "coarse_spacing_m": COARSE_M,
        "tile_size_m": TILE_M,
        "shortlist_tiles": keep_tiles,
        "range_m": RANGE_M,
        "angle_resolution_deg": ANGULAR_DEG,
        "method": "aid-only tile screening followed by full AIS+aid regional refinement",
        "assumption": (
            "Synthetic scene shares the full catalog and reports every visible angular group."
        ),
        "rows": rows,
        "details": details,
    }
    (out / "config.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    (out / "README.md").write_text(
        "# Chart-aid-first tile screening experiment\n\n"
        "The first stage scores water-valid candidate points on a 10 km lattice using "
        "only aid-labelled reports and chart aids. It applies the 10 km range, land "
        "line-of-sight, 0.1 degree angular grouping, and complete-report penalty. The "
        "best point score ranks each 100 km tile; top-ranked tiles are retained for "
        "refinement. The second "
        "stage uses all reports and the combined AIS plus chart-aid catalog inside each "
        "tile, with a 10 km catalog buffer, and refines each region hierarchically.\n\n"
        "The seeded B1–B5 runs are closed-loop synthetic cases. Results report true-tile "
        "rank and retention, final error, and staged runtime beside the previously "
        "measured global hierarchical baseline. See tile-details.json for per-tile "
        "scores. Reproduce with uv run python tools/benchmark_aid_first_tile_pruning.py "
        "--tiles 8 or --tiles 32.\n",
        encoding="utf-8",
    )
    print(f"Wrote {out / 'b1-b5-aid-first.csv'}")


if __name__ == "__main__":
    main()
