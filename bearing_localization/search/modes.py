"""Public evidence for independently converged spatial search modes."""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot, isfinite

from .refinement import RefinementResult

_CONVERGED_STOP_REASONS = frozenset({"relative_improvement_below_1e-8", "displacement_below_0_01m"})


@dataclass(frozen=True, slots=True)
class SearchModeEvidence:
    """Runtime-only evidence that one scored spatial mode locally converged.

    This record contains no evaluator truth. Its stable serialized field names
    are consumed by decision logging and evaluator-side calibration tooling.
    """

    east_m: float
    north_m: float
    score: float
    matched_catalog_keys: tuple[str, ...]
    radius_95_m: float
    stop_reason: str

    def __post_init__(self) -> None:
        if not all(isfinite(value) for value in (self.east_m, self.north_m, self.score)):
            raise ValueError("Mode coordinates and score must be finite.")
        if not isfinite(self.radius_95_m) or self.radius_95_m < 0.0:
            raise ValueError("radius_95_m must be finite and nonnegative.")
        if self.stop_reason not in _CONVERGED_STOP_REASONS:
            raise ValueError("Mode evidence requires a recognized local convergence stop reason.")
        if not isinstance(self.matched_catalog_keys, tuple) or any(
            not isinstance(key, str) or not key.strip() for key in self.matched_catalog_keys
        ):
            raise TypeError("matched_catalog_keys must be a tuple of nonempty strings.")
        if len(set(self.matched_catalog_keys)) < 3:
            raise ValueError("Mode evidence requires at least three distinct matched groups.")

    def as_dict(self) -> dict[str, object]:
        """Return the stable JSON-compatible public representation."""
        return {
            "east_m": self.east_m,
            "north_m": self.north_m,
            "score": self.score,
            "matched_catalog_keys": list(self.matched_catalog_keys),
            "radius_95_m": self.radius_95_m,
            "stop_reason": self.stop_reason,
        }


def verified_mode_competitor_gap(
    *,
    top_east_m: float,
    top_north_m: float,
    top_score: float,
    modes: tuple[SearchModeEvidence, ...] | list[SearchModeEvidence],
) -> tuple[bool, float | None]:
    """Return top-mode verification and its best distinct distant-mode score gap."""
    if not all(isfinite(value) for value in (top_east_m, top_north_m, top_score)):
        return False, None
    matching = [
        mode
        for mode in modes
        if hypot(mode.east_m - top_east_m, mode.north_m - top_north_m) <= 1e-6
        and abs(mode.score - top_score) <= max(1e-12, 1e-10 * max(abs(mode.score), abs(top_score)))
    ]
    if not matching:
        return False, None
    top_mode = min(matching, key=lambda mode: (mode.score, mode.east_m, mode.north_m))
    distant = [
        mode
        for mode in modes
        if hypot(mode.east_m - top_mode.east_m, mode.north_m - top_mode.north_m) >= 100.0
    ]
    if not distant:
        return True, None
    competitor = min(distant, key=lambda mode: (mode.score, mode.east_m, mode.north_m))
    return True, competitor.score - top_mode.score


def converged_mode_evidence(
    refinement: RefinementResult,
    *,
    deduplicate_within_m: float = 25.0,
) -> tuple[SearchModeEvidence, ...]:
    """Extract deterministic distinct converged modes from refinement output.

    Modes are considered in score/east/north order. The first mode within the
    deduplication radius wins, so duplicate seeds do not inflate competitor
    evidence. Iteration-limit and unsupported seeds are never convergence.
    """
    if not isfinite(deduplicate_within_m) or deduplicate_within_m < 0.0:
        raise ValueError("deduplicate_within_m must be finite and nonnegative.")
    candidates: list[SearchModeEvidence] = []
    for refined in refinement.modes:
        hypothesis = refined.hypothesis
        if (
            refined.stop_reason not in _CONVERGED_STOP_REASONS
            or refined.uncertainty_rank != 2
            or refined.uncertainty_singular
            or hypothesis is None
            or hypothesis.uncertainty is None
        ):
            continue
        keys = tuple(sorted({assignment.catalog_key for assignment in hypothesis.assignments}))
        if len(keys) < 3:
            continue
        candidates.append(
            SearchModeEvidence(
                east_m=hypothesis.east_m,
                north_m=hypothesis.north_m,
                score=hypothesis.score,
                matched_catalog_keys=keys,
                radius_95_m=hypothesis.uncertainty.major_radius_m,
                stop_reason=refined.stop_reason,
            )
        )
    candidates.sort(key=lambda mode: (mode.score, mode.east_m, mode.north_m))
    retained: list[SearchModeEvidence] = []
    for candidate in candidates:
        if any(
            hypot(candidate.east_m - previous.east_m, candidate.north_m - previous.north_m)
            <= deduplicate_within_m
            for previous in retained
        ):
            continue
        retained.append(candidate)
    return tuple(retained)


__all__ = ["SearchModeEvidence", "converged_mode_evidence", "verified_mode_competitor_gap"]
