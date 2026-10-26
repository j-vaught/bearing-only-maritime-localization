from __future__ import annotations

from math import isclose

from pyproj import CRS, Geod, Transformer
from shapely.geometry import box

from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime.schemas.localization import Hypothesis
from maritime.search.config import SearchConfig
from maritime.search.reference_grid import (
    CandidateCounts,
    enumerate_grid_points,
    grid_shape,
    search_reference_grid,
)
from maritime.search.scoring import CandidateScorer

CRS_UTM = CRS.from_epsg(32618)
_TO_XY = Transformer.from_crs("EPSG:4326", CRS_UTM, always_xy=True)
_GEOD = Geod(ellps="WGS84")
_ORIGIN = (-76.3, 37.0)


def _xy():
    east, north = _TO_XY.transform(*_ORIGIN)
    return float(east), float(north)


def _catalog(*records):
    return CatalogV2(
        records=tuple(records),
        timestamp_utc="2026-10-25T12:00:00Z",
        source="test",
        catalog_version="1",
        catalog_id="a18",
    )


def _measurements():
    return MeasurementSetV2((MeasurementV2(0.0, "2026-10-25T12:00:00Z", 0.5, "vessel"),))


def _map(*, land=None, coverage=None):
    if coverage is None:
        x, y = _xy()
        coverage = box(x - 20_000, y - 20_000, x + 20_000, y + 20_000)
    return VectorMap(land, crs=CRS_UTM, coverage_geometry=coverage)


def _record(key, *, metadata=()):
    lon, lat, _ = _GEOD.fwd(*_ORIGIN, 0.0, 1_000.0)
    return CatalogRecordV2(
        key=key, longitude_deg=lon, latitude_deg=lat, normalized_type="vessel", metadata=metadata
    )


def test_grid_points_are_lazy_ordered_and_include_both_max_edges():
    points = enumerate_grid_points((-1.0, 1.5, 2.0, 4.0), 1.0)
    assert iter(points) is points
    assert list(points) == [(e, n) for e in (-1.0, 0.0, 1.0, 1.5) for n in (2.0, 3.0, 4.0)]
    assert grid_shape((-1.0, 1.5, 2.0, 4.0), 1.0) == (4, 3)
    assert list(enumerate_grid_points((0.0, 2.0, 4.0, 6.0), 1.0)) == [
        (e, n) for e in (0.0, 1.0, 2.0) for n in (4.0, 5.0, 6.0)
    ]


def test_full_grid_matches_direct_scorer_and_counts_all_points():
    x, y = _xy()
    bounds = (x - 1.0, x + 1.0, y - 1.0, y + 1.0)
    config = SearchConfig(bounds_xy=bounds, max_range_m=5_000.0, candidate_budget=20)
    measurements, catalog, vector_map = _measurements(), _catalog(_record("north")), _map()
    points = tuple(enumerate_grid_points(bounds, 1.0))
    direct = [
        CandidateScorer(measurements, catalog, vector_map, config).evaluate(p) for p in points
    ]
    result = search_reference_grid(measurements, catalog, vector_map, config, spacing_m=1.0)
    assert result.visited_points == points
    assert result.candidate_counts == CandidateCounts(9, 9, 9, 0, 0, 0, 0)
    assert result.search_status == "completed"
    assert result.certificate_scope == "finite_candidate_set"
    assert result.decision_status == "ambiguous"
    assert result.localization_result.accepted_index is None
    assert all(
        isclose(h.score, min(e.hypothesis.score for e in direct if e.hypothesis))
        for h in result.hypotheses
    )


def test_tied_spatial_modes_preserved_without_acceptance(monkeypatch):
    import maritime.search.reference_grid as module

    class FakeScorer:
        def __init__(self, *_args):
            pass

        def evaluate(self, point):
            score = 0.0 if point in ((0.0, 0.0), (2.0, 2.0)) else 10.0
            return type(
                "Outcome",
                (),
                {"status": "scored", "hypothesis": Hypothesis(point[0], point[1], score)},
            )()

    monkeypatch.setattr(module, "CandidateScorer", FakeScorer)
    result = search_reference_grid(
        _measurements(),
        _catalog(),
        _map(),
        SearchConfig(bounds_xy=(0.0, 2.0, 0.0, 2.0), candidate_budget=9),
        spacing_m=1.0,
    )
    assert len(result.modes) == 2
    assert {(h.east_m, h.north_m) for h in result.hypotheses} == {(0.0, 0.0), (2.0, 2.0)}
    assert result.decision_status == "ambiguous"
    assert result.localization_result.accepted_index is None


def test_water_invalid_and_unknown_coverage_are_counted_without_certificate():
    x, y = _xy()
    bounds = (x - 1.0, x + 1.0, y - 1.0, y + 1.0)
    land = box(x - 0.5, y - 0.5, x + 0.5, y + 0.5)
    coverage = box(x - 0.5, y - 10.0, x + 0.5, y + 10.0)
    result = search_reference_grid(
        _measurements(),
        _catalog(),
        _map(land=land, coverage=coverage),
        SearchConfig(bounds_xy=bounds, candidate_budget=9),
        spacing_m=1.0,
    )
    assert result.candidate_counts.total == 9
    assert result.candidate_counts.invalid_water == 1
    assert result.candidate_counts.unresolved_coverage == 6
    assert result.certificate_scope == "none"


def test_large_grid_budget_is_bounded_and_reports_unvisited_points():
    bounds = (0.0, 1_000_000.0, 0.0, 1_000_000.0)
    result = search_reference_grid(
        _measurements(),
        _catalog(),
        _map(coverage=box(-5.0, -5.0, -1.0, -1.0)),
        SearchConfig(bounds_xy=bounds, candidate_budget=5),
        spacing_m=1.0,
    )
    assert result.grid_shape == (1_000_001, 1_000_001)
    assert result.candidate_counts.total == 1_000_002_000_001
    assert result.candidate_counts.evaluated == 5
    assert result.candidate_counts.unevaluated == result.candidate_counts.total - 5
    assert len(result.visited_points) == 5
    assert result.search_status == "budget_exhausted"
    assert result.certificate_scope == "none"
    assert result.decision_status == "no_fix"


def test_unresolved_model_is_counted_and_prevents_certificate():
    x, y = _xy()
    config = SearchConfig(bounds_xy=(x - 0.1, x + 0.1, y - 0.1, y + 0.1), max_range_m=5_000.0)
    result = search_reference_grid(
        _measurements(),
        _catalog(_record("unsupported", metadata=(("vessel_length_m", 40.0),))),
        _map(),
        config,
        spacing_m=1.0,
    )
    assert result.candidate_counts.unresolved_model == 4
    assert result.candidate_counts.scored == 0
    assert result.certificate_scope == "none"
    assert result.decision_status == "no_fix"
