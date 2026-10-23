"""Estimator-visible bearing measurements.

These records intentionally contain no detected-object identity, range, or
observer position/orientation. Bearings are clockwise from true north.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class Observation:
    """One detected object or unresolved angular group."""

    bearing_deg: float
    bearing_sigma_deg: float
    object_type: str | None = None
    angular_half_width_deg: float | None = None

    def __post_init__(self) -> None:
        if not isfinite(self.bearing_deg) or not -180.0 <= self.bearing_deg < 180.0:
            raise ValueError("bearing_deg must be finite and in [-180, 180).")
        if not isfinite(self.bearing_sigma_deg) or self.bearing_sigma_deg < 0.0:
            raise ValueError("bearing_sigma_deg must be finite and nonnegative.")
        if self.angular_half_width_deg is not None and (
            not isfinite(self.angular_half_width_deg) or self.angular_half_width_deg < 0.0
        ):
            raise ValueError("angular_half_width_deg must be finite and nonnegative or None.")
        if self.object_type is not None and not self.object_type.strip():
            raise ValueError("object_type must be a nonempty string or None.")


@dataclass(frozen=True, slots=True)
class MeasurementSet:
    """One instantaneous sensor report, without observer pose or object IDs."""

    observations: tuple[Observation, ...]
    trial_id: str | None = None
    timestamp_utc: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported measurement schema version {self.schema_version}.")
        if self.trial_id is not None and not self.trial_id.strip():
            raise ValueError("trial_id must be a nonempty string or None.")
        if self.timestamp_utc is not None:
            try:
                parsed = datetime.fromisoformat(self.timestamp_utc.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("timestamp_utc must be an ISO 8601 timestamp.") from exc
            if parsed.tzinfo is None:
                raise ValueError("timestamp_utc must include a timezone.")
        if not isinstance(self.observations, tuple):
            raise TypeError("observations must be a tuple of Observation records.")
        if any(not isinstance(item, Observation) for item in self.observations):
            raise TypeError("observations must contain only Observation records.")
