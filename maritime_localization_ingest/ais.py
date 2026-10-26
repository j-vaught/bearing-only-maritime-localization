"""Strict adapters from AIS records and CSV exports into CatalogV2."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pyproj import Geod

from maritime_localization_shared.contracts.inputs_v2 import CatalogRecordV2, CatalogV2
from maritime_localization_shared.contracts.object_types import normalize_object_type

_GEOD = Geod(ellps="WGS84")
_MAX_SOG_KNOTS = 102.2


def _parse_time(value: str, *, naive_utc: bool = False) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid ISO 8601 AIS timestamp {value!r}.") from exc
    if parsed.tzinfo is None:
        if not naive_utc:
            raise ValueError("AIS timestamps must include a timezone.")
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _number(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _valid_motion(row: Mapping[str, Any]) -> tuple[float | None, float | None]:
    speed = _number(row.get("sog_kn", row.get("sog", row.get("speed_knots"))))
    course = _number(row.get("cog_deg", row.get("cog", row.get("course_deg"))))
    if speed is not None and not 0.0 <= speed <= _MAX_SOG_KNOTS:
        speed = None
    if course is not None and not 0.0 <= course < 360.0:
        course = None
    return speed, course


def _stable_key(row: Mapping[str, Any]) -> tuple[str, str, bool]:
    mmsi = row.get("mmsi", row.get("MMSI"))
    if mmsi is not None and str(mmsi).strip():
        value = str(mmsi).strip()
        return f"mmsi:{value}", value, True
    for field in ("source_key", "object_id", "record_id"):
        value = row.get(field)
        if value is not None and str(value).strip():
            value = str(value).strip()
            return f"source:{value}", value, False
    raise ValueError("AIS row requires MMSI or an explicit stable source key.")


def _position(row: Mapping[str, Any]) -> tuple[float, float]:
    lat = _number(row.get("latitude_deg", row.get("lat")))
    lon = _number(row.get("longitude_deg", row.get("lon", row.get("longitude"))))
    if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("AIS row requires finite WGS84 latitude_deg and longitude_deg.")
    return lat, lon


def catalog_from_ais_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    target_time_utc: str,
    source: str,
    catalog_version: str = "2",
    catalog_id: str | None = None,
    observer_id: str | int | None = None,
    max_bracket_seconds: float = 60.0,
    allow_extrapolation: bool = False,
    naive_timestamps_are_utc: bool = False,
) -> CatalogV2:
    """Normalize AIS rows, interpolating only two-sided short brackets.

    Unbracketed reports retain their actual ``report_time_utc`` and carry an
    explicit ``motion_status``. They are never relabeled as target-time data.
    """
    if not math.isfinite(max_bracket_seconds) or max_bracket_seconds < 0:
        raise ValueError("max_bracket_seconds must be finite and nonnegative.")
    if allow_extrapolation:
        raise ValueError("AIS extrapolation is not supported by this adapter.")
    target = _parse_time(target_time_utc)
    parsed: dict[str, list[tuple[datetime, Mapping[str, Any], str, bool]]] = {}
    observer = None if observer_id is None else str(observer_id).strip()
    for row in rows:
        key, source_key, is_mmsi = _stable_key(row)
        if observer is not None and (source_key == observer or key == observer):
            continue
        raw_time = row.get("timestamp_utc", row.get("timestamp", row.get("report_time_utc")))
        if raw_time is None:
            raise ValueError("AIS row requires timestamp_utc.")
        stamp = _parse_time(str(raw_time), naive_utc=naive_timestamps_are_utc)
        _position(row)
        parsed.setdefault(key, []).append((stamp, row, source_key, is_mmsi))

    records: list[CatalogRecordV2] = []
    for key, samples in sorted(parsed.items()):
        samples.sort(key=lambda item: item[0])
        before = [sample for sample in samples if sample[0] <= target]
        after = [sample for sample in samples if sample[0] >= target]
        left = before[-1] if before else None
        right = after[0] if after else None
        interpolated = False
        source_times: tuple[datetime, ...]
        row: Mapping[str, Any]
        report_time: datetime
        source_key: str
        is_mmsi: bool
        lat: float
        lon: float
        if left is not None and right is not None:
            t0, row0, source_key, is_mmsi = left
            t1, row1, _, _ = right
            lat0, lon0 = _position(row0)
            if t0 == t1:
                lat, lon, row, report_time = lat0, lon0, row0, t0
                source_times = (t0,)
                if t0 != target:
                    status = "stale_exact_report"
                else:
                    status = "reported_at_target"
            elif (t1 - t0).total_seconds() <= max_bracket_seconds:
                lat1, lon1 = _position(row1)
                fraction = (target - t0).total_seconds() / (t1 - t0).total_seconds()
                azimuth, _, distance = _GEOD.inv(lon0, lat0, lon1, lat1)
                lon, lat, _ = _GEOD.fwd(lon0, lat0, azimuth, distance * fraction)
                row = row0 if fraction <= 0.5 else row1
                report_time = target
                source_times = (t0, t1)
                status = "interpolated"
                interpolated = True
            else:
                nearest = min(samples, key=lambda item: abs((item[0] - target).total_seconds()))
                report_time, row, source_key, is_mmsi = nearest
                lat, lon = _position(row)
                source_times = (report_time,)
                status = "stale_bracket_too_wide"
        else:
            nearest = min(samples, key=lambda item: abs((item[0] - target).total_seconds()))
            report_time, row, source_key, is_mmsi = nearest
            lat, lon = _position(row)
            source_times = (report_time,)
            status = "stale_extrapolation_disabled"

        speed, course = _valid_motion(row)
        raw_type_value = row.get("vessel_type_code", row.get("raw_type", row.get("ais_class")))
        raw_type = (
            None
            if raw_type_value is None or not str(raw_type_value).strip()
            else str(raw_type_value)
        )
        normalized = normalize_object_type(raw_type)
        # AIS vessel-type numbers are retained as opaque source labels. They are
        # not mapped to a visual taxonomy without a source-specific crosswalk.
        if raw_type is not None and raw_type.isdecimal():
            normalized = "vessel"
        metadata = (
            ("motion_status", status),
            ("position_interpolated", interpolated),
            ("motion_valid", speed is not None and course is not None),
            ("identity_kind", "mmsi" if is_mmsi else "source_key"),
            ("position_uncertainty_m", None),
            ("uncertainty_status", "unspecified_by_source"),
        )
        provenance = (
            ("source", source),
            ("source_key", source_key),
            ("mmsi_present", is_mmsi),
            ("source_timestamps_utc", [_iso(value) for value in source_times]),
            ("position_reference", "interpolated_target" if interpolated else "reported_position"),
        )
        records.append(
            CatalogRecordV2(
                key=key,
                latitude_deg=float(lat),
                longitude_deg=float(lon),
                raw_type=raw_type,
                normalized_type=normalized,
                report_time_utc=_iso(report_time),
                quality=1.0 if status in {"reported_at_target", "interpolated"} else 0.5,
                motion_reference="target_time_interpolation"
                if interpolated
                else "reported_position",
                reference_latitude_deg=float(lat),
                reference_longitude_deg=float(lon),
                reference_time_utc=_iso(report_time),
                speed_knots=speed,
                course_deg=course,
                metadata=metadata,
                provenance=provenance,
            )
        )
    if catalog_id is None:
        payload = f"{source}|{_iso(target)}|{len(records)}"
        catalog_id = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return CatalogV2(tuple(records), _iso(target), source, catalog_version, catalog_id)


def read_ais_csv(
    path: str | Path,
    *,
    target_time_utc: str,
    manifest_path: str | Path | None = None,
    observer_id: str | int | None = None,
    max_bracket_seconds: float = 60.0,
    allow_extrapolation: bool = False,
) -> CatalogV2:
    """Read an existing AIS CSV and its optional manifest without rewriting it."""
    csv_path = Path(path)
    manifest_file = (
        Path(manifest_path) if manifest_path is not None else csv_path.with_suffix(".manifest.json")
    )
    manifest = json.loads(manifest_file.read_text()) if manifest_file.exists() else {}
    with csv_path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ValueError("AIS CSV must have a header row.")
        return catalog_from_ais_rows(
            reader,
            target_time_utc=target_time_utc,
            source=str(manifest.get("source", csv_path.name)),
            catalog_version=str(manifest.get("schema_version", "legacy-csv")),
            catalog_id=csv_path.stem,
            observer_id=observer_id,
            max_bracket_seconds=max_bracket_seconds,
            allow_extrapolation=allow_extrapolation,
            naive_timestamps_are_utc=bool(manifest.get("time_start_utc")),
        )
