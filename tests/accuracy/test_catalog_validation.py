"""A44 CatalogV2 loader validation regressions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maritime.schemas.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime.serialization import contract_from_dict, read_contract


def _record(key: str = "record-1") -> CatalogRecordV2:
    return CatalogRecordV2(
        key,
        34.0,
        -80.0,
        metadata=(("nested", {"finite": [1.0, None, True]}),),
        provenance=(("source", "fixture"),),
    )


def test_catalog_duplicate_keys_remain_rejected() -> None:
    record = _record()
    with pytest.raises(ValueError, match="Catalog record keys must be unique"):
        CatalogV2((record, record), "2026-10-25T12:00:00Z", "test", "1", "catalog-a")


def test_catalog_nested_nonfinite_metadata_remains_rejected() -> None:
    with pytest.raises(ValueError, match="metadata must contain finite JSON-compatible values"):
        CatalogRecordV2(
            "record-1",
            34.0,
            -80.0,
            metadata=(("nested", {"bad": float("nan")}),),
        )


def test_covariance_validation_remains_strict() -> None:
    with pytest.raises(ValueError, match="positive semidefinite"):
        MeasurementSetV2(
            (
                MeasurementV2(0.0, "2026-10-25T12:00:00Z"),
                MeasurementV2(1.0, "2026-10-25T12:00:00Z"),
            ),
            covariance_deg2=((1.0, 2.0), (2.0, 1.0)),
        )


def test_catalog_reader_rejects_unknown_fields(tmp_path: Path) -> None:
    payload = {
        "contract_type": "CatalogRecordV2",
        "key": "record-1",
        "latitude_deg": 34.0,
        "longitude_deg": -80.0,
        "unknown_field": "must remain rejected",
    }
    path = tmp_path / "catalog-record.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="Unexpected CatalogRecordV2 fields"):
        read_contract(path, CatalogRecordV2)


def test_catalog_round_trip_preserves_loaded_record_values() -> None:
    payload = {
        "contract_type": "CatalogV2",
        "records": [
            {
                "contract_type": "CatalogRecordV2",
                "key": "record-1",
                "latitude_deg": 34.0,
                "longitude_deg": -80.0,
                "raw_type": None,
                "normalized_type": None,
                "report_time_utc": None,
                "quality": None,
                "motion_reference": None,
                "reference_latitude_deg": None,
                "reference_longitude_deg": None,
                "reference_time_utc": None,
                "speed_knots": None,
                "course_deg": None,
                "metadata": [["source", "fixture"]],
                "provenance": [["version", 1]],
            }
        ],
        "timestamp_utc": "2026-10-25T12:00:00Z",
        "source": "test",
        "catalog_version": "1",
        "catalog_id": "catalog-a",
        "schema_version": 2,
    }
    loaded = contract_from_dict(CatalogV2, payload)
    assert loaded.records[0].metadata == (("source", "fixture"),)
    assert loaded.records[0].provenance == (("version", 1),)
