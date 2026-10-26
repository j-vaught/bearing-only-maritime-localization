"""Tests for version 2 input contract validation and truth isolation."""

from __future__ import annotations

from typing import Any

import pytest

from maritime_localization_shared.contracts.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MapV2,
    MeasurementSetV2,
    MeasurementV2,
)


def _measurement() -> MeasurementV2:
    return MeasurementV2(42.0, "2026-10-25T12:00:00Z", sigma_deg=0.5)


def test_valid_v2_contracts() -> None:
    measurement = _measurement()
    measurements = MeasurementSetV2((measurement,), covariance_deg2=((0.25,),))
    record = CatalogRecordV2(
        "feature-1",
        32.78,
        -79.93,
        "AIS buoy",
        "buoy",
        reference_latitude_deg=32.7,
        reference_longitude_deg=-79.8,
    )
    catalog = CatalogV2((record,), "2026-10-25T12:00:00Z", "test", "1", "catalog-a")
    geometry = '{"type":"Polygon","coordinates":[]}'
    map_input = MapV2(
        "EPSG:4326",
        "test",
        "1",
        coverage_status="partial",
        coverage_geometry_geojson=geometry,
        unknown_geometry_geojson=geometry,
        land_geometry_geojson=geometry,
    )

    assert measurements.schema_version == catalog.schema_version == map_input.schema_version == 2
    assert measurement.bearing_deg == 42.0
    assert catalog.records[0].key == "feature-1"
    assert map_input.coverage_status == "partial"


def test_optional_fields_can_be_missing() -> None:
    assert MeasurementSetV2((_measurement(),)).covariance_deg2 is None
    assert CatalogRecordV2("feature-1", 0.0, 0.0).normalized_type is None
    assert MapV2("EPSG:4326", "test", "1").coverage_status == "unknown"


def test_invalid_covariance_is_rejected() -> None:
    with pytest.raises(ValueError, match="positive semidefinite"):
        MeasurementSetV2(
            (_measurement(), MeasurementV2(90.0, "2026-10-25T12:00:00Z")),
            covariance_deg2=((1.0, 2.0), (2.0, 1.0)),
        )
    with pytest.raises(ValueError, match="square matrix"):
        MeasurementSetV2((_measurement(),), covariance_deg2=((1.0, 0.0), (0.0, 1.0)))


def test_measurement_constructor_rejects_truth_fields() -> None:
    forbidden = (
        ("observer_latitude_deg", 32.0),
        ("observer_longitude_deg", -79.0),
        ("object_id", "target-1"),
        ("range_m", 1000.0),
        ("target_latitude_deg", 32.1),
    )
    for field, value in forbidden:
        kwargs: Any = {"bearing_deg": 42.0, "timestamp_utc": "2026-10-25T12:00:00Z", field: value}
        with pytest.raises(TypeError):
            MeasurementV2(**kwargs)


def test_legacy_contract_construction_is_unchanged() -> None:
    from maritime_localization_shared.contracts.observation import MeasurementSet, Observation

    legacy = MeasurementSet((Observation(42.0, 0.5),))
    assert legacy.schema_version == 1
    assert legacy.observations[0].bearing_deg == 42.0


def test_catalog_keys_and_map_coverage_are_validated() -> None:
    record = CatalogRecordV2("duplicate", 0.0, 0.0)
    with pytest.raises(ValueError, match="unique"):
        CatalogV2((record, record), "2026-10-25T12:00:00Z", "test", "1", "catalog-a")
    with pytest.raises(ValueError, match="Known coverage"):
        MapV2("EPSG:4326", "test", "1", coverage_status="known")
    with pytest.raises(ValueError, match="Known coverage"):
        MapV2(
            "EPSG:4326",
            "test",
            "1",
            coverage_status="known",
            land_geometry_geojson='{"type":"Polygon","coordinates":[]}',
        )
