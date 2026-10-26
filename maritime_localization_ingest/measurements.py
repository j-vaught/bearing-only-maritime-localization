"""Adapters for replaying north-referenced bearing observations as V2."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from maritime_localization_shared.contracts.inputs_v2 import MeasurementSetV2, MeasurementV2
from maritime_localization_shared.contracts.observation import MeasurementSet


def measurement_set_v2_from_observations(
    measurements: MeasurementSet,
    *,
    timestamps_utc: Iterable[str] | None = None,
    calibrated_common_bias_mean_deg: float | None = None,
    calibrated_common_bias_sigma_deg: float | None = None,
) -> MeasurementSetV2:
    """Replay V1 bearings without changing north reference, sigma, or extent.

    A V1 set has only one optional timestamp. Per-report timestamps must be
    supplied explicitly when the source reports are asynchronous.
    """
    if not isinstance(measurements, MeasurementSet):
        raise TypeError("measurements must be a MeasurementSet.")
    if timestamps_utc is None:
        if measurements.timestamp_utc is None:
            raise ValueError("A measurement timestamp is required; timestamps are never invented.")
        times = (measurements.timestamp_utc,) * len(measurements.observations)
    else:
        times = tuple(timestamps_utc)
        if len(times) != len(measurements.observations):
            raise ValueError("timestamps_utc must have one timestamp per observation.")
    values: list[MeasurementV2] = []
    for observation, stamp in zip(measurements.observations, times, strict=True):
        # V1 allows [-180, 180); convert to equivalent [0, 360) clockwise north.
        bearing = observation.bearing_deg % 360.0
        values.append(
            MeasurementV2(
                bearing_deg=bearing,
                timestamp_utc=stamp,
                sigma_deg=observation.bearing_sigma_deg,
                object_type=observation.object_type,
                angular_half_width_deg=observation.angular_half_width_deg,
            )
        )
    if not values:
        if measurements.timestamp_utc is None:
            raise ValueError("An empty measurement set requires its timestamp_utc.")
        datetime.fromisoformat(measurements.timestamp_utc.replace("Z", "+00:00"))
    return MeasurementSetV2(
        tuple(values),
        trial_id=measurements.trial_id,
        calibrated_common_bias_mean_deg=calibrated_common_bias_mean_deg,
        calibrated_common_bias_sigma_deg=calibrated_common_bias_sigma_deg,
    )


__all__ = ["measurement_set_v2_from_observations"]
