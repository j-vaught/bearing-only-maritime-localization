"""Tests for projection direction diagnostics used by the A04 audit."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from maritime_localization_shared.geospatial.coordinates import LocalFrame
from tools.audit_a04_wedge_projection import audit_projection  # noqa: E402


def test_audit_reports_local_north_and_finite_ray_errors_separately() -> None:
    frame = LocalFrame((-75.0, 38.0))
    catalog = [
        {"key": "near", "longitude_deg": -75.0, "latitude_deg": 38.0},
        {"key": "far", "longitude_deg": -80.0, "latitude_deg": 32.0},
    ]
    result = audit_projection(
        frame=frame,
        catalog_rows=catalog,
        measured_bearings_deg=[20.0, 210.0],
        ray_distance_m=10_000.0,
        claimed_bound_deg=0.5,
    )

    assert result["catalog_count"] == 2
    assert result["measurement_count"] == 2
    assert result["source_grid_north"]["count"] == 2
    assert result["reverse_ray_endpoints"]["count"] == 4
    assert result["reverse_ray_endpoints"]["max_abs_deg"] > 0.5
    assert result["reverse_ray_endpoints"]["bound_violations"] > 0
    assert result["reverse_ray_endpoints"]["max_abs_event"]["source_key"] == "far"
    assert result["source_grid_north"]["p95_event_nearest_rank"]["source_key"] in {"near", "far"}


def test_ray_error_decreases_for_source_at_its_frame_origin() -> None:
    frame = LocalFrame((-75.0, 38.0))
    result = audit_projection(
        frame=frame,
        catalog_rows=[{"key": "origin", "longitude_deg": -75.0, "latitude_deg": 38.0}],
        measured_bearings_deg=[45.0],
        ray_distance_m=10_000.0,
    )

    assert result["source_grid_north"]["max_abs_deg"] == pytest.approx(0.0, abs=1e-10)
    assert result["reverse_ray_endpoints"]["max_abs_deg"] < 0.1


def test_audit_rejects_empty_and_invalid_ray_inputs() -> None:
    frame = LocalFrame((0.0, 0.0))
    with pytest.raises(ValueError, match="catalog_rows"):
        audit_projection(frame=frame, catalog_rows=[], measured_bearings_deg=[1.0])
    with pytest.raises(ValueError, match="ray_distance_m"):
        audit_projection(
            frame=frame,
            catalog_rows=[{"key": "x", "longitude_deg": 0.0, "latitude_deg": 0.0}],
            measured_bearings_deg=[1.0],
            ray_distance_m=0.0,
        )
