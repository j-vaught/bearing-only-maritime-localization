"""Convert visible object geometry into noisy global-bearing measurements.

Coordinates use a local east/north frame in metres. Bearings are degrees
clockwise from north, wrapped to ``[-180, 180)``. Sensor uncertainty and an
object's apparent angular half-width are recorded separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees, isfinite

import numpy as np
from numpy.random import Generator

from maritime.schemas import Observation

PointEN = tuple[float, float]


@dataclass(frozen=True, slots=True)
class DetectionGeometry:
    """Visible target geometry, with optional endpoints for an extended object.

    ``center_en_m`` is the point whose bearing represents the object. For a
    vessel, callers should supply the hull midpoint. ``endpoints_en_m`` can
    contain the two visible ends of the hull, which determine apparent angular
    half-width but do not change the representative center bearing.
    """

    center_en_m: PointEN
    object_type: str | None = None
    endpoints_en_m: tuple[PointEN, PointEN] | None = None


def _validate_point(point: PointEN, name: str) -> None:
    if len(point) != 2 or not all(isfinite(value) for value in point):
        raise ValueError(f"{name} must contain finite east and north coordinates.")


def _wrap_bearing(angle_deg: float) -> float:
    """Wrap an angle in degrees to the observation contract's interval."""
    return (angle_deg + 180.0) % 360.0 - 180.0


def _bearing_deg(observer_en_m: PointEN, point_en_m: PointEN) -> float:
    delta_east = point_en_m[0] - observer_en_m[0]
    delta_north = point_en_m[1] - observer_en_m[1]
    if delta_east == 0.0 and delta_north == 0.0:
        raise ValueError("Bearing is undefined when observer and target positions coincide.")
    return _wrap_bearing(degrees(atan2(delta_east, delta_north)))


def apparent_angular_half_width_deg(observer_en_m: PointEN, geometry: DetectionGeometry) -> float:
    """Return the largest endpoint offset from the object's center bearing.

    Point targets have zero angular half-width. The shortest signed circular
    difference is used so objects spanning north are measured correctly.
    """
    _validate_point(observer_en_m, "observer_en_m")
    _validate_point(geometry.center_en_m, "center_en_m")
    if geometry.endpoints_en_m is None:
        return 0.0
    center_bearing = _bearing_deg(observer_en_m, geometry.center_en_m)
    endpoint_offsets = []
    for index, endpoint in enumerate(geometry.endpoints_en_m):
        _validate_point(endpoint, f"endpoints_en_m[{index}]")
        endpoint_bearing = _bearing_deg(observer_en_m, endpoint)
        endpoint_offsets.append(abs(_wrap_bearing(endpoint_bearing - center_bearing)))
    return max(endpoint_offsets)


def make_observation(
    observer_en_m: PointEN,
    geometry: DetectionGeometry,
    bearing_sigma_deg: float,
    *,
    report_type_labels: bool = True,
    rng: Generator | None = None,
) -> Observation:
    """Create one noisy observation from visible object geometry.

    Gaussian sensor noise is applied to the center bearing. Its standard
    deviation is retained in ``bearing_sigma_deg``; the target's angular size
    is retained independently in ``angular_half_width_deg``.
    """
    _validate_point(observer_en_m, "observer_en_m")
    _validate_point(geometry.center_en_m, "center_en_m")
    if not isfinite(bearing_sigma_deg) or bearing_sigma_deg < 0.0:
        raise ValueError("bearing_sigma_deg must be finite and nonnegative.")
    if geometry.object_type is not None and not geometry.object_type.strip():
        raise ValueError("object_type must be a nonempty string or None.")

    center_bearing = _bearing_deg(observer_en_m, geometry.center_en_m)
    half_width = apparent_angular_half_width_deg(observer_en_m, geometry)
    generator = rng if rng is not None else np.random.default_rng()
    noisy_bearing = _wrap_bearing(center_bearing + float(generator.normal(0.0, bearing_sigma_deg)))
    return Observation(
        bearing_deg=noisy_bearing,
        bearing_sigma_deg=bearing_sigma_deg,
        object_type=geometry.object_type if report_type_labels else None,
        angular_half_width_deg=half_width,
    )


def add_bearing_noise(observation: Observation, rng: Generator) -> Observation:
    """Return a copy with one Gaussian error applied to its global bearing.

    This is useful after angular grouping, where the sensor reports one
    representative measurement for a group and noise must not affect grouping.
    The stored uncertainty remains the standard deviation used to draw noise.
    """
    if not isinstance(observation, Observation):
        raise TypeError("observation must be an Observation.")
    if not isinstance(rng, Generator):
        raise TypeError("rng must be a numpy.random.Generator.")
    noisy_bearing = _wrap_bearing(
        observation.bearing_deg + float(rng.normal(0.0, observation.bearing_sigma_deg))
    )
    return Observation(
        bearing_deg=noisy_bearing,
        bearing_sigma_deg=observation.bearing_sigma_deg,
        object_type=observation.object_type,
        angular_half_width_deg=observation.angular_half_width_deg,
    )


__all__ = [
    "DetectionGeometry",
    "add_bearing_noise",
    "apparent_angular_half_width_deg",
    "make_observation",
]
