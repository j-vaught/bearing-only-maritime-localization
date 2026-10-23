"""Composition root for the file-driven localization programs.

The root invokes search using measurement, AIS, and optional map files only.
It invokes evaluation afterward with the estimate and separate truth file.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from math import isfinite
from pathlib import Path

from bearing_localization.evaluate_cli import evaluate_files
from bearing_localization.search_cli import run_search_files
from maritime_localization_shared.serialization import write_contract


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


def run_pipeline(args: argparse.Namespace) -> dict[str, object]:
    """Compose search then evaluation without exposing truth to search."""
    input_paths = [args.measurements, args.ais, args.truth]
    if args.map_path is not None:
        input_paths.append(args.map_path)
    input_paths = [path.expanduser().resolve() for path in input_paths]
    output_paths = [
        args.estimate_output.expanduser().resolve(),
        args.metrics_output.expanduser().resolve(),
    ]
    if len(set(input_paths + output_paths)) != len(input_paths) + len(output_paths):
        raise ValueError("Pipeline inputs and outputs must use distinct files.")
    estimate = run_search_files(
        args.measurements,
        args.ais,
        args.bounds,
        algorithm=args.algorithm,
        map_path=args.map_path,
        grid_spacing_m=args.grid_spacing_m,
        visibility_radius_m=args.visibility_radius_m,
        use_types=not args.no_types,
        refine=not args.no_refine,
        refine_starts=args.refine_starts,
        iterations=args.iterations,
        seed=args.seed,
    )
    args.estimate_output.parent.mkdir(parents=True, exist_ok=True)
    write_contract(args.estimate_output, estimate)
    metrics = evaluate_files(
        args.estimate_output,
        args.truth,
        success_position_threshold_m=args.success_position_threshold_m,
        success_min_correct_associations=args.success_min_correct_associations,
    )
    args.metrics_output.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_output.write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return metrics


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.grid_spacing_m <= 0 or args.refine_starts < 1 or args.iterations < 1:
        parser.error("grid spacing, refine starts, and iterations must be positive")
    if args.visibility_radius_m <= 0:
        parser.error("visibility radius must be positive")
    try:
        metrics = run_pipeline(args)
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(metrics, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
