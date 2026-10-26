"""A29 gate for deciding whether a GPU assignment prototype is warranted.

This module contains no GPU implementation. The profile gate keeps the exact CPU
solver as the only assignment backend unless assignment is at least one fifth of
the complete development search workload.
"""

from __future__ import annotations

from math import isfinite
from typing import Literal

AssignmentAccelerationDecision = Literal["defer_cpu", "prototype_eligible"]
ASSIGNMENT_ACCELERATION_THRESHOLD = 0.20


def assignment_acceleration_decision(
    assignment_fraction: float,
    *,
    threshold: float = ASSIGNMENT_ACCELERATION_THRESHOLD,
) -> AssignmentAccelerationDecision:
    """Return whether measured assignment share clears the prototype gate.

    A passing gate permits investigation only. It does not certify an accelerated
    implementation or allow a different objective.
    """
    if not isfinite(assignment_fraction) or not 0.0 <= assignment_fraction <= 1.0:
        raise ValueError("assignment_fraction must be finite and in [0, 1].")
    if not isfinite(threshold) or not 0.0 < threshold <= 1.0:
        raise ValueError("threshold must be finite and in (0, 1].")
    return "prototype_eligible" if assignment_fraction >= threshold else "defer_cpu"


__all__ = [
    "ASSIGNMENT_ACCELERATION_THRESHOLD",
    "AssignmentAccelerationDecision",
    "assignment_acceleration_decision",
]
