"""Generate one reproducible synthetic bearing-only sensor report."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from maritime.scene import SceneConfig, ShipPose, generate_scene, random_ship_pose
from maritime.schemas import MapInput
from maritime.serialization import read_contract
from maritime.simulator.observer import sample_observers

from .runner import run_simulator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20261023)
    parser.add_argument("--trial-id", default=None)
    parser.add_argument("--scene-km", type=float, default=20.0)
    parser.add_argument("--object-count", type=int, default=200)
    parser.add_argument("--ais-fraction", type=float, default=0.7)
    parser.add_argument("--range-km", type=float, default=5.0)
    parser.add_argument("--noise-deg", type=float, default=0.5)
    parser.add_argument("--angular-resolution-deg", type=float, default=0.0)
    parser.add_argument(
        "--report-types",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include normalized object type labels in measurements (default: enabled).",
    )
    parser.add_argument("--observer-east-m", type=float)
    parser.add_argument("--observer-north-m", type=float)
    parser.add_argument("--observer-heading-deg", type=float, default=0.0)
    parser.add_argument("--map-input", type=Path, help="Optional shared MapInput JSON contract.")
    parser.add_argument("--measurements-out", type=Path, default=Path("results/measurements.json"))
    parser.add_argument("--truth-out", type=Path, default=Path("results/truth.json"))
    parser.add_argument("--ais-out", type=Path, default=Path("results/ais.json"))
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--schema-version" in arguments:
        marker = arguments.index("--schema-version")
        if marker + 1 >= len(arguments) or arguments[marker + 1] != "2":
            raise SystemExit("--schema-version must be 2 when specified.")
        v2_arguments = arguments[:marker] + arguments[marker + 2 :]
        from maritime.accuracy_cli import simulator_v2_main

        return simulator_v2_main(v2_arguments)
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.scene_km <= 0 or args.range_km <= 0:
        parser.error("scene-km and range-km must be positive")
    if args.object_count < 0:
        parser.error("object-count must be nonnegative")
    if not 0.0 <= args.ais_fraction <= 1.0:
        parser.error("ais-fraction must be in [0, 1]")
    if args.noise_deg < 0:
        parser.error("noise-deg must be nonnegative")
    if not 0.0 <= args.angular_resolution_deg < 180.0:
        parser.error("angular-resolution-deg must be in [0, 180)")
    if (args.observer_east_m is None) != (args.observer_north_m is None):
        parser.error("observer-east-m and observer-north-m must be supplied together")
    output_paths = [args.measurements_out, args.truth_out, args.ais_out]
    resolved = [path.expanduser().resolve() for path in output_paths]
    if len(set(resolved)) != len(resolved):
        parser.error("measurements-out, truth-out, and ais-out must be distinct files")

    seed_rng = np.random.default_rng(args.seed)
    scene_config = SceneConfig(
        width_m=args.scene_km * 1_000.0,
        height_m=args.scene_km * 1_000.0,
        object_count=args.object_count,
        ais_fraction=args.ais_fraction,
        visibility_radius_m=args.range_km * 1_000.0,
        bearing_noise_sigma_deg=args.noise_deg,
        report_type_labels=args.report_types,
    )
    scene = generate_scene(scene_config, seed_rng)
    map_input = read_contract(args.map_input, MapInput) if args.map_input is not None else None

    if args.observer_east_m is None:
        if map_input is None:
            observer = random_ship_pose(scene, seed_rng)
        else:
            position = sample_observers(map_input, 1, seed_rng)[0]
            observer = ShipPose(
                east_m=position.east_m,
                north_m=position.north_m,
                heading_deg=float(seed_rng.uniform(-180.0, 180.0)),
            )
    else:
        observer = ShipPose(
            east_m=args.observer_east_m,
            north_m=args.observer_north_m,
            heading_deg=args.observer_heading_deg,
        )

    for path in output_paths:
        path.expanduser().parent.mkdir(parents=True, exist_ok=True)
    run = run_simulator(
        scene,
        observer,
        seed=args.seed,
        trial_id=args.trial_id,
        angular_resolution_deg=args.angular_resolution_deg,
        map_input=map_input,
        measurements_path=args.measurements_out,
        truth_path=args.truth_out,
        ais_path=args.ais_out,
    )
    print(
        f"Measurements: {args.measurements_out} ({len(run.measurements.observations)} detections)"
    )
    print(f"AIS snapshot: {args.ais_out} ({len(run.ais_snapshot.records)} records)")
    print(f"Evaluation truth: {args.truth_out} ({len(run.truth.objects)} scene objects)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
