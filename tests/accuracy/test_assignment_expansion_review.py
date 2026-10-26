"""Independent adversarial checks for bounded assignment expansion.

These tests use small synthetic geographic scenes and exercise only the
estimator contracts. They deliberately avoid simulator truth or accepted
fixes; the seed is always obtained from the canonical scorer itself.
"""

from __future__ import annotations

from dataclasses import replace
from math import atan2, degrees, hypot
from typing import cast

import numpy as np
import pytest
from bearing_localization.search.assignment_expansion import (
    ExpansionConfig,
    expand_assignments,
)
from pyproj import Geod, Transformer
from shapely.geometry import box

from bearing_localization.search.config import SearchConfig
from bearing_localization.search.scoring import CandidateScorer
from maritime_localization_shared.contracts.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime_localization_shared.contracts.localization import Assignment, Hypothesis
from maritime_localization_shared.geospatial.vector_map import VectorMap

_GEOD = Geod(ellps="WGS84")
_ORIGIN_LON = -75.0
_ORIGIN_LAT = 40.0
_CRS = "EPSG:32618"
_TIME = "2026-10-26T12:00:00Z"
_FORWARD = Transformer.from_crs("EPSG:4326", _CRS, always_xy=True)
_BASE_EAST, _BASE_NORTH = (float(value) for value in _FORWARD.transform(_ORIGIN_LON, _ORIGIN_LAT))


def _lonlat(east_m: float, north_m: float) -> tuple[float, float]:
    distance = hypot(east_m, north_m)
    bearing = degrees(atan2(east_m, north_m)) % 360.0
    lon, lat, _ = _GEOD.fwd(_ORIGIN_LON, _ORIGIN_LAT, bearing, distance)
    return float(lon), float(lat)


def _bearing_to(east_m: float, north_m: float) -> float:
    lon, lat = _lonlat(east_m, north_m)
    bearing, _, _ = _GEOD.inv(_ORIGIN_LON, _ORIGIN_LAT, lon, lat)
    return float(bearing % 360.0)


def _record(key: str, east_m: float, north_m: float, **metadata: object) -> CatalogRecordV2:
    lon, lat = _lonlat(east_m, north_m)
    return CatalogRecordV2(
        key=key,
        latitude_deg=lat,
        longitude_deg=lon,
        normalized_type="vessel",
        metadata=tuple(metadata.items()),
    )


def _catalog(records: tuple[CatalogRecordV2, ...]) -> CatalogV2:
    return CatalogV2(
        records=records,
        timestamp_utc=_TIME,
        source="synthetic-review",
        catalog_version="review-v1",
        catalog_id="assignment-expansion-review",
    )


def _measurements(*bearings: float, sigmas: tuple[float, ...] | None = None) -> MeasurementSetV2:
    values = tuple(0.5 for _ in bearings) if sigmas is None else sigmas
    return MeasurementSetV2(
        measurements=tuple(
            MeasurementV2(
                bearing_deg=float(bearing % 360.0),
                timestamp_utc=_TIME,
                sigma_deg=sigma,
            )
            for bearing, sigma in zip(bearings, values, strict=True)
        ),
        trial_id="assignment-expansion-review",
    )


def _scorer(
    records: tuple[CatalogRecordV2, ...],
    measurements: MeasurementSetV2,
    *,
    bounds: tuple[float, float, float, float] = (
        _BASE_EAST - 5_000.0,
        _BASE_EAST + 5_000.0,
        _BASE_NORTH - 5_000.0,
        _BASE_NORTH + 5_000.0,
    ),
) -> CandidateScorer:
    vector_map = VectorMap(
        None,
        crs=_CRS,
        coverage_geometry=box(
            _BASE_EAST - 30_000.0,
            _BASE_NORTH - 30_000.0,
            _BASE_EAST + 30_000.0,
            _BASE_NORTH + 30_000.0,
        ),
    )
    config = SearchConfig(
        bounds_xy=bounds,
        max_range_m=20_000.0,
        min_range_m=10.0,
        circular_merge_tolerance_deg=0.1,
    )
    return CandidateScorer(measurements, _catalog(records), vector_map, config)


def _seed(scorer: CandidateScorer) -> Hypothesis:
    evaluation = scorer.evaluate((_BASE_EAST, _BASE_NORTH))
    assert evaluation.status == "scored"
    assert evaluation.hypothesis is not None
    return evaluation.hypothesis


def _branch_scene() -> tuple[CandidateScorer, Hypothesis]:
    records = (
        _record("assigned-a", 0.0, 6_000.0),
        _record("assigned-b", 6_000.0, 0.0),
        _record("unused-a", 4_200.0, 4_200.0),
        _record("unused-b", -4_200.0, -4_200.0),
    )
    measurements = _measurements(
        _bearing_to(0.0, 6_000.0),
        _bearing_to(6_000.0, 0.0),
        30.0,
    )
    scorer = _scorer(records, measurements)
    return scorer, _seed(scorer)


def test_stale_seed_with_different_assignment_set_is_rejected() -> None:
    scorer, seed = _branch_scene()
    assert seed.assignments
    stale = replace(seed, assignments=(Assignment(0, "wrong-key"), seed.assignments[1]))

    result = expand_assignments(scorer, stale, config=ExpansionConfig(max_branches=1))

    assert result.status == "stale_hypothesis"
    assert result.best_hypothesis == stale
    assert not result.completed


def test_unsupported_covariance_is_explicit_and_has_no_branches() -> None:
    records = (
        _record("assigned-a", 0.0, 6_000.0),
        _record("assigned-b", 6_000.0, 0.0),
        _record("unused", 4_200.0, 4_200.0),
    )
    measurements = MeasurementSetV2(
        measurements=(
            MeasurementV2(_bearing_to(0.0, 6_000.0), _TIME),
            MeasurementV2(_bearing_to(6_000.0, 0.0), _TIME),
            MeasurementV2(30.0, _TIME),
        ),
        covariance_deg2=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    )
    scorer = _scorer(records, measurements)
    seed = _seed(scorer)

    result = expand_assignments(scorer, seed, config=ExpansionConfig())

    assert result.status == "unsupported_covariance"
    assert result.counters == {}
    assert not result.completed


def test_callback_runs_before_each_branch_and_propagates_exceptions() -> None:
    scorer, seed = _branch_scene()
    seen: list[dict[str, object]] = []

    def callback(event: dict[str, object]) -> None:
        seen.append(event)
        raise RuntimeError("review callback cancellation")

    with pytest.raises(RuntimeError, match="review callback cancellation"):
        expand_assignments(
            scorer,
            seed,
            config=ExpansionConfig(max_branches=1),
            progress_callback=callback,
        )

    assert len(seen) == 1
    assert seen[0]["status"] == "started"
    assert seen[0]["branch_id"] == 0


def test_branch_budget_marks_truncation_incomplete_with_exact_counts() -> None:
    scorer, seed = _branch_scene()

    result = expand_assignments(scorer, seed, config=ExpansionConfig(max_branches=1))

    assert result.status == "budget_exhausted"
    assert result.budget_exhausted
    assert not result.completed
    assert result.counters["candidate_branches"] == 2
    assert result.counters["visited_branches"] == 1
    assert result.counters["unvisited_branches"] == 1
    assert len(result.branch_events) == 1


def test_candidate_branch_order_is_key_stable_and_catalog_order_independent() -> None:
    scorer, seed = _branch_scene()
    first: list[tuple[int, str]] = []
    result = expand_assignments(
        scorer,
        seed,
        config=ExpansionConfig(max_branches=10),
        progress_callback=lambda event: first.append(
            (int(event["report_index"]), str(event["catalog_key"]))
        ),
    )
    assert result.completed
    assert first == [(2, "unused-a"), (2, "unused-b")]

    reversed_scorer = _scorer(
        tuple(reversed(scorer.catalog.records)),
        scorer.measurements,
    )
    reversed_seed = _seed(reversed_scorer)
    second: list[tuple[int, str]] = []
    reversed_result = expand_assignments(
        reversed_scorer,
        reversed_seed,
        config=ExpansionConfig(max_branches=10),
        progress_callback=lambda event: second.append(
            (int(event["report_index"]), str(event["catalog_key"]))
        ),
    )

    assert second == first
    assert result.counters["pool_digest"] == reversed_result.counters["pool_digest"]


def test_duplicate_angular_group_cannot_create_false_success_or_new_pair() -> None:
    records = (
        _record("assigned-a", 0.0, 6_000.0),
        _record("assigned-b", 6_000.0, 0.0),
        _record("duplicate", 2.0, 6_000.0),
    )
    measurements = _measurements(
        _bearing_to(0.0, 6_000.0),
        _bearing_to(6_000.0, 0.0),
        _bearing_to(2.0, 6_000.0),
    )
    scorer = _scorer(records, measurements)
    seed = _seed(scorer)
    assert len(seed.assignments) == 2

    result = expand_assignments(scorer, seed, config=ExpansionConfig(max_branches=2))

    assert result.completed
    assert result.best_score >= result.original_score
    assert len(result.branch_events) == 1
    event = result.branch_events[0]
    assert event["catalog_key"] == "duplicate"
    assert event["added_assignment_retained"] is False


def test_bounds_are_clipped_before_solver_and_nfev_is_forwarded(monkeypatch) -> None:
    module = __import__(
        "bearing_localization.search.assignment_expansion", fromlist=["least_squares"]
    )
    observed: dict[str, object] = {}

    class _Solve:
        x = (_BASE_EAST, _BASE_NORTH)
        status = 1
        success = True

    def fake_least_squares(residuals, x0, *, bounds, method, max_nfev):
        observed["bounds"] = bounds
        observed["max_nfev"] = max_nfev
        assert method == "trf"
        residuals(x0)
        return _Solve()

    monkeypatch.setattr(module, "least_squares", fake_least_squares)
    scorer, _ = _branch_scene()
    clipped_scorer = _scorer(
        tuple(scorer.catalog.records),
        scorer.measurements,
        bounds=(
            _BASE_EAST - 100.0,
            _BASE_EAST + 100.0,
            _BASE_NORTH - 50.0,
            _BASE_NORTH + 75.0,
        ),
    )
    seed = _seed(clipped_scorer)
    result = expand_assignments(
        clipped_scorer,
        seed,
        config=ExpansionConfig(local_radius_m=1_000.0, max_nfev=17, max_branches=1),
    )

    assert result.counters["visited_branches"] == 1
    lower, upper = cast(tuple[tuple[float, float], tuple[float, float]], observed["bounds"])
    assert tuple(lower) == pytest.approx((_BASE_EAST - 100.0, _BASE_NORTH - 50.0))
    assert tuple(upper) == pytest.approx((_BASE_EAST + 100.0, _BASE_NORTH + 75.0))
    assert observed["max_nfev"] == 17


def test_residual_fit_uses_true_north_and_report_catalog_uncertainty(monkeypatch) -> None:
    records = (
        _record("assigned-a", 0.0, 6_000.0, position_uncertainty_m=100.0),
        _record("assigned-b", 6_000.0, 0.0, position_uncertainty_m=200.0),
        _record("unused-a", 4_200.0, 4_200.0, position_uncertainty_m=300.0),
    )
    measurements = _measurements(
        _bearing_to(0.0, 6_000.0),
        _bearing_to(6_000.0, 0.0),
        30.0,
        sigmas=(0.4, 0.8, 1.2),
    )
    scorer = _scorer(records, measurements)
    seed = _seed(scorer)
    captured: dict[str, object] = {}
    module = __import__(
        "bearing_localization.search.assignment_expansion", fromlist=["least_squares"]
    )

    class _Solve:
        x = (_BASE_EAST, _BASE_NORTH)
        status = 1
        success = True

    def fake_least_squares(residuals, x0, *, bounds, method, max_nfev):
        captured["residuals"] = np.asarray(residuals(np.asarray(x0, dtype=float)))
        return _Solve()

    monkeypatch.setattr(module, "least_squares", fake_least_squares)
    result = expand_assignments(scorer, seed, config=ExpansionConfig(max_branches=1))
    assert result.counters["visited_branches"] == 1

    residuals = cast(np.ndarray, captured["residuals"])
    seed_lon, seed_lat = Transformer.from_crs(_CRS, "EPSG:4326", always_xy=True).transform(
        _BASE_EAST, _BASE_NORTH
    )
    expected: list[float] = []
    for report_index, key, uncertainty in (
        (0, "assigned-a", 100.0),
        (1, "assigned-b", 200.0),
        (2, "unused-a", 300.0),
    ):
        record = next(item for item in records if item.key == key)
        bearing, _, distance = _GEOD.inv(
            seed_lon,
            seed_lat,
            record.longitude_deg,
            record.latitude_deg,
        )
        sigma = hypot(
            measurements.measurements[report_index].sigma_deg or 0.0,
            degrees(atan2(uncertainty, max(float(distance), 1.0))),
        )
        delta = (
            bearing % 360.0 - measurements.measurements[report_index].bearing_deg + 180.0
        ) % 360.0 - 180.0
        expected.append(delta / sigma)

    np.testing.assert_allclose(residuals, expected, rtol=1e-8, atol=1e-8)


def test_expansion_keeps_exact_canonical_assignment_when_new_key_is_not_retained() -> None:
    records = (
        _record("assigned-a", 0.0, 6_000.0),
        _record("assigned-b", 6_000.0, 0.0),
        _record("duplicate", 2.0, 6_000.0),
    )
    measurements = _measurements(
        _bearing_to(0.0, 6_000.0),
        _bearing_to(6_000.0, 0.0),
        _bearing_to(2.0, 6_000.0),
    )
    scorer = _scorer(records, measurements)
    seed = _seed(scorer)
    result = expand_assignments(scorer, seed, config=ExpansionConfig(max_branches=2))
    event = result.branch_events[0]

    assert event["canonical_status"] == "scored"
    assert event["exact_score"] is not None
    assert event["added_assignment_retained"] is False
    assert result.best_hypothesis.assignments == seed.assignments


def test_result_callback_follows_branch_completion_and_preserves_event_copy() -> None:
    scorer, seed = _branch_scene()
    phases: list[tuple[str, int]] = []

    def on_start(event: dict[str, object]) -> None:
        phases.append(("started", int(event["branch_id"])))

    def on_result(event: dict[str, object]) -> None:
        phases.append(("result", int(event["branch_id"])))
        event["status"] = "review-mutation"

    result = expand_assignments(
        scorer,
        seed,
        config=ExpansionConfig(max_branches=2),
        progress_callback=on_start,
        result_callback=on_result,
    )

    assert phases == [
        ("started", 0),
        ("result", 0),
        ("started", 1),
        ("result", 1),
    ]
    assert all(event["status"] != "review-mutation" for event in result.branch_events)
