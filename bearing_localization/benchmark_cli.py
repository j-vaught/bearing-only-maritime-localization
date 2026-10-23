"""Run reproducible synthetic search and visibility/type ablation benchmarks."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from bearing_localization.evaluation.benchmark import (
    BenchmarkCase,
    BenchmarkConfig,
    SearchMethod,
    run_benchmark,
    write_benchmark,
)
from bearing_localization.scene import (
    Scene,
    SceneConfig,
    ShipPose,
    generate_scene,
    random_ship_pose,
)
from bearing_localization.search import (
    HierarchicalSearchConfig,
    run_hierarchical_search,
)
from bearing_localization.search.grid import GridSearchConfig, run_grid_search
from bearing_localization.search.randomized import RandomizedSearchConfig, randomized_search
from bearing_localization.simulator.runner import run_simulator


@dataclass(frozen=True, slots=True)
class _PreparedTrial:
    scene: Scene
    observer: ShipPose


def _positive_float(value: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Value must be a finite positive number.") from exc
    if not np.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("Value must be a finite positive number.")
    return result


def _probability(value: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Value must be between 0 and 1.") from exc
    if not np.isfinite(result) or not 0.0 <= result <= 1.0:
        raise argparse.ArgumentTypeError("Value must be between 0 and 1.")
    return result


def _positive_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Value must be a positive integer.") from exc
    if result < 1:
        raise argparse.ArgumentTypeError("Value must be a positive integer.")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=_positive_int, default=3)
    parser.add_argument("--seed", type=int, default=20261023)
    parser.add_argument("--scene-km", type=_positive_float, default=20.0)
    parser.add_argument("--object-count", type=int, default=100)
    parser.add_argument("--ais-fraction", type=_probability, default=0.7)
    parser.add_argument(
        "--visibility-ranges-km", type=_positive_float, nargs="+", default=(1, 5, 10)
    )
    parser.add_argument("--noise-deg", type=float, default=0.5)
    parser.add_argument("--angular-resolution-deg", type=float, default=0.1)
    parser.add_argument("--grid-spacing-m", type=_positive_float, default=1_000.0)
    parser.add_argument("--iterations", type=_positive_int, default=500)
    parser.add_argument(
        "--algorithms",
        nargs="+",
        choices=("grid", "randomized", "hierarchical"),
        default=("grid", "randomized", "hierarchical"),
    )
    parser.add_argument(
        "--no-refine", action="store_true", help="Disable local refinement in grid search."
    )
    parser.add_argument(
        "--single-type-mode",
        action="store_true",
        help="Run labeled observations only; default compares labeled and unlabeled modes.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("results/benchmark"))
    return parser


def _search_methods(
    names: Sequence[str],
    *,
    bounds_xy: tuple[float, float, float, float],
    grid_spacing_m: float,
    iterations: int,
    refine: bool,
) -> dict[str, SearchMethod]:
    methods: dict[str, SearchMethod] = {}
    for name in names:
        if name == "grid":
            methods[name] = lambda measurements, ais, request: run_grid_search(
                measurements,
                ais,
                bounds_xy,
                GridSearchConfig(
                    grid_spacing_m=grid_spacing_m,
                    visibility_radius_m=request.visibility_radius_m,
                    use_types=request.use_types,
                    refine=refine,
                ),
            )
        elif name == "randomized":
            methods[name] = lambda measurements, ais, request: randomized_search(
                measurements,
                ais,
                RandomizedSearchConfig(
                    bounds_xy=bounds_xy,
                    visibility_radius_m=request.visibility_radius_m,
                    use_types=request.use_types,
                    iterations=iterations,
                    seed=request.seed,
                ),
            )
        elif name == "hierarchical":
            methods[name] = lambda measurements, ais, request: run_hierarchical_search(
                measurements,
                ais,
                bounds_xy,
                HierarchicalSearchConfig(
                    coarse_spacing_m=grid_spacing_m,
                    visibility_radius_m=request.visibility_radius_m,
                    use_types=request.use_types,
                ),
            )
    return methods


def run_cli(args: argparse.Namespace) -> tuple[Path, Path]:
    """Run synthetic trials through separated simulator, search, and evaluator APIs."""
    if args.object_count < 0:
        raise ValueError("object-count must be nonnegative.")
    if args.noise_deg < 0 or not np.isfinite(args.noise_deg):
        raise ValueError("noise-deg must be finite and nonnegative.")
    if (
        args.angular_resolution_deg < 0
        or not np.isfinite(args.angular_resolution_deg)
        or args.angular_resolution_deg >= 180
    ):
        raise ValueError("angular-resolution-deg must be finite and in [0, 180).")
    if not args.algorithms or len(set(args.algorithms)) != len(args.algorithms):
        raise ValueError("algorithms must be nonempty and unique.")
    if not args.visibility_ranges_km:
        raise ValueError("At least one visibility range is required.")

    width_m = args.scene_km * 1_000.0
    bounds_xy = (-width_m / 2, width_m / 2, -width_m / 2, width_m / 2)

    def prepare_trial(seed: int) -> _PreparedTrial:
        rng = np.random.default_rng(seed)
        scene = generate_scene(
            SceneConfig(
                width_m=width_m,
                height_m=width_m,
                object_count=args.object_count,
                ais_fraction=args.ais_fraction,
                visibility_radius_m=max(args.visibility_ranges_km) * 1_000.0,
                bearing_noise_sigma_deg=args.noise_deg,
            ),
            rng,
        )
        return _PreparedTrial(scene, random_ship_pose(scene, rng))

    def simulate_trial(
        prepared: _PreparedTrial,
        visibility_radius_m: float,
        use_types: bool,
        seed: int,
    ) -> BenchmarkCase:
        scene = replace(
            prepared.scene,
            config=replace(
                prepared.scene.config,
                visibility_radius_m=visibility_radius_m,
                report_type_labels=use_types,
            ),
        )
        run = run_simulator(
            scene,
            prepared.observer,
            seed=seed,
            trial_id=f"seed-{seed}",
            angular_resolution_deg=args.angular_resolution_deg,
        )
        return BenchmarkCase(run.measurements, run.ais_snapshot, run.truth)

    ranges_m = tuple(value * 1_000.0 for value in args.visibility_ranges_km)
    result = run_benchmark(
        BenchmarkConfig(
            trials=args.trials,
            seed=args.seed,
            visibility_ranges_m=ranges_m,
            type_labels=(True,) if args.single_type_mode else (True, False),
        ),
        prepare_trial=prepare_trial,
        simulate_trial=simulate_trial,
        search_methods=_search_methods(
            args.algorithms,
            bounds_xy=bounds_xy,
            grid_spacing_m=args.grid_spacing_m,
            iterations=args.iterations,
            refine=not args.no_refine,
        ),
    )
    return write_benchmark(result, args.output_dir)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        trial_path, summary_path = run_cli(args)
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Trial results: {trial_path}")
    print(f"Summary results: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
