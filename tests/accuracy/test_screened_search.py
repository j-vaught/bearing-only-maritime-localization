from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

import pytest
from pyproj import CRS, Transformer
from shapely.geometry import box

from accuracy.test_visibility_screen import GEOD, catalog, record, vmap, xy
from bearing_localization.search.config import SearchConfig
from bearing_localization.search.scoring import CandidateScorer
from bearing_localization.search.screened_search import (
    CandidatePosition,
    FeasibleIncumbent,
    screened_candidate_search,
)
from maritime_localization_shared.contracts.inputs_v2 import (
    CatalogRecordV2,
    MeasurementSetV2,
    MeasurementV2,
)

CRS_UTM = CRS.from_epsg(32618)
TO_LONLAT = Transformer.from_crs(CRS_UTM, "EPSG:4326", always_xy=True)
ORIGIN_LONLAT = (-76.3, 37.0)
TIMESTAMP = datetime(2026, 10, 26, tzinfo=UTC).isoformat()


def _record_at_xy(key: str, east: float, north: float, object_type: str) -> CatalogRecordV2:
    lon, lat = TO_LONLAT.transform(east, north)
    return CatalogRecordV2(
        key=key,
        longitude_deg=float(lon),
        latitude_deg=float(lat),
        normalized_type=object_type,
    )


def _scenario(use_types: bool):
    x, y = xy()
    observer_b = (x + 6_000.0, y)
    targets = [record("a-target", 0.0, 900.0)]
    lon_b, lat_b = TO_LONLAT.transform(*observer_b)
    bearings = (45.0, 45.06, 45.12, 100.0, 140.0, 180.0, 220.0, 260.0, 300.0, 340.0)
    for index, bearing in enumerate(bearings):
        lon, lat, _ = GEOD.fwd(lon_b, lat_b, bearing, 800.0)
        object_type = "vessel" if index % 2 == 0 else "physical_aid"
        targets.append(
            CatalogRecordV2(
                key=f"b-target-{index}",
                longitude_deg=lon,
                latitude_deg=lat,
                normalized_type=object_type,
            )
        )
    cat = catalog(*targets)
    land = box(x + 2_950.0, y - 50.0, x + 3_050.0, y + 50.0)
    unknown = box(x - 2_050.0, y - 50.0, x - 1_950.0, y + 50.0)
    vector_map = vmap(land=land, unknown=unknown)
    measurements = MeasurementSetV2(
        (MeasurementV2(0.0, TIMESTAMP, 0.05, "vessel"),),
        trial_id="screened-a22",
    )
    config = SearchConfig(
        bounds_xy=(x - 10_000.0, x + 10_000.0, y - 5_000.0, y + 5_000.0),
        min_range_m=0.0,
        max_range_m=4_500.0,
        circular_merge_tolerance_deg=0.1,
        use_types=use_types,
        candidate_budget=100,
    )
    scorer = CandidateScorer(measurements, cat, vector_map, config)
    points = (
        CandidatePosition("incumbent", xy()),
        CandidatePosition("busy-bridge-groups", observer_b),
        CandidatePosition("land", (x + 3_000.0, y)),
        CandidatePosition("unknown", (x - 2_000.0, y)),
        CandidatePosition("remote-water", (x + 9_000.0, y)),
    )
    return scorer, points, observer_b


@pytest.mark.parametrize("use_types", [False, True])
def test_screened_finite_search_matches_full_exact_winners_and_ties(use_types, monkeypatch):
    import bearing_localization.search.screened_search as search_module

    scorer, points, observer_b = _scenario(use_types)
    screen_calls = []
    original_screen = search_module.screen_candidate_visibility

    def counted_screen(*args, **kwargs):
        screen_calls.append(kwargs)
        return original_screen(*args, **kwargs)

    monkeypatch.setattr(search_module, "screen_candidate_visibility", counted_screen)
    direct = [scorer.evaluate(point.position_xy_m) for point in points]
    result = screened_candidate_search(
        scorer,
        points,
        visit_budget=len(points),
        complete_candidate_count=len(points),
    )

    assert result.search_status == "completed"
    assert result.certificate_scope == "none"
    assert result.candidate_count == len(points)
    assert result.pruned_count >= 1
    assert result.invalid_water_count == 1
    assert result.unresolved_count == 1
    assert result.exact_evaluation_count + result.pruned_count == len(points)

    exact_hypotheses = [
        item.hypothesis
        for item in direct
        if item.status == "scored" and item.hypothesis is not None
    ]
    expected_best = min(item.score for item in exact_hypotheses)
    actual_best = min(item.score for item in result.hypotheses)
    assert actual_best == pytest.approx(expected_best, abs=1e-12)
    expected_ties = {
        (item.east_m, item.north_m)
        for item in exact_hypotheses
        if abs(item.score - expected_best) <= 1e-8
    }
    actual_ties = {
        (item.east_m, item.north_m)
        for item in result.hypotheses
        if abs(item.score - actual_best) <= 1e-8
    }
    assert actual_ties == expected_ties

    busy_visit = next(item for item in result.visits if item.candidate_id == "busy-bridge-groups")
    busy_exact = direct[1]
    assert busy_visit.action == "pruned"
    assert len(screen_calls) == 2
    assert all(call["scan_all_members"] is True for call in screen_calls)
    assert busy_visit.visibility is not None
    assert busy_visit.visibility.minimum_visible_group_count <= len(busy_exact.predicted.groups)
    assert busy_visit.visibility.ray_check_count < len(scorer._prediction_context.records)
    assert busy_visit.lower_bound is not None and busy_visit.lower_bound.bound is not None
    assert busy_exact.hypothesis is not None
    assert busy_visit.lower_bound.bound <= busy_exact.hypothesis.score + 1e-12
    assert result.best_scored_candidate is not None
    assert result.best_scored_candidate.score < 0.0


def test_first_candidate_is_exact_scored_without_an_incumbent_and_no_float_incumbent():
    scorer, points, _observer_b = _scenario(True)
    first = screened_candidate_search(
        scorer,
        points[:1],
        visit_budget=1,
        complete_candidate_count=1,
    )
    assert first.visits[0].candidate_id == "incumbent"
    assert first.visits[0].action == "exact_scored"
    assert first.visits[0].visibility is None
    assert first.pruned_count == 0

    with pytest.raises(TypeError, match="FeasibleIncumbent"):
        screened_candidate_search(
            scorer, points, visit_budget=1, feasible_incumbent=cast(FeasibleIncumbent, -1000.0)
        )


def test_exact_incumbent_is_revalidated_by_the_receiving_scorer():
    scorer, points, _observer_b = _scenario(True)
    first = screened_candidate_search(
        scorer,
        points[:1],
        visit_budget=1,
        complete_candidate_count=1,
    )
    assert first.best_scored_candidate is not None
    resumed = screened_candidate_search(
        scorer,
        points[1:],
        visit_budget=len(points) - 1,
        complete_candidate_count=len(points) - 1,
        feasible_incumbent=first.best_scored_candidate,
    )
    assert resumed.incumbent_validation_count == 1
    assert resumed.best_scored_candidate is not None
    assert resumed.best_scored_candidate.candidate_id == "incumbent"
    assert resumed.best_scored_candidate.score == pytest.approx(
        first.best_scored_candidate.score, abs=1e-12
    )
    other_scorer, _, _ = _scenario(True)
    revalidated = screened_candidate_search(
        other_scorer,
        points[1:],
        visit_budget=len(points) - 1,
        feasible_incumbent=first.best_scored_candidate,
    )
    assert revalidated.incumbent_validation_count == 1
    assert revalidated.best_scored_candidate is not None
    assert revalidated.best_scored_candidate.score == pytest.approx(
        other_scorer.evaluate(points[0].position_xy_m).hypothesis.score,
        abs=1e-12,
    )


def test_visit_budget_reports_unvisited_candidates_and_withholds_certificate():
    scorer, points, _observer_b = _scenario(True)
    result = screened_candidate_search(
        scorer,
        points,
        visit_budget=2,
        complete_candidate_count=len(points),
    )
    assert result.search_status == "budget_exhausted"
    assert result.certificate_scope == "none"
    assert result.localization.decision_status == "ambiguous"
    assert result.unvisited_count == 3
    assert result.unvisited_candidate_ids == ("land", "unknown", "remote-water")
    assert result.candidate_count == result.localization.budget_used == 2
    assert result.localization.budget_limit == 2


def test_equal_score_candidates_remain_as_distinct_ranked_ties():
    x, y = xy()
    scorer = CandidateScorer(
        MeasurementSetV2((MeasurementV2(0.0, TIMESTAMP, 0.5, "vessel"),)),
        catalog(),
        vmap(),
        SearchConfig(bounds_xy=(x - 1_000, x + 1_000, y - 1_000, y + 1_000)),
    )
    candidates = (
        CandidatePosition("left", (x - 500, y)),
        CandidatePosition("right", (x + 500, y)),
    )
    result = screened_candidate_search(
        scorer,
        candidates,
        visit_budget=2,
        complete_candidate_count=2,
    )
    assert result.certificate_scope == "finite_candidate_set"
    assert len(result.hypotheses) == 2
    assert result.hypotheses[0].score == result.hypotheses[1].score


def test_uncertified_or_unsupported_visibility_falls_back_to_exact_scoring():
    scorer, points, _observer_b = _scenario(True)
    unknown = scorer.evaluate(points[3].position_xy_m)
    result = screened_candidate_search(
        scorer,
        (points[0], points[3]),
        visit_budget=2,
        complete_candidate_count=2,
    )
    visit = result.visits[1]
    assert visit.action == "exact_unresolved"
    assert visit.visibility is None
    assert visit.evaluation_status == unknown.status == "unresolved_coverage"
    assert result.certificate_scope == "none"


def test_unsupported_objective_bound_falls_back_to_exact_scoring():
    x, y = xy()
    scorer = CandidateScorer(
        MeasurementSetV2((MeasurementV2(0.0, TIMESTAMP, 0.5, "vessel"),)),
        catalog(),
        vmap(),
        SearchConfig(
            bounds_xy=(x - 1_000, x + 1_000, y - 1_000, y + 1_000),
            objective_version="legacy_corrected_v0",
        ),
    )
    candidates = (CandidatePosition("legacy", (x, y)),)
    result = screened_candidate_search(
        scorer,
        candidates,
        visit_budget=1,
        complete_candidate_count=1,
    )
    assert result.pruned_count == 0
    assert result.visits[0].action == "exact_scored"
    assert result.certificate_scope == "finite_candidate_set"


def test_unknown_length_iterator_reports_partial_remaining_count_without_certificate():
    scorer, points, _observer_b = _scenario(True)
    result = screened_candidate_search(
        scorer,
        iter(points),
        visit_budget=1,
    )
    assert result.search_status == "budget_exhausted"
    assert result.certificate_scope == "none"
    assert result.unvisited_count is None
    assert result.first_unvisited_candidate_id == "busy-bridge-groups"
    assert result.unvisited_candidate_ids == ("busy-bridge-groups",)
    assert list(result.remaining_candidates) == list(points[1:])


def test_declared_candidate_count_must_match_consumed_finite_stream():
    scorer, points, _observer_b = _scenario(True)
    with pytest.raises(ValueError, match="stream length does not match"):
        screened_candidate_search(
            scorer,
            iter(points[:1]),
            visit_budget=2,
            complete_candidate_count=2,
        )


def test_feasible_incumbents_cannot_be_fabricated_from_a_score_object():
    scorer, points, _observer_b = _scenario(True)
    evaluation = scorer.evaluate(points[0].position_xy_m)
    constructor = cast(Callable[..., FeasibleIncumbent], FeasibleIncumbent)
    with pytest.raises(TypeError):
        constructor(
            "forged",
            points[0].position_xy_m,
            evaluation,
            scorer,
        )


def test_early_group_bound_cannot_hide_unknown_los_members():
    x, y = xy()
    candidate_lon, candidate_lat = ORIGIN_LONLAT
    target_records = []
    for index, bearing in enumerate((0, 30, 60, 90, 120, 150, 180, 210, 240)):
        lon, lat, _ = GEOD.fwd(candidate_lon, candidate_lat, bearing, 800.0)
        target_records.append(
            CatalogRecordV2(
                key=f"visible-{index}",
                longitude_deg=lon,
                latitude_deg=lat,
                normalized_type="vessel",
            )
        )
    near_lon, near_lat, _ = GEOD.fwd(candidate_lon, candidate_lat, 350.0, 800.0)
    far_lon, far_lat, _ = GEOD.fwd(candidate_lon, candidate_lat, 350.0, 1_600.0)
    target_records.extend(
        (
            CatalogRecordV2(
                key="bridge-near",
                longitude_deg=near_lon,
                latitude_deg=near_lat,
                normalized_type="vessel",
            ),
            CatalogRecordV2(
                key="bridge-far",
                longitude_deg=far_lon,
                latitude_deg=far_lat,
                normalized_type="vessel",
            ),
        )
    )
    incumbent_target = _record_at_xy("incumbent-target", x + 7_000.0, y + 800.0, "vessel")
    catalog_data = catalog(*target_records, incumbent_target)
    unknown_lon, unknown_lat, _ = GEOD.fwd(candidate_lon, candidate_lat, 350.0, 1_150.0)
    unknown_x, unknown_y = Transformer.from_crs("EPSG:4326", CRS_UTM, always_xy=True).transform(
        unknown_lon, unknown_lat
    )
    unknown_region = box(
        float(unknown_x) - 60.0,
        float(unknown_y) - 60.0,
        float(unknown_x) + 60.0,
        float(unknown_y) + 60.0,
    )
    vector_map = vmap(unknown=unknown_region)
    measurements = MeasurementSetV2(
        (MeasurementV2(0.0, TIMESTAMP, 0.02, "vessel"),),
        trial_id="a22-hidden-unknown-los",
    )
    config = SearchConfig(
        bounds_xy=(x - 10_000.0, x + 10_000.0, y - 5_000.0, y + 5_000.0),
        min_range_m=0.0,
        max_range_m=4_500.0,
        circular_merge_tolerance_deg=0.1,
        use_types=True,
        candidate_budget=100,
    )
    scorer = CandidateScorer(measurements, catalog_data, vector_map, config)
    candidates = (
        CandidatePosition("incumbent", (x + 7_000.0, y)),
        CandidatePosition("early-k-bridge-unknown", (x, y)),
    )
    result = screened_candidate_search(
        scorer,
        candidates,
        visit_budget=2,
        complete_candidate_count=2,
    )

    candidate_visit = result.visits[1]
    assert candidate_visit.action == "exact_unresolved"
    assert candidate_visit.evaluation_status == "unresolved_coverage"
    assert candidate_visit.visibility is not None
    assert not candidate_visit.visibility.certified
    assert candidate_visit.visibility.reason == "unknown_line_of_sight"
    assert result.pruned_count == 0
    assert result.unresolved_count == 1
    assert result.certificate_scope == "none"


def test_land_and_unknown_candidates_use_exact_water_status_without_visibility_screen(monkeypatch):
    import bearing_localization.search.screened_search as search_module

    scorer, points, _observer_b = _scenario(True)

    def unexpected_screen(*_args, **_kwargs):
        pytest.fail("land and unknown-water candidates must bypass visibility screening")

    monkeypatch.setattr(search_module, "screen_candidate_visibility", unexpected_screen)
    result = screened_candidate_search(
        scorer,
        (points[0], points[2], points[3]),
        visit_budget=3,
        complete_candidate_count=3,
    )

    assert tuple(visit.action for visit in result.visits) == (
        "exact_scored",
        "exact_invalid_water",
        "exact_unresolved",
    )
    assert tuple(visit.evaluation_status for visit in result.visits) == (
        "scored",
        "invalid_water",
        "unresolved_coverage",
    )
    assert result.exact_evaluation_count == 3
    assert result.exact_scored_count == 1
    assert result.invalid_water_count == 1
    assert result.unresolved_count == 1
    assert result.pruned_count == 0
    assert result.certificate_scope == "none"
