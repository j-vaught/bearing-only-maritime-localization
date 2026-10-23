"""Command-line benchmark for simulated bearing-only maritime localization."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import asdict, replace
from pathlib import Path
from time import perf_counter

import numpy as np

from .algorithms import SearchResult, grid_search
from .backends import benchmark_backends
from .scene import (
    AISRecord,
    Observation,
    SceneConfig,
    ShipPose,
    generate_scene,
    random_ship_pose,
    simulate_observations,
)

RAW_FIELDS = [
    "trial",
    "visibility_m",
    "type_labels",
    "algorithm",
    "requested_features",
    "status",
    "observed_features",
    "observed_ais_features",
    "position_error_m",
    "correct_associations",
    "predicted_associations",
    "association_precision",
    "association_recall",
    "success",
    "elapsed_ms",
    "pose_evaluations",
    "normalized_score",
]


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _numeric(row: dict[str, object], key: str) -> float:
    value = row[key]
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    raise TypeError(f"{key} is not numeric: {value!r}")


def _feature_subsets(
    count: int,
    requested: list[int],
    rng: np.random.Generator,
) -> list[tuple[str, int | None, np.ndarray | None]]:
    rows: list[tuple[str, int | None, np.ndarray | None]] = [("all", None, None)]
    for feature_count in requested:
        if count >= feature_count:
            rows.append(
                (str(feature_count), feature_count, rng.choice(count, feature_count, replace=False))
            )
        else:
            rows.append((str(feature_count), feature_count, np.array([], dtype=int)))
    return rows


def _evaluate_search(
    observations: tuple[Observation, ...],
    observed_ids: tuple[int, ...],
    ais: tuple[AISRecord, ...],
    truth_ship: ShipPose,
    bounds: tuple[float, float, float, float],
    visibility_m: float,
    sigma_deg: float,
    grid_spacing_m: float,
    refine_starts: int,
    type_labels: bool,
    refine: bool,
    complete_report: bool,
    position_threshold_m: float,
) -> tuple[SearchResult, dict[str, object]]:
    reported = [
        (observation.object_type if type_labels else None, observation.bearing_deg)
        for observation in observations
    ]
    ais_xy = np.array([[record.east_m, record.north_m] for record in ais], dtype=float).reshape(
        -1, 2
    )
    ais_types: list[str | None] = [record.object_type for record in ais]
    started = perf_counter()
    result = grid_search(
        reported,
        ais_xy,
        ais_types,
        bounds,
        sigma_deg=sigma_deg,
        grid_spacing_m=grid_spacing_m,
        refine_starts=refine_starts,
        use_types=type_labels,
        refine=refine,
        visibility_radius_m=visibility_m,
        penalize_missing_ais=complete_report,
    )
    elapsed_s = max(perf_counter() - started, result.elapsed_s)
    position_error = float(
        np.hypot(result.east_m - truth_ship.east_m, result.north_m - truth_ship.north_m)
    )
    observed_ais_ids = {
        object_id for object_id in observed_ids if object_id in {a.ais_id for a in ais}
    }
    correct = sum(
        1
        for observation_index, ais_index in result.matches
        if observation_index < len(observed_ids)
        and ais_index < len(ais)
        and observed_ids[observation_index] == ais[ais_index].ais_id
    )
    predicted = len(result.matches)
    precision = correct / predicted if predicted else 0.0
    recall = correct / len(observed_ais_ids) if observed_ais_ids else 0.0
    success = position_error <= position_threshold_m and correct >= 3
    metrics: dict[str, object] = {
        "position_error_m": position_error,
        "correct_associations": correct,
        "predicted_associations": predicted,
        "association_precision": precision,
        "association_recall": recall,
        "success": success,
        "elapsed_ms": elapsed_s * 1_000.0,
        "pose_evaluations": result.evaluations,
        "normalized_score": result.score_sigma,
    }
    return result, metrics


def _summarize(
    rows: list[dict[str, object]], success_target: float
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    grouped: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[
            (
                row["visibility_m"],
                row["type_labels"],
                row["algorithm"],
                row["requested_features"],
            )
        ].append(row)
    summary: list[dict[str, object]] = []
    for key, group in sorted(grouped.items(), key=lambda item: str(item[0])):
        visible_trials = [row for row in group if row["status"] == "estimated"]
        successes = sum(bool(row["success"]) for row in group)
        times = [_numeric(row, "elapsed_ms") for row in visible_trials]
        position_errors = [_numeric(row, "position_error_m") for row in visible_trials]
        summary.append(
            {
                "visibility_m": key[0],
                "type_labels": key[1],
                "algorithm": key[2],
                "requested_features": key[3] if key[3] is not None else "all",
                "trials": len(group),
                "trials_with_enough_features": len(visible_trials),
                "feature_availability_rate": len(visible_trials) / len(group),
                "success_rate_all_trials": successes / len(group),
                "success_rate_when_estimated": (
                    successes / len(visible_trials) if visible_trials else 0.0
                ),
                "median_position_error_m": float(np.median(position_errors))
                if position_errors
                else "",
                "p95_position_error_m": float(np.percentile(position_errors, 95))
                if position_errors
                else "",
                "median_latency_ms": float(np.median(times)) if times else "",
                "p95_latency_ms": float(np.percentile(times, 95)) if times else "",
                "p99_latency_ms": float(np.percentile(times, 99)) if times else "",
                "under_100_ms_rate": float(np.mean(np.asarray(times) <= 100.0)) if times else 0.0,
                "median_correct_associations": float(
                    np.median([_numeric(row, "correct_associations") for row in visible_trials])
                )
                if visible_trials
                else "",
            }
        )

    by_mode: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
    for row in summary:
        if row["requested_features"] != "all":
            by_mode[(row["visibility_m"], row["type_labels"], row["algorithm"])].append(row)
    minimums: list[dict[str, object]] = []
    for (visibility, types, algorithm), group in sorted(
        by_mode.items(), key=lambda item: str(item[0])
    ):
        eligible = [
            row
            for row in group
            if _numeric(row, "feature_availability_rate") == 1.0
            and _numeric(row, "success_rate_all_trials") >= success_target
        ]
        minimums.append(
            {
                "visibility_m": visibility,
                "type_labels": types,
                "algorithm": algorithm,
                "target_success_rate": success_target,
                "minimum_total_features": min(
                    (int(str(row["requested_features"])) for row in eligible), default=""
                ),
            }
        )
    return summary, minimums


def run_benchmark(args: argparse.Namespace) -> Path:
    output = Path(args.output_dir).expanduser().resolve()
    seed_sequence = np.random.SeedSequence(args.seed)
    trial_seeds = seed_sequence.spawn(args.trials)
    raw: list[dict[str, object]] = []
    ais_counts: list[int] = []

    for trial, trial_seed in enumerate(trial_seeds):
        rng = np.random.default_rng(trial_seed)
        base_config = SceneConfig(
            width_m=args.scene_km * 1_000.0,
            height_m=args.scene_km * 1_000.0,
            object_count=args.object_count,
            ais_fraction=args.ais_fraction,
            visibility_radius_m=max(args.ranges_km) * 1_000.0,
            bearing_noise_sigma_deg=args.noise_deg,
        )
        base_scene = generate_scene(base_config, rng)
        ais_counts.append(len(base_scene.ais))
        ship = random_ship_pose(base_scene, rng, edge_margin_m=max(args.ranges_km) * 1_000.0)
        for range_km in sorted(args.ranges_km, reverse=True):
            radius_m = range_km * 1_000.0
            scene = replace(
                base_scene,
                config=replace(base_scene.config, visibility_radius_m=radius_m),
            )
            observations, truth = simulate_observations(scene, ship, rng)
            subsets = _feature_subsets(len(observations), args.features, rng)
            visible_ids = truth.visible_object_ids
            bounds = (
                -base_config.width_m / 2,
                base_config.width_m / 2,
                -base_config.height_m / 2,
                base_config.height_m / 2,
            )
            for name, requested, selected in subsets:
                if selected is None:
                    indices = np.arange(len(observations), dtype=int)
                    status = "estimated" if len(indices) >= 3 else "insufficient_features"
                elif len(selected) == 0:
                    indices = np.arange(len(observations), dtype=int)
                    status = "insufficient_features"
                else:
                    indices = np.asarray(selected, dtype=int)
                    status = "estimated"
                selected_observations = tuple(observations[i] for i in indices)
                selected_ids = tuple(visible_ids[i] for i in indices)
                true_ais_features = sum(
                    object_id in {record.ais_id for record in base_scene.ais}
                    for object_id in selected_ids
                )
                for type_labels in (True, False):
                    for algorithm, refine in (("grid", False), ("grid_refined", True)):
                        row: dict[str, object] = {
                            "trial": trial,
                            "visibility_m": radius_m,
                            "type_labels": type_labels,
                            "algorithm": algorithm,
                            "requested_features": name,
                            "status": status,
                            "observed_features": len(selected_observations),
                            "observed_ais_features": true_ais_features,
                            "position_error_m": "",
                            "correct_associations": 0,
                            "predicted_associations": 0,
                            "association_precision": 0.0,
                            "association_recall": 0.0,
                            "success": False,
                            "elapsed_ms": 0.0,
                            "pose_evaluations": 0,
                            "normalized_score": "",
                        }
                        if status == "estimated":
                            _, metrics = _evaluate_search(
                                selected_observations,
                                selected_ids,
                                base_scene.ais,
                                truth.ship,
                                bounds,
                                radius_m,
                                args.noise_deg,
                                args.grid_km * 1_000.0,
                                args.refine_starts,
                                type_labels,
                                refine,
                                requested is None,
                                args.success_position_m,
                            )
                            row.update(metrics)
                        raw.append(row)

    raw_path = output / "trials.csv"
    _write_csv(raw_path, RAW_FIELDS, raw)
    summary, minimums = _summarize(raw, args.target_success_rate)
    _write_csv(output / "summary.csv", list(summary[0]) if summary else [], summary)
    _write_csv(output / "minimum_features.csv", list(minimums[0]) if minimums else [], minimums)

    # Profile the parallel angular-residual kernel separately from full assignment.
    profile_rng = np.random.default_rng(args.seed + 1)
    candidate_count = args.backend_candidates
    candidate_xy = np.column_stack(
        (
            profile_rng.uniform(-base_config.width_m / 2, base_config.width_m / 2, candidate_count),
            profile_rng.uniform(
                -base_config.height_m / 2, base_config.height_m / 2, candidate_count
            ),
        )
    )
    profile_scene = generate_scene(base_config, profile_rng)
    profile_ship = random_ship_pose(profile_scene, profile_rng, max(args.ranges_km) * 1_000.0)
    profile_range_scene = replace(
        profile_scene,
        config=replace(profile_scene.config, visibility_radius_m=max(args.ranges_km) * 1_000.0),
    )
    profile_observations, _ = simulate_observations(profile_range_scene, profile_ship, profile_rng)
    backend_rows: list[dict[str, object]] = [
        asdict(record)
        for record in benchmark_backends(
            candidate_xy,
            np.array([observation.bearing_deg for observation in profile_observations]),
            np.array([[record.east_m, record.north_m] for record in profile_scene.ais]).reshape(
                -1, 2
            ),
            repeats=args.backend_repeats,
        )
    ]
    _write_csv(output / "backend_profile.csv", list(backend_rows[0]), backend_rows)

    print(f"Trials written to {raw_path}")
    print(f"Summary: {output / 'summary.csv'}")
    print(f"Minimum feature counts: {output / 'minimum_features.csv'}")
    print(f"Backend profile: {output / 'backend_profile.csv'}")
    print(
        "Scene AIS catalogue size per trial: "
        f"median {float(np.median(ais_counts)):.0f} objects; profile kernel excludes assignment."
    )
    print("A 100 ms latency is the unadjusted 10 FPS frame budget.")
    return output


def _csv_float_list(value: str) -> list[float]:
    try:
        result = [float(item) for item in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected comma-separated numbers.") from exc
    if not result or any(item <= 0 for item in result):
        raise argparse.ArgumentTypeError("Values must be positive.")
    return result


def _csv_int_list(value: str) -> list[int]:
    try:
        result = [int(item) for item in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected comma-separated integers.") from exc
    if not result or any(item < 1 for item in result):
        raise argparse.ArgumentTypeError("Feature counts must be positive integers.")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20261023)
    parser.add_argument("--ranges-km", type=_csv_float_list, default=[5.0, 3.0, 1.0])
    parser.add_argument("--features", type=_csv_int_list, default=[3, 4, 6, 8, 12, 20])
    parser.add_argument("--scene-km", type=float, default=20.0)
    parser.add_argument("--object-count", type=int, default=200)
    parser.add_argument("--ais-fraction", type=float, default=0.7)
    parser.add_argument("--noise-deg", type=float, default=0.5)
    parser.add_argument("--grid-km", type=float, default=1.0)
    parser.add_argument("--refine-starts", type=int, default=16)
    parser.add_argument("--success-position-m", type=float, default=250.0)
    parser.add_argument("--target-success-rate", type=float, default=0.95)
    parser.add_argument("--backend-candidates", type=int, default=512)
    parser.add_argument("--backend-repeats", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.trials < 1 or args.object_count < 0:
        parser.error("trials must be positive and object-count nonnegative")
    if not 0.0 <= args.ais_fraction <= 1.0:
        parser.error("ais-fraction must be in [0, 1]")
    if args.scene_km <= 2.0 * max(args.ranges_km):
        parser.error("scene-km must exceed twice the largest visibility range in km")
    if args.noise_deg <= 0 or args.grid_km <= 0:
        parser.error("noise and grid spacing must be positive")
    if args.refine_starts < 1:
        parser.error("refine-starts must be positive")
    if not 0 < args.target_success_rate <= 1:
        parser.error("target-success-rate must be in (0, 1]")
    if args.backend_candidates < 1 or args.backend_repeats < 1:
        parser.error("backend-candidates and backend-repeats must be positive")
    run_benchmark(args)


if __name__ == "__main__":
    main()
