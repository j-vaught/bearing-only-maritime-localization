"""Composition root for file-driven localization programs.

The root launches search and evaluation as separate processes. Search receives
measurement, catalog, map, and config files; evaluator-only truth enters later.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from math import isfinite
from pathlib import Path


class _ArgumentParser(argparse.ArgumentParser):
    """Accept comma-delimited bounds whose first value is negative."""

    def _parse_optional(self, arg_string: str):
        if arg_string.startswith("-") and "," in arg_string:
            try:
                float(arg_string.split(",", maxsplit=1)[0])
            except ValueError:
                pass
            else:
                return None
        return super()._parse_optional(arg_string)


def _parse_bounds(value: str) -> tuple[float, float, float, float]:
    try:
        values = tuple(float(item) for item in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Bounds must be four comma-separated numbers.") from exc
    if len(values) != 4 or not all(isfinite(item) for item in values):
        raise argparse.ArgumentTypeError("Bounds must be four finite values.")
    xmin, xmax, ymin, ymax = values
    if xmin >= xmax or ymin >= ymax:
        raise argparse.ArgumentTypeError("Bounds must have positive width and height.")
    return values


def _distinct_inputs_outputs(inputs: Sequence[Path], outputs: Sequence[Path]) -> tuple[Path, ...]:
    resolved_inputs = tuple(path.expanduser().resolve() for path in inputs)
    resolved_outputs = tuple(path.expanduser().resolve() for path in outputs)
    if len(set(resolved_inputs + resolved_outputs)) != len(resolved_inputs) + len(resolved_outputs):
        raise ValueError("Pipeline inputs and outputs must use distinct files.")
    return resolved_outputs


def run_pipeline(args: argparse.Namespace) -> dict[str, object]:
    """Run the v1 search and evaluator as separate file-driven processes."""
    inputs = [args.measurements, args.ais, args.truth]
    if args.map_path is not None:
        inputs.append(args.map_path)
    estimate_path, metrics_path = _distinct_inputs_outputs(
        inputs, [args.estimate_output, args.metrics_output]
    )
    search_command = [
        sys.executable,
        "-m",
        "bearing_localization.search_cli",
        "--measurements",
        str(args.measurements),
        "--ais",
        str(args.ais),
        "--bounds",
        ",".join(str(value) for value in args.bounds),
        "--algorithm",
        args.algorithm,
        "--grid-spacing-m",
        str(args.grid_spacing_m),
        "--visibility-radius-m",
        str(args.visibility_radius_m),
        "--refine-starts",
        str(args.refine_starts),
        "--iterations",
        str(args.iterations),
        "--seed",
        str(args.seed),
        "--output",
        str(estimate_path),
    ]
    if args.map_path is not None:
        search_command.extend(("--map", str(args.map_path)))
    if args.no_types:
        search_command.append("--no-types")
    if args.no_refine:
        search_command.append("--no-refine")
    estimate_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(search_command, check=True, capture_output=True, text=True)

    evaluate_command = [
        sys.executable,
        "-m",
        "bearing_localization.evaluate_cli",
        "--estimate",
        str(estimate_path),
        "--truth",
        str(args.truth),
        "--output",
        str(metrics_path),
        "--success-position-threshold-m",
        str(args.success_position_threshold_m),
        "--success-min-correct-associations",
        str(args.success_min_correct_associations),
    ]
    subprocess.run(evaluate_command, check=True, capture_output=True, text=True)
    return json.loads(metrics_path.read_text(encoding="utf-8"))


def run_pipeline_v2(args: argparse.Namespace) -> dict[str, object]:
    """Run truth-free v2 search before a separate evaluator process."""
    inputs = [args.measurements, args.catalog, args.map_path, args.config, args.truth, args.support]
    if args.calibration is not None:
        inputs.append(args.calibration)
    estimate_path, metrics_path = _distinct_inputs_outputs(
        inputs, [args.estimate_output, args.metrics_output]
    )
    search_command = [
        sys.executable,
        "-m",
        "bearing_localization.search_cli",
        "--schema-version",
        "2",
        "--measurements",
        str(args.measurements),
        "--catalog",
        str(args.catalog),
        "--map",
        str(args.map_path),
        "--config",
        str(args.config),
        "--projected-crs",
        args.projected_crs,
        "--output",
        str(estimate_path),
        "--spacing-m",
        str(args.spacing_m),
        "--seed",
        str(args.seed),
        "--seed-policy",
        getattr(args, "seed_policy", "fixed"),
    ]
    if args.calibration is not None:
        search_command.extend(("--calibration", str(args.calibration)))
    if args.device_id is not None:
        for device_id in args.device_id:
            search_command.extend(("--device-id", str(device_id)))
    if args.algorithm is not None:
        search_command.extend(("--algorithm", args.algorithm))
    if args.no_refine:
        search_command.append("--no-refine")
    if args.scope_manifest is not None:
        search_command.extend(("--scope-manifest", str(args.scope_manifest)))
    if args.max_grid_bearing_error_deg is not None:
        search_command.extend(
            ("--max-grid-bearing-error-deg", str(args.max_grid_bearing_error_deg))
        )
    estimate_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    # The search child receives estimator inputs only. Truth and support are added later.
    subprocess.run(search_command, check=True, capture_output=True, text=True)
    evaluate_command = [
        sys.executable,
        "-m",
        "bearing_localization.evaluate_cli",
        "--schema-version",
        "2",
        "--estimate",
        str(estimate_path),
        "--truth",
        str(args.truth),
        "--support",
        str(args.support),
        "--output",
        str(metrics_path),
    ]
    subprocess.run(evaluate_command, check=True, capture_output=True, text=True)
    return json.loads(metrics_path.read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(description=__doc__)
    parser.add_argument("--measurements", type=Path, required=True)
    parser.add_argument("--ais", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--map", dest="map_path", type=Path)
    parser.add_argument(
        "--bounds", type=_parse_bounds, required=True, metavar="XMIN,XMAX,YMIN,YMAX"
    )
    parser.add_argument(
        "--algorithm", choices=("grid", "randomized", "hierarchical"), default="grid"
    )
    parser.add_argument("--grid-spacing-m", type=float, default=250.0)
    parser.add_argument("--visibility-radius-m", type=float, default=float("inf"))
    parser.add_argument("--no-types", action="store_true")
    parser.add_argument("--no-refine", action="store_true")
    parser.add_argument("--refine-starts", type=int, default=16)
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--estimate-output", type=Path, default=Path("results/estimate.json"))
    parser.add_argument("--metrics-output", type=Path, default=Path("results/evaluation.json"))
    parser.add_argument("--success-position-threshold-m", type=float, default=250.0)
    parser.add_argument("--success-min-correct-associations", type=int, default=3)
    return parser


def _v2_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compose v2 search and evaluation programs.")
    parser.add_argument("--measurements", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--map", dest="map_path", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--projected-crs", required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--support", type=Path, required=True)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--scope-manifest", type=Path)
    parser.add_argument("--max-grid-bearing-error-deg", type=float)
    parser.add_argument(
        "--algorithm",
        choices=(
            "grid",
            "hierarchical",
            "randomized",
            "pairwise",
            "reference_grid",
            "branch_bound",
            "wedge",
        ),
    )
    parser.add_argument("--spacing-m", type=float, default=250.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--seed-policy",
        choices=("fixed", "per_trial_variable"),
        default="fixed",
    )
    parser.add_argument(
        "--device-id",
        type=int,
        action="append",
        default=None,
        help="GPU device index. Repeat to use multiple GPUs; required for GPU backend.",
    )
    parser.add_argument("--no-refine", action="store_true")
    parser.add_argument(
        "--estimate-output", type=Path, default=Path("results/localization-v2.json")
    )
    parser.add_argument("--metrics-output", type=Path, default=Path("results/evaluation-v2.json"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--schema-version" in arguments:
        marker = arguments.index("--schema-version")
        if marker + 1 >= len(arguments) or arguments[marker + 1] != "2":
            raise SystemExit("--schema-version must be 2 when specified.")
        parser = _v2_parser()
        args = parser.parse_args(arguments[:marker] + arguments[marker + 2 :])
        if args.spacing_m <= 0:
            parser.error("spacing-m must be positive")
        if args.device_id is not None and (
            any(device_id < 0 for device_id in args.device_id)
            or len(set(args.device_id)) != len(args.device_id)
        ):
            parser.error("device IDs must be distinct nonnegative integers")
        try:
            metrics = run_pipeline_v2(args)
        except (OSError, TypeError, ValueError, subprocess.CalledProcessError) as exc:
            parser.error(str(exc))
        print(json.dumps(metrics, sort_keys=True))
        return 0

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.grid_spacing_m <= 0 or args.refine_starts < 1 or args.iterations < 1:
        parser.error("grid spacing, refine starts, and iterations must be positive")
    if args.visibility_radius_m <= 0:
        parser.error("visibility radius must be positive")
    try:
        metrics = run_pipeline(args)
    except (OSError, TypeError, ValueError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    print(json.dumps(metrics, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
