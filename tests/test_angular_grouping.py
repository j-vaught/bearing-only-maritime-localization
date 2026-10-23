from __future__ import annotations

import pytest

from bearing_localization.simulator.angular_grouping import (
    DetectedObject,
    group_angular_detections,
)


def _detection(
    object_id: str,
    bearing_deg: float,
    range_m: float,
    object_type: str = "ship",
    *,
    sigma: float = 0.0,
    half_width: float = 0.0,
) -> DetectedObject:
    return DetectedObject(
        object_id=object_id,
        object_type=object_type,
        bearing_deg=bearing_deg,
        range_m=range_m,
        bearing_sigma_deg=sigma,
        angular_half_width_deg=half_width,
    )


def test_groups_detections_across_zero_and_selects_nearest_representative() -> None:
    result = group_angular_detections(
        [
            _detection("far", 1.0, 500.0, "buoy"),
            _detection("separate", 20.0, 100.0),
            _detection("near", 359.0, 25.0, "ship", sigma=0.4, half_width=0.3),
        ],
        resolution_deg=2.1,
    )

    assert len(result.observations) == 2
    assert result.truth_groups == (("separate",), ("near", "far"))
    assert result.observations[1].bearing_deg == pytest.approx(-1.0)
    assert result.observations[1].object_type == "ship"
    assert result.observations[1].bearing_sigma_deg == pytest.approx(0.4)
    assert result.observations[1].angular_half_width_deg == pytest.approx(0.3)


def test_grouping_is_independent_of_detection_input_order() -> None:
    objects = [
        _detection("c", 359.5, 9.0),
        _detection("a", 5.0, 10.0),
        _detection("b", 2.0, 5.0),
        _detection("d", 80.0, 2.0),
    ]

    forward = group_angular_detections(objects, 3.0)
    reverse = group_angular_detections(list(reversed(objects)), 3.0)

    assert forward == reverse
    assert forward.truth_groups == (("d",), ("c", "b", "a"))


def test_type_labels_can_be_hidden_while_truth_group_is_retained() -> None:
    result = group_angular_detections(
        [_detection("vessel-1", 10.0, 100.0, "ship")],
        resolution_deg=1.0,
        report_type_labels=False,
    )

    assert result.observations[0].object_type is None
    assert result.truth_groups == (("vessel-1",),)
    assert not hasattr(result.observations[0], "object_id")


def test_empty_input_returns_empty_measurements_and_truth() -> None:
    assert group_angular_detections([], 1.0).observations == ()
    assert group_angular_detections([], 1.0).truth_groups == ()


@pytest.mark.parametrize("resolution", [-0.1, 180.0, float("inf")])
def test_invalid_resolution_is_rejected(resolution: float) -> None:
    with pytest.raises(ValueError, match="resolution_deg"):
        group_angular_detections([], resolution)


def test_equal_range_tie_uses_object_id_for_determinism() -> None:
    result = group_angular_detections(
        [
            _detection("z", 10.5, 20.0, "buoy"),
            _detection("a", 10.0, 20.0, "ship"),
        ],
        resolution_deg=1.0,
    )

    assert result.observations[0].bearing_deg == pytest.approx(10.0)
    assert result.observations[0].object_type == "ship"
    assert result.truth_groups == (("a", "z"),)
