"""Explicit version 2 input contracts for independent localization programs.

Angles use degrees. Bearings are clockwise from true north, and geographic
positions use latitude/longitude degrees. Measurements contain no truth pose,
target range, target identity, or association.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any

SCHEMA_VERSION = 2


def _nonempty(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string.")


def _timestamp(value: str, name: str) -> None:
    _nonempty(value, name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO 8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone.")


def _finite(value: float, name: str) -> None:
    if not isfinite(value):
        raise ValueError(f"{name} must be finite.")


def _json_value(value: Any, name: str) -> None:
    try:
        json.dumps(value, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain finite JSON-compatible values.") from exc


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number {value!r} is not permitted.")


def _geojson(value: str, name: str) -> None:
    try:
        geometry = json.loads(value, parse_constant=_reject_constant)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"{name} must be valid GeoJSON geometry JSON.") from exc
    if not isinstance(geometry, dict) or geometry.get("type") not in {
        "Polygon",
        "MultiPolygon",
        "GeometryCollection",
    }:
        raise ValueError(f"{name} must contain a Polygon, MultiPolygon, or GeometryCollection.")
    _json_value(geometry, name)


@dataclass(frozen=True, slots=True)
class MeasurementV2:
    """One angular report. Bearing is clockwise from true north in degrees."""

    bearing_deg: float
    timestamp_utc: str
    sigma_deg: float | None = None
    object_type: str | None = None
    angular_half_width_deg: float | None = None

    def __post_init__(self) -> None:
        _finite(self.bearing_deg, "bearing_deg")
        if not 0.0 <= self.bearing_deg < 360.0:
            raise ValueError("bearing_deg must be clockwise from true north in [0, 360).")
        _timestamp(self.timestamp_utc, "timestamp_utc")
        if self.sigma_deg is not None and (not isfinite(self.sigma_deg) or self.sigma_deg < 0.0):
            raise ValueError("sigma_deg must be finite and nonnegative or None.")
        if self.object_type is not None:
            _nonempty(self.object_type, "object_type")
        if self.angular_half_width_deg is not None and (
            not isfinite(self.angular_half_width_deg)
            or not 0.0 <= self.angular_half_width_deg <= 180.0
        ):
            raise ValueError("angular_half_width_deg must be finite and in [0, 180].")


@dataclass(frozen=True, slots=True)
class MeasurementSetV2:
    """Measurement collection with optional covariance in square degrees."""

    measurements: tuple[MeasurementV2, ...]
    trial_id: str | None = None
    covariance_deg2: tuple[tuple[float, ...], ...] | None = None
    calibrated_common_bias_mean_deg: float | None = None
    calibrated_common_bias_sigma_deg: float | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported measurement schema version {self.schema_version}.")
        if self.trial_id is not None:
            _nonempty(self.trial_id, "trial_id")
        if not isinstance(self.measurements, tuple) or any(
            not isinstance(item, MeasurementV2) for item in self.measurements
        ):
            raise TypeError("measurements must be a tuple of MeasurementV2 records.")
        if self.covariance_deg2 is not None:
            covariance = self.covariance_deg2
            count = len(self.measurements)
            if not isinstance(covariance, tuple) or len(covariance) != count:
                raise ValueError("covariance_deg2 must be a square matrix matching measurements.")
            if any(not isinstance(row, tuple) or len(row) != count for row in covariance):
                raise ValueError("covariance_deg2 must be a square matrix matching measurements.")
            import numpy as np

            matrix = np.asarray(covariance, dtype=float)
            if not np.isfinite(matrix).all():
                raise ValueError("covariance_deg2 must contain only finite values.")
            if not np.allclose(matrix, matrix.T, rtol=1e-10, atol=1e-12):
                raise ValueError("covariance_deg2 must be symmetric.")
            if np.linalg.eigvalsh(matrix).min(initial=0.0) < -1e-10:
                raise ValueError("covariance_deg2 must be positive semidefinite.")
        if self.calibrated_common_bias_mean_deg is not None:
            _finite(self.calibrated_common_bias_mean_deg, "calibrated_common_bias_mean_deg")
        if self.calibrated_common_bias_sigma_deg is not None:
            _finite(self.calibrated_common_bias_sigma_deg, "calibrated_common_bias_sigma_deg")
            if self.calibrated_common_bias_sigma_deg < 0.0:
                raise ValueError("calibrated_common_bias_sigma_deg must be nonnegative.")
        if (self.calibrated_common_bias_mean_deg is None) != (
            self.calibrated_common_bias_sigma_deg is None
        ):
            raise ValueError("Calibrated common-bias mean and sigma must be supplied together.")


@dataclass(frozen=True, slots=True)
class CatalogRecordV2:
    """Stable-key geographic catalog feature with optional motion reference."""

    key: str
    latitude_deg: float
    longitude_deg: float
    raw_type: str | None = None
    normalized_type: str | None = None
    report_time_utc: str | None = None
    quality: float | None = None
    motion_reference: str | None = None
    reference_latitude_deg: float | None = None
    reference_longitude_deg: float | None = None
    reference_time_utc: str | None = None
    speed_knots: float | None = None
    course_deg: float | None = None
    metadata: tuple[tuple[str, Any], ...] = ()
    provenance: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        _nonempty(self.key, "key")
        if not isfinite(self.latitude_deg) or not -90.0 <= self.latitude_deg <= 90.0:
            raise ValueError("latitude_deg must be finite and in [-90, 90].")
        if not isfinite(self.longitude_deg) or not -180.0 <= self.longitude_deg <= 180.0:
            raise ValueError("longitude_deg must be finite and in [-180, 180].")
        for name, value in (
            ("raw_type", self.raw_type),
            ("normalized_type", self.normalized_type),
            ("motion_reference", self.motion_reference),
        ):
            if value is not None:
                _nonempty(value, name)
        if self.report_time_utc is not None:
            _timestamp(self.report_time_utc, "report_time_utc")
        if self.reference_time_utc is not None:
            _timestamp(self.reference_time_utc, "reference_time_utc")
        if self.quality is not None:
            _finite(self.quality, "quality")
        for name, value in (
            ("reference_latitude_deg", self.reference_latitude_deg),
            ("reference_longitude_deg", self.reference_longitude_deg),
            ("speed_knots", self.speed_knots),
            ("course_deg", self.course_deg),
        ):
            if value is not None:
                _finite(value, name)
        if (
            self.reference_latitude_deg is not None
            and not -90.0 <= self.reference_latitude_deg <= 90.0
        ):
            raise ValueError("reference_latitude_deg must be in [-90, 90].")
        if (
            self.reference_longitude_deg is not None
            and not -180.0 <= self.reference_longitude_deg <= 180.0
        ):
            raise ValueError("reference_longitude_deg must be in [-180, 180].")
        if self.speed_knots is not None and self.speed_knots < 0:
            raise ValueError("speed_knots must be nonnegative.")
        if self.course_deg is not None and not 0.0 <= self.course_deg < 360.0:
            raise ValueError("course_deg must be in [0, 360).")
        if (self.reference_latitude_deg is None) != (self.reference_longitude_deg is None):
            raise ValueError("Reference latitude and longitude must be supplied together.")
        for name, pairs in (("metadata", self.metadata), ("provenance", self.provenance)):
            if not isinstance(pairs, tuple) or any(
                not isinstance(pair, tuple) or len(pair) != 2 for pair in pairs
            ):
                raise TypeError(f"{name} must be a tuple of (key, value) pairs.")
            keys = [pair[0] for pair in pairs]
            if any(not isinstance(key, str) or not key for key in keys):
                raise ValueError(f"{name} keys must be nonempty strings.")
            if len(keys) != len(set(keys)):
                raise ValueError(f"{name} keys must be unique.")
            _json_value(dict(pairs), name)


@dataclass(frozen=True, slots=True)
class CatalogV2:
    """Time-stamped geographic catalog with unique stable feature keys."""

    records: tuple[CatalogRecordV2, ...]
    timestamp_utc: str
    source: str
    catalog_version: str
    catalog_id: str
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported catalog schema version {self.schema_version}.")
        _timestamp(self.timestamp_utc, "timestamp_utc")
        for name in ("source", "catalog_version", "catalog_id"):
            _nonempty(getattr(self, name), name)
        if not isinstance(self.records, tuple) or any(
            not isinstance(item, CatalogRecordV2) for item in self.records
        ):
            raise TypeError("records must be a tuple of CatalogRecordV2 records.")
        keys = [record.key for record in self.records]
        if len(keys) != len(set(keys)):
            raise ValueError("Catalog record keys must be unique.")


@dataclass(frozen=True, slots=True)
class MapV2:
    """Neutral GeoJSON land mask with explicit covered and unknown regions."""

    crs: str
    source: str
    map_version: str
    coverage_status: str = "unknown"
    coverage_geometry_geojson: str | None = None
    unknown_geometry_geojson: str | None = None
    land_geometry_geojson: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported map schema version {self.schema_version}.")
        for name in ("crs", "source", "map_version"):
            _nonempty(getattr(self, name), name)
        if self.coverage_status not in {"known", "partial", "unknown"}:
            raise ValueError("coverage_status must be known, partial, or unknown.")
        for name in (
            "coverage_geometry_geojson",
            "unknown_geometry_geojson",
            "land_geometry_geojson",
        ):
            geometry = getattr(self, name)
            if geometry is not None:
                _geojson(geometry, name)
        if self.coverage_status == "known" and (
            self.coverage_geometry_geojson is None or self.unknown_geometry_geojson is not None
        ):
            raise ValueError("Known coverage requires coverage geometry and no unknown geometry.")
        if self.coverage_status == "partial" and (
            self.coverage_geometry_geojson is None or self.unknown_geometry_geojson is None
        ):
            raise ValueError("Partial coverage requires both coverage and unknown geometries.")
        if self.coverage_status == "unknown" and (
            self.coverage_geometry_geojson is not None or self.unknown_geometry_geojson is not None
        ):
            raise ValueError("Unknown coverage cannot claim known or unknown region geometries.")
        if self.land_geometry_geojson is not None and self.coverage_geometry_geojson is None:
            raise ValueError("Land geometry requires explicit coverage_geometry_geojson.")
