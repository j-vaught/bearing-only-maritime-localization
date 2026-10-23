"""Recreate the largest-error synthetic trial for the release figure."""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import numpy as np

from bearing_localization.algorithms import grid_search
from bearing_localization.scene import (
    SceneConfig,
    generate_scene,
    random_ship_pose,
    simulate_observations,
)

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results" / "synthetic-baseline-v0.1.0"
OUTPUT = ROOT / "figures" / "data"


def read_config() -> dict[str, float | int]:
    with (RESULTS / "config.json").open(encoding="utf-8") as stream:
        return json.load(stream)


def select_failed_trial() -> int:
    rows = []
    with (RESULTS / "trials.csv").open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if (
                row["visibility_m"] == "10000.0"
                and row["type_labels"] == "True"
                and row["algorithm"] == "grid_refined"
                and row["requested_features"] == "all"
                and row["status"] == "estimated"
            ):
                rows.append(row)
    failed = [row for row in rows if row["success"] == "False"]
    if not failed:
        raise RuntimeError("The selected benchmark has no failed full-feature trial at 10 km.")
    worst = max(failed, key=lambda row: float(row["position_error_m"]))
    return int(worst["trial"])


def export_benchmark_figure_data() -> None:
    source_rows = {}
    with (RESULTS / "summary.csv").open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["requested_features"] != "all" or row["visibility_m"] == "1000.0":
                continue
            source_rows[
                (
                    float(row["visibility_m"]) / 1_000.0,
                    row["type_labels"],
                    row["algorithm"],
                )
            ] = row
    rows = []
    for radius in (5.0, 10.0):
        row: dict[str, object] = {"visibility_km": radius}
        for labels, label_suffix in (("True", "types"), ("False", "no_types")):
            for algorithm, algorithm_suffix in (("grid", "grid"), ("grid_refined", "refined")):
                source = source_rows[(radius, labels, algorithm)]
                row[f"latency_{algorithm_suffix}_{label_suffix}"] = source["median_latency_ms"]
                row[f"success_{algorithm_suffix}_{label_suffix}"] = source[
                    "success_rate_all_trials"
                ]
                row[f"p95_error_{algorithm_suffix}_{label_suffix}"] = source["p95_position_error_m"]
        rows.append(row)
    write_csv(
        OUTPUT / "benchmark_summary.csv",
        list(rows[0]),
        rows,
    )


def write_csv(path: Path, fields: list[str], rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    config = read_config()
    export_benchmark_figure_data()
    trial = select_failed_trial()
    trial_count = int(config["trials"])
    seed = int(config["seed"])
    trial_seed = np.random.SeedSequence(seed).spawn(trial_count)[trial]
    rng = np.random.default_rng(trial_seed)
    scene_config = SceneConfig(
        width_m=float(config["scene_km"]) * 1_000.0,
        height_m=float(config["scene_km"]) * 1_000.0,
        object_count=int(config["object_count"]),
        ais_fraction=float(config["ais_fraction"]),
        visibility_radius_m=10_000.0,
        bearing_noise_sigma_deg=float(config["noise_deg"]),
    )
    scene = generate_scene(scene_config, rng)
    ship = random_ship_pose(scene, rng, edge_margin_m=10_000.0)
    visible_scene = replace(
        scene,
        config=replace(scene.config, visibility_radius_m=10_000.0),
    )
    observations, truth = simulate_observations(visible_scene, ship, rng)
    ais_xy = np.array([[item.east_m, item.north_m] for item in scene.ais], dtype=float)
    ais_types: list[str | None] = [item.object_type for item in scene.ais]
    reported = [(item.object_type, item.bearing_deg) for item in observations]
    estimate = grid_search(
        reported,
        ais_xy,
        ais_types,
        (
            -scene_config.width_m / 2,
            scene_config.width_m / 2,
            -scene_config.height_m / 2,
            scene_config.height_m / 2,
        ),
        sigma_deg=scene_config.bearing_noise_sigma_deg,
        grid_spacing_m=float(config["grid_km"]) * 1_000.0,
        refine_starts=int(config["refine_starts"]),
        use_types=True,
        refine=True,
        visibility_radius_m=10_000.0,
        penalize_missing_ais=True,
    )

    selected_ids = {item.ais_id for item in scene.ais}
    visible_lookup = {
        object_id: (index, object_id in selected_ids)
        for index, object_id in enumerate(truth.visible_object_ids)
    }
    matched_by_ais: dict[int, list[tuple[int, bool]]] = {}
    for observation_index, ais_index in estimate.matches:
        ais_id = scene.ais[ais_index].ais_id
        is_correct = (
            observation_index < len(truth.visible_object_ids)
            and truth.visible_object_ids[observation_index] == ais_id
        )
        matched_by_ais.setdefault(ais_id, []).append((observation_index, is_correct))

    landmark_rows = []
    for landmark in scene.landmarks:
        landmark_rows.append(
            {
                "object_id": landmark.object_id,
                "east_km": landmark.east_m / 1_000.0,
                "north_km": landmark.north_m / 1_000.0,
                "object_type": landmark.object_type,
                "has_ais": landmark.has_ais,
                "visible": landmark.object_id in visible_lookup,
                "selected_by_estimator": landmark.object_id in matched_by_ais,
                "correct_association": any(
                    correct for _, correct in matched_by_ais.get(landmark.object_id, [])
                ),
            }
        )
    write_csv(
        OUTPUT / "failure_landmarks.csv",
        [
            "object_id",
            "east_km",
            "north_km",
            "object_type",
            "has_ais",
            "visible",
            "selected_by_estimator",
            "correct_association",
        ],
        landmark_rows,
    )

    pose_row = {
        "trial": trial,
        "truth_east_km": ship.east_m / 1_000.0,
        "truth_north_km": ship.north_m / 1_000.0,
        "estimate_east_km": estimate.east_m / 1_000.0,
        "estimate_north_km": estimate.north_m / 1_000.0,
        "position_error_km": np.hypot(
            estimate.east_m - ship.east_m, estimate.north_m - ship.north_m
        )
        / 1_000.0,
        "visibility_radius_km": 10,
        "observed_features": len(observations),
        "observed_ais_features": len(truth.visible_ais_ids),
        "correct_associations": sum(
            is_correct for pairs in matched_by_ais.values() for _, is_correct in pairs
        ),
        "predicted_associations": len(estimate.matches),
    }
    write_csv(OUTPUT / "failure_pose.csv", list(pose_row), [pose_row])

    observed_rows = []
    for index, observation in enumerate(observations):
        true_id = truth.visible_object_ids[index]
        matched_id = next(
            (scene.ais[ais_i].ais_id for obs_i, ais_i in estimate.matches if obs_i == index),
            "",
        )
        observed_rows.append(
            {
                "observation_index": index,
                "bearing_deg_from_north": observation.bearing_deg,
                "object_type": observation.object_type,
                "true_object_id": true_id,
                "true_object_has_ais": true_id in selected_ids,
                "matched_ais_id": matched_id,
                "association_correct": bool(matched_id != "" and matched_id == true_id),
            }
        )
    write_csv(
        OUTPUT / "failure_observations.csv",
        [
            "observation_index",
            "bearing_deg_from_north",
            "object_type",
            "true_object_id",
            "true_object_has_ais",
            "matched_ais_id",
            "association_correct",
        ],
        observed_rows,
    )
    print(
        f"Exported trial {trial}: {pose_row['position_error_km']:.2f} km error, "
        f"{pose_row['correct_associations']}/{pose_row['predicted_associations']} "
        "correct matches."
    )


if __name__ == "__main__":
    main()
