from __future__ import annotations

import importlib.util
from pathlib import Path

from pyproj import Transformer

MODULE_PATH = Path(__file__).resolve().parents[2] / "tools" / "diagnose_a35_frozen_objective.py"
SPEC = importlib.util.spec_from_file_location("diagnose_a35_frozen_objective", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
_geodesic_error_m = MODULE._geodesic_error_m
_spatial_modes = MODULE._spatial_modes


def test_geodesic_error_transforms_truth_and_candidate_from_distinct_utm_zones() -> None:
    lon, lat = -68.88951866523595, 44.455393145950055
    truth_xy = Transformer.from_crs("EPSG:4326", "EPSG:32619", always_xy=True).transform(lon, lat)
    candidate_xy = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform(
        lon, lat
    )

    error = _geodesic_error_m(
        candidate_xy,
        truth_xy,
        candidate_crs="EPSG:32618",
        truth_crs="EPSG:32619",
    )

    assert error < 1e-6


def test_spatial_modes_cluster_only_hypotheses_within_geodesic_radius() -> None:
    hypotheses = [
        {"east_m": 500_000.0, "north_m": 4_900_000.0},
        {"east_m": 500_010.0, "north_m": 4_900_000.0},
        {"east_m": 501_000.0, "north_m": 4_900_000.0},
    ]

    assert _spatial_modes(hypotheses, radius_m=100.0) == [[0, 1], [2]]
