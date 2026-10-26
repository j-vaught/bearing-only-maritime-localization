"""Normalized AIS input contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class AISRecord:
    """One AIS-transmitting object's projected location and normalized type."""

    record_id: str
    east_m: float
    north_m: float
    ais_class: str
    source_type_code: str | None = None

    def __post_init__(self) -> None:
        if not self.record_id.strip():
            raise ValueError("record_id must be nonempty and opaque within its snapshot.")
        if not isfinite(self.east_m) or not isfinite(self.north_m):
            raise ValueError("AIS coordinates must be finite.")
        if not self.ais_class.strip():
            raise ValueError("ais_class must be nonempty.")


@dataclass(frozen=True, slots=True)
class AISSnapshot:
    """AIS catalogue valid at one instant in a named projected CRS."""

    records: tuple[AISRecord, ...]
    timestamp_utc: str
    crs: str
    snapshot_id: str
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported AIS schema version {self.schema_version}.")
        if not self.crs.strip():
            raise ValueError("crs must identify the projected coordinate reference system.")
        if not self.snapshot_id.strip():
            raise ValueError("snapshot_id must be nonempty.")
        try:
            parsed = datetime.fromisoformat(self.timestamp_utc.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("timestamp_utc must be an ISO 8601 timestamp.") from exc
        if parsed.tzinfo is None:
            raise ValueError("timestamp_utc must include a timezone.")
        if not isinstance(self.records, tuple):
            raise TypeError("records must be a tuple of AISRecord records.")
        if any(not isinstance(item, AISRecord) for item in self.records):
            raise TypeError("records must contain only AISRecord records.")
        ids = [item.record_id for item in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("AIS record IDs must be unique within a snapshot.")
