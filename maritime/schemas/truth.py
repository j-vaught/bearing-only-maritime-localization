"""Evaluator-only simulation truth contracts.

Do not pass these objects to search algorithms. Estimator-facing observation
types live in :mod:`maritime.schemas.observation`.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class TrueObject:
    """A scene object and its association with a reported angular group."""

    object_id: str
    object_type: str
    east_m: float
    north_m: float
    ais_record_id: str | None = None
    observation_index: int | None = None

    def __post_init__(self) -> None:
        if not self.object_id.strip() or not self.object_type.strip():
            raise ValueError("object_id and object_type must be nonempty.")
        if not isfinite(self.east_m) or not isfinite(self.north_m):
            raise ValueError("Truth coordinates must be finite.")
        if self.observation_index is not None and self.observation_index < 0:
            raise ValueError("observation_index must be nonnegative or None.")


@dataclass(frozen=True, slots=True)
class EvaluationTruth:
    """Hidden observer pose and object truth for offline evaluation only."""

    observer_east_m: float
    observer_north_m: float
    observer_heading_deg: float
    objects: tuple[TrueObject, ...]
    trial_id: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported truth schema version {self.schema_version}.")
        if self.trial_id is not None and not self.trial_id.strip():
            raise ValueError("trial_id must be a nonempty string or None.")
        if not all(
            isfinite(value)
            for value in (
                self.observer_east_m,
                self.observer_north_m,
                self.observer_heading_deg,
            )
        ):
            raise ValueError("Truth pose must contain finite values.")
        if not isinstance(self.objects, tuple):
            raise TypeError("objects must be a tuple of TrueObject records.")
        if any(not isinstance(item, TrueObject) for item in self.objects):
            raise TypeError("objects must contain only TrueObject records.")
