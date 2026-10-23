"""Search output contract."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class PositionEstimate:
    """Estimated observer location and search diagnostics, without truth data."""

    east_m: float
    north_m: float
    score: float
    elapsed_s: float
    algorithm: str
    matched_pairs: tuple[tuple[int, str], ...] = ()
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported estimate schema version {self.schema_version}.")
        if not all(isfinite(value) for value in (self.east_m, self.north_m, self.score)):
            raise ValueError("Estimate coordinates and score must be finite.")
        if not isfinite(self.elapsed_s) or self.elapsed_s < 0:
            raise ValueError("elapsed_s must be finite and nonnegative.")
        if not self.algorithm.strip():
            raise ValueError("algorithm must be nonempty.")
        if any(index < 0 or not ais_id for index, ais_id in self.matched_pairs):
            raise ValueError("matched_pairs must contain valid observation indices and AIS IDs.")
