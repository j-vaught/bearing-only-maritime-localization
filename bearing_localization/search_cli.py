"""Standalone search program for measurement and AIS contract files.

Search receives estimator-visible measurements, an AIS snapshot, an optional
water/land map, and search configuration. It deliberately has no truth input.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

import numpy as np

from bearing_localization.search import HierarchicalSearchConfig, run_hierarchical_search
from bearing_localization.search.grid import GridSearchConfig, run_grid_search
from bearing_localization.search.randomized import RandomizedSearchConfig, randomized_search
from maritime_localization_shared.contracts import AISSnapshot, MapInput, MeasurementSet
from maritime_localization_shared.contracts.estimate import PositionEstimate
from maritime_localization_shared.geospatial.raster_los import raster_line_of_sight
from maritime_localization_shared.serialization import read_contract, write_contract


class _RasterVisibility:
    """Adapt the shared map contract to the grid-search visibility protocol."""

    def __init__(self, map_input: MapInput) -> None:
        self.map_input = map_input

    def is_water(self, east_m: float, north_m: float) -> bool:
        col = int(np.floor((east_m - self.map_input.west_m) / self.map_input.cell_size_m))
        row = int(np.floor((north_m - self.map_input.south_m) / self.map_input.cell_size_m))
        return (
            0 <= row < self.map_input.height_cells
            and 0 <= col < self.map_input.width_cells
            and self.map_input.water_mask[row][col]
        )

    def has_line_of_sight(
        self,
        observer_east_m: float,
        observer_north_m: float,
        target_east_m: float,
        target_north_m: float,
    ) -> bool:
        return raster_line_of_sight(
            self.map_input,
            (observer_east_m, observer_north_m),
            (target_east_m, target_north_m),
        )


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


def run_search_files(
    measurements_path: str | Path,
    ais_path: str | Path,
    bounds_xy: tuple[float, float, float, float],
    *,
    algorithm: str = "grid",
    map_path: str | Path | None = None,
    grid_spacing_m: float = 250.0,
    visibility_radius_m: float = float("inf"),
    use_types: bool = True,
    refine: bool = True,
    refine_starts: int = 16,
    iterations: int = 500,
    seed: int = 0,
) -> PositionEstimate:
    """Load search inputs, estimate position, and return an estimate contract.

    This function never loads or accepts an evaluator truth document.
    """
    measurements = read_contract(measurements_path, MeasurementSet)
    ais = read_contract(ais_path, AISSnapshot)
    map_input = read_contract(map_path, MapInput) if map_path is not None else None
    if map_input is not None and map_input.crs != ais.crs:
        raise ValueError("Map and AIS inputs must use the same projected CRS.")

    if algorithm == "grid":
        config = GridSearchConfig(
            grid_spacing_m=grid_spacing_m,
            visibility_radius_m=visibility_radius_m,
            use_types=use_types,
            refine=refine,
            refine_starts=refine_starts,
        )
        return run_grid_search(
            measurements,
            ais,
            bounds_xy,
            config,
            visibility=_RasterVisibility(map_input) if map_input is not None else None,
        )
    if algorithm == "randomized":
        return randomized_search(
            measurements,
            ais,
            RandomizedSearchConfig(
                bounds_xy=bounds_xy,
                visibility_radius_m=visibility_radius_m,
                use_types=use_types,
                iterations=iterations,
                seed=seed,
                visibility=_RasterVisibility(map_input) if map_input is not None else None,
            ),
        )
    if algorithm == "hierarchical":
        return run_hierarchical_search(
            measurements,
            ais,
            bounds_xy,
            HierarchicalSearchConfig(
                coarse_spacing_m=grid_spacing_m,
                visibility_radius_m=visibility_radius_m,
                use_types=use_types,
            ),
            visibility=_RasterVisibility(map_input) if map_input is not None else None,
        )
    raise ValueError(f"Unsupported search algorithm {algorithm!r}.")


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(description=__doc__)
    parser.add_argument("--measurements", type=Path, required=True)
    parser.add_argument("--ais", type=Path, required=True)
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
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _parse_bounds(value: str) -> tuple[float, float, float, float]:
    try:
        values = tuple(float(item) for item in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Bounds must be four comma-separated numbers.") from exc
    if len(values) != 4 or not all(np.isfinite(values)):
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
    if args.visibility_radius_m <= 0 or np.isnan(args.visibility_radius_m):
        parser.error("visibility radius must be positive")
    inputs = [args.measurements, args.ais]
    if args.map_path is not None:
        inputs.append(args.map_path)
    if args.output.expanduser().resolve() in {path.expanduser().resolve() for path in inputs}:
        parser.error("search output must be separate from measurement, AIS, and map inputs")
    try:
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
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_contract(args.output, estimate)
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(asdict(estimate), sort_keys=True))
    return 0


def _entrypoint() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    _entrypoint()
