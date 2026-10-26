from __future__ import annotations

import numpy as np
import pytest

from maritime.simulator.measurement_model import (
    DetectionGeometry,
    apparent_angular_half_width_deg,
    make_observation,
)


@pytest.mark.parametrize(
    ("target", "expected"),
    [((0.0, 10.0), 0.0), ((10.0, 0.0), 90.0), ((0.0, -10.0), -180.0), ((-10.0, 0.0), -90.0)],
)
def test_bearing_is_global_clockwise_from_north(target, expected):
    observation = make_observation(
        (0.0, 0.0), DetectionGeometry(target, "ship"), 0.0, rng=np.random.default_rng(1)
    )
    assert observation.bearing_deg == pytest.approx(expected)


def test_sensor_sigma_and_apparent_half_width_are_distinct():
    geometry = DetectionGeometry(
        center_en_m=(100.0, 1000.0),
        object_type="ship",
        endpoints_en_m=((-50.0, 1000.0), (250.0, 1000.0)),
    )
    observation = make_observation((0.0, 0.0), geometry, 0.4, rng=np.random.default_rng(5))
    expected_half_width = max(
        abs(np.degrees(np.arctan2(-50.0, 1000.0)) - np.degrees(np.arctan2(100.0, 1000.0))),
        abs(np.degrees(np.arctan2(250.0, 1000.0)) - np.degrees(np.arctan2(100.0, 1000.0))),
    )
    assert observation.bearing_sigma_deg == pytest.approx(0.4)
    assert observation.angular_half_width_deg == pytest.approx(expected_half_width)
    assert observation.bearing_deg != pytest.approx(expected_half_width)


def test_half_width_uses_shortest_arc_across_north():
    geometry = DetectionGeometry(
        center_en_m=(0.0, 1000.0),
        endpoints_en_m=((-10.0, 1000.0), (10.0, 1000.0)),
    )
    half_width = apparent_angular_half_width_deg((0.0, 0.0), geometry)
    assert half_width == pytest.approx(np.degrees(np.arctan2(10.0, 1000.0)))


def test_measurement_noise_is_seed_reproducible_and_wrapped():
    geometry = DetectionGeometry((1.0, -100.0), "buoy")
    first = make_observation((0.0, 0.0), geometry, 8.0, rng=np.random.default_rng(12))
    second = make_observation((0.0, 0.0), geometry, 8.0, rng=np.random.default_rng(12))
    assert first == second
    assert -180.0 <= first.bearing_deg < 180.0


def test_type_labels_can_be_suppressed():
    observation = make_observation(
        (0.0, 0.0),
        DetectionGeometry((0.0, 1.0), "channel_marker"),
        0.0,
        report_type_labels=False,
        rng=np.random.default_rng(1),
    )
    assert observation.object_type is None


@pytest.mark.parametrize("sigma", [-1.0, float("nan"), float("inf")])
def test_invalid_noise_sigma_is_rejected(sigma):
    with pytest.raises(ValueError, match="bearing_sigma_deg"):
        make_observation((0.0, 0.0), DetectionGeometry((0.0, 1.0)), sigma)


def test_coincident_observer_and_target_is_rejected():
    with pytest.raises(ValueError, match="coincide"):
        make_observation((1.0, 2.0), DetectionGeometry((1.0, 2.0)), 0.0)
