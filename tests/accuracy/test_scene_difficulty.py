"""A25 scene support and bearing-geometry diagnostics tests."""

from __future__ import annotations

from math import atan2, degrees, radians

import numpy as np
import pytest
from pyproj import Geod

from maritime.evaluation.difficulty import (
    build_support_manifest,
    evaluate_scene_difficulty,
)
from maritime.schemas.inputs_v2 import MeasurementSetV2, MeasurementV2
from maritime.schemas.truth import EvaluationTruth, TrueObject

STAMP = "2026-10-25T12:00:00Z"


def scene(
    points: tuple[tuple[float, float], ...],
    *,
    sigma: float = 0.5,
    types: tuple[str, ...] | None = None,
    add_unmatched: bool = False,
    common_bias_sigma: float | None = None,
    covariance: tuple[tuple[float, ...], ...] | None = None,
):
    types = types or tuple("aid" for _ in points)
    objects = tuple(
        TrueObject(f"target-{i}", types[i], east, north, f"catalog-{i}", i)
        for i, (east, north) in enumerate(points)
    )
    if add_unmatched:
        objects += (TrueObject("uncataloged", "ship", 0.0, 500.0, None, len(points)),)
    truth = EvaluationTruth(0.0, 0.0, 0.0, objects)
    measurements = MeasurementSetV2(
        tuple(
            MeasurementV2(
                degrees(atan2(east, north)) % 360,
                STAMP,
                sigma_deg=sigma,
                object_type=types[i],
            )
            for i, (east, north) in enumerate(points)
        ),
        calibrated_common_bias_mean_deg=0.0 if common_bias_sigma is not None else None,
        calibrated_common_bias_sigma_deg=common_bias_sigma,
        covariance_deg2=covariance,
    )
    return truth, measurements


def diagnostics():
    from types import SimpleNamespace

    groups = (
        SimpleNamespace(
            detected=True,
            observation_index=0,
            representative_object_id="a",
            representative_catalog_key="c1",
            catalog_member_keys=("c1", "c2"),
        ),
        SimpleNamespace(
            detected=True,
            observation_index=0,
            representative_object_id="merged",
            representative_catalog_key="c2",
            catalog_member_keys=("c2",),
        ),
        SimpleNamespace(
            detected=True,
            observation_index=1,
            representative_object_id="b",
            representative_catalog_key=None,
            catalog_member_keys=("c3",),
        ),
        SimpleNamespace(
            detected=False,
            observation_index=None,
            representative_object_id="miss",
            representative_catalog_key="c4",
            catalog_member_keys=("c4",),
        ),
        SimpleNamespace(
            detected=True,
            observation_index=3,
            representative_object_id="u",
            representative_catalog_key=None,
            catalog_member_keys=(),
        ),
    )
    return SimpleNamespace(
        groups=groups,
        in_range_physical_count=6,
        line_of_sight_visible_physical_count=5,
        merged_physical_group_count=4,
        detected_physical_group_count=3,
        clutter_report_count=2,
    )


def test_orthogonal_bearings_are_better_conditioned_than_parallel_bearings():
    parallel = scene(((0, 1000), (0, 2000), (0, 3000)))
    orthogonal = scene(((1000, 0), (0, 1000), (-1000, 0)))
    bad = evaluate_scene_difficulty(*parallel, valid_reference_water=True)
    good = evaluate_scene_difficulty(*orthogonal, valid_reference_water=True)

    assert bad.geometry_stratum == "singular"
    assert bad.ellipse_major_radius_95_m is None
    assert good.ellipse_major_radius_95_m is not None
    assert good.geometry_stratum != "singular"
    assert good.angular_spread_deg == pytest.approx(180.0)
    assert good.largest_angular_gap_deg == pytest.approx(180.0)


def test_fisher_information_matches_finite_difference_global_bearing_jacobian():
    truth, measurements = scene(((700, 900), (-1100, 500), (300, -1300)), sigma=0.7)
    result = evaluate_scene_difficulty(truth, measurements, valid_reference_water=True)
    assert result.fisher_information_m_inv2 is not None
    got = np.asarray(result.fisher_information_m_inv2)
    step = 1e-3
    expected = np.zeros((2, 2))
    sigma_rad = radians(0.7)
    for obj in truth.objects:

        def bearing(east_observer: float, north_observer: float) -> float:
            return atan2(obj.east_m - east_observer, obj.north_m - north_observer)

        j = np.array(
            [
                (bearing(step, 0) - bearing(-step, 0)) / (2 * step),
                (bearing(0, step) - bearing(0, -step)) / (2 * step),
            ]
        )
        expected += np.outer(j, j) / sigma_rad**2
    np.testing.assert_allclose(got, expected, rtol=1e-8, atol=1e-10)


def test_angular_geometry_uses_truth_bearings_not_measurement_noise():
    truth, measurements = scene(((1000, 0), (0, 1000), (-1000, 0)))
    shifted = MeasurementSetV2(
        tuple(
            MeasurementV2(
                (measurement.bearing_deg + offset) % 360.0,
                measurement.timestamp_utc,
                sigma_deg=measurement.sigma_deg,
                object_type=measurement.object_type,
            )
            for measurement, offset in zip(measurements.measurements, (10.0, -5.0, 25.0))
        )
    )
    baseline = evaluate_scene_difficulty(truth, measurements, valid_reference_water=True)
    perturbed = evaluate_scene_difficulty(truth, shifted, valid_reference_water=True)

    assert perturbed.angular_spread_deg == baseline.angular_spread_deg
    assert perturbed.largest_angular_gap_deg == baseline.largest_angular_gap_deg


def test_more_range_or_noise_increases_uncertainty():
    close = scene(((500, 0), (0, 500), (-500, 0)), sigma=0.5)
    far = scene(((5000, 0), (0, 5000), (-5000, 0)), sigma=0.5)
    noisy = scene(((500, 0), (0, 500), (-500, 0)), sigma=1.0)
    close_r = evaluate_scene_difficulty(
        *close, valid_reference_water=True
    ).ellipse_major_radius_95_m
    far_r = evaluate_scene_difficulty(*far, valid_reference_water=True).ellipse_major_radius_95_m
    noisy_r = evaluate_scene_difficulty(
        *noisy, valid_reference_water=True
    ).ellipse_major_radius_95_m

    assert close_r is not None and far_r is not None and noisy_r is not None
    assert far_r == pytest.approx(close_r * 10)
    assert noisy_r == pytest.approx(close_r * 2)


def test_common_bias_nuisance_prior_weakens_position_information():
    points = ((1000, 0), (0, 1000), (-1000, 500))
    known = scene(points)
    uncertain_bias = scene(points, common_bias_sigma=2.0)
    known_radius = evaluate_scene_difficulty(
        *known, valid_reference_water=True
    ).ellipse_major_radius_95_m
    bias_radius = evaluate_scene_difficulty(
        *uncertain_bias, valid_reference_water=True
    ).ellipse_major_radius_95_m

    assert known_radius is not None and bias_radius is not None
    assert bias_radius > known_radius


def test_postmerge_groups_are_deduplicated_and_truth_isolation_is_preserved():
    truth, measurements = scene(((1000, 0), (0, 1000), (-1000, 0)), add_unmatched=True)
    diag = diagnostics()
    manifest = build_support_manifest(True, diag, port_id="p1", placement_id="x1")
    result = evaluate_scene_difficulty(
        truth,
        measurements,
        diag,
        valid_reference_water=True,
        competing_mode_scores=(12.0, 14.5, 17.0),
        port_id="p1",
        placement_id="x1",
    )

    assert manifest.detected_physical_catalog_matched_groups == 1
    assert not manifest.supported
    assert result.support_manifest == manifest
    assert result.matched_group_count == 3
    assert result.support_manifest.detected_physical_catalog_matched_groups == 1
    assert result.missed_physical_group_count == 1
    assert result.clutter_report_count == 2
    assert result.merged_physical_group_count == 4
    assert result.merge_member_excess_count == 1
    assert result.competing_mode_gap == pytest.approx(2.5)
    assert result.distinct_type_count == 1


def test_measurement_covariance_is_used_in_information():
    points = ((1000, 0), (0, 1000), (-1000, 0))
    independent = scene(points, sigma=0.5)
    covariance = scene(
        points,
        sigma=0.5,
        covariance=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    )
    independent_radius = evaluate_scene_difficulty(
        *independent, valid_reference_water=True
    ).ellipse_major_radius_95_m
    covariance_radius = evaluate_scene_difficulty(
        *covariance, valid_reference_water=True
    ).ellipse_major_radius_95_m

    assert independent_radius is not None and covariance_radius is not None
    assert covariance_radius == pytest.approx(independent_radius * 2)


def test_singular_geometry_remains_supported_with_three_detected_catalog_groups():
    from types import SimpleNamespace

    groups = tuple(
        SimpleNamespace(
            detected=True,
            observation_index=index,
            representative_object_id=f"g{index}",
            representative_catalog_key=f"c{index}",
            catalog_member_keys=(f"c{index}",),
        )
        for index in range(3)
    )
    diag = SimpleNamespace(
        groups=groups,
        clutter_report_count=0,
        merged_physical_group_count=3,
        line_of_sight_visible_physical_count=3,
    )
    truth, measurements = scene(((0, 1000), (0, 2000), (0, 3000)))
    result = evaluate_scene_difficulty(truth, measurements, diag, valid_reference_water=True)

    assert result.geometry_stratum == "singular"
    assert result.support_manifest.supported
    assert result.support_manifest.detected_physical_catalog_matched_groups == 3


def test_global_bearing_jacobian_agrees_with_wgs84_geodesic_reference():
    geod = Geod(ellps="WGS84")
    origin = (-76.3, 37.0)
    target_bearings = (35.0, 145.0, 260.0)
    target_ranges = (2000.0, 4000.0, 6000.0)
    objects = []
    measurements = []
    target_lonlats = []
    for index, (azimuth, distance) in enumerate(zip(target_bearings, target_ranges)):
        lon, lat, _ = geod.fwd(*origin, azimuth, distance)
        target_lonlats.append((lon, lat))
        angle = radians(azimuth)
        objects.append(
            TrueObject(
                f"g{index}",
                "aid",
                distance * np.sin(angle),
                distance * np.cos(angle),
                f"c{index}",
                index,
            )
        )
        measurements.append(MeasurementV2(azimuth, STAMP, sigma_deg=0.5))
    truth = EvaluationTruth(0.0, 0.0, 0.0, tuple(objects))
    reports = MeasurementSetV2(tuple(measurements))
    result = evaluate_scene_difficulty(truth, reports, valid_reference_water=True)
    assert result.fisher_information_m_inv2 is not None
    got = np.asarray(result.fisher_information_m_inv2)

    def wrap_radians(value: float) -> float:
        return (value + np.pi) % (2 * np.pi) - np.pi

    step_m = 1.0
    expected = np.zeros((2, 2))
    sigma_rad = radians(0.5)
    for target in target_lonlats:
        jacobian = []
        for plus_azimuth, minus_azimuth in ((90.0, 270.0), (0.0, 180.0)):
            plus = geod.fwd(*origin, plus_azimuth, step_m)[:2]
            minus = geod.fwd(*origin, minus_azimuth, step_m)[:2]
            plus_bearing = radians(geod.inv(*plus, *target)[0])
            minus_bearing = radians(geod.inv(*minus, *target)[0])
            jacobian.append(wrap_radians(plus_bearing - minus_bearing) / (2 * step_m))
        vector = np.asarray(jacobian)
        expected += np.outer(vector, vector) / sigma_rad**2

    np.testing.assert_allclose(got, expected, rtol=5e-4, atol=1e-10)
