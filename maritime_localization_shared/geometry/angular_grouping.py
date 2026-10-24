"""Merge detections that cannot be resolved by bearing angle.

The returned observations contain only estimator-visible fields. Object IDs and
group membership are returned separately for evaluation truth and must not be
passed to a search algorithm.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import NamedTuple

from maritime_localization_shared.contracts.observation import Observation


@dataclass(frozen=True, slots=True)
class DetectedObject:
    """One simulator detection before angular-resolution merging.

    ``range_m`` is available to the simulator to select the visible frontmost
    object in an unresolved group; it is never copied into ``Observation``.
    ``angular_half_width_deg`` optionally describes the object's apparent
    extent and is carried separately from sensor bearing uncertainty.
    """

    object_id: str
    object_type: str
    bearing_deg: float
    range_m: float
    bearing_sigma_deg: float = 0.0
    angular_half_width_deg: float = 0.0

    def __post_init__(self) -> None:
        if not self.object_id.strip() or not self.object_type.strip():
            raise ValueError("object_id and object_type must be nonempty.")
        if not isfinite(self.bearing_deg):
            raise ValueError("bearing_deg must be finite.")
        if not isfinite(self.range_m) or self.range_m < 0.0:
            raise ValueError("range_m must be finite and nonnegative.")
        if not isfinite(self.bearing_sigma_deg) or self.bearing_sigma_deg < 0.0:
            raise ValueError("bearing_sigma_deg must be finite and nonnegative.")
        if not isfinite(self.angular_half_width_deg) or self.angular_half_width_deg < 0.0:
            raise ValueError("angular_half_width_deg must be finite and nonnegative.")


class GroupedDetections(NamedTuple):
    """Estimator measurements and aligned truth-only group membership."""

    observations: tuple[Observation, ...]
    truth_groups: tuple[tuple[str, ...], ...]


def _wrap_360(angle_deg: float) -> float:
    return angle_deg % 360.0


def _wrap_signed(angle_deg: float) -> float:
    """Wrap clockwise-from-north bearing to the shared contract interval."""
    return ((angle_deg + 180.0) % 360.0) - 180.0


def group_angular_detections(
    detections: tuple[DetectedObject, ...] | list[DetectedObject],
    resolution_deg: float,
    *,
    report_type_labels: bool = True,
) -> GroupedDetections:
    """Merge neighboring bearings within ``resolution_deg`` on the circle.

    Circularly adjacent detections merge when their angular gap is no greater
    than the resolution threshold. This naturally groups detections across
    0/360 degrees. When a group contains multiple objects, the nearest object
    supplies its reported bearing and type. The representative's angular
    sensor uncertainty and apparent angular half-width remain separate fields.

    Groups are ordered by their first member's bearing after rotating the
    circle to begin immediately after the first resolvable gap. Members inside
    each truth group are ordered by bearing in that same circular frame.
    Equal-range representatives are selected by object ID for determinism.
    """
    if not isfinite(resolution_deg) or not 0.0 <= resolution_deg < 180.0:
        raise ValueError("resolution_deg must be finite and in [0, 180).")
    if not isinstance(report_type_labels, bool):
        raise TypeError("report_type_labels must be a bool.")
    if any(not isinstance(item, DetectedObject) for item in detections):
        raise TypeError("detections must contain only DetectedObject records.")
    if not detections:
        return GroupedDetections((), ())

    ordered = sorted(
        ((_wrap_360(item.bearing_deg), item) for item in detections),
        key=lambda item: (item[0], item[1].object_id),
    )
    gaps = [
        (ordered[(index + 1) % len(ordered)][0] - item[0]) % 360.0
        for index, item in enumerate(ordered)
    ]
    break_indices = [index for index, gap in enumerate(gaps) if gap > resolution_deg]

    if break_indices:
        # The first resolvable gap makes output order stable regardless of input order.
        start_index = break_indices[0]
        rotated = ordered[start_index + 1 :] + ordered[: start_index + 1]
        clusters: list[list[DetectedObject]] = []
        current: list[DetectedObject] = []
        for index, (_, detection) in enumerate(rotated):
            current.append(detection)
            next_gap_index = (start_index + 1 + index) % len(gaps)
            if gaps[next_gap_index] > resolution_deg:
                clusters.append(current)
                current = []
        if current:
            clusters.append(current)
    else:
        # No resolvable gap remains, so all bearings form one circular group.
        clusters = [[detection for _, detection in ordered]]

    observations: list[Observation] = []
    truth_groups: list[tuple[str, ...]] = []
    for cluster in clusters:
        representative = min(cluster, key=lambda item: (item.range_m, item.object_id))
        observations.append(
            Observation(
                bearing_deg=_wrap_signed(representative.bearing_deg),
                bearing_sigma_deg=representative.bearing_sigma_deg,
                object_type=representative.object_type if report_type_labels else None,
                angular_half_width_deg=representative.angular_half_width_deg,
            )
        )
        truth_groups.append(tuple(item.object_id for item in cluster))

    return GroupedDetections(tuple(observations), tuple(truth_groups))
