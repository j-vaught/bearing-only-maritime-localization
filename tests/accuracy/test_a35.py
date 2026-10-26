from __future__ import annotations

import pytest
from pyproj import CRS, Geod, Transformer
from shapely.geometry import box

from bearing_localization.search.config import SearchConfig
from bearing_localization.search.grid import run_grid_search_v2
from bearing_localization.search.hierarchical import run_hierarchical_search_v2
from bearing_localization.search.randomized import run_randomized_search_v2
from bearing_localization.search.scoring import CandidateScorer
from maritime_localization_shared.contracts.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime_localization_shared.geospatial.vector_map import VectorMap

from .reference_assignment import optimal_assignments

CRS_UTM = CRS.from_epsg(32618)
_TO_XY = Transformer.from_crs("EPSG:4326", CRS_UTM, always_xy=True)
_GEOD = Geod(ellps="WGS84")
_ORIGIN = (-76.3, 37.0)


def _origin_xy() -> tuple[float, float]:
    east, north = _TO_XY.transform(*_ORIGIN)
    return float(east), float(north)


def _vector_map() -> VectorMap:
    east, north = _origin_xy()
    return VectorMap(
        None,
        crs=CRS_UTM,
        coverage_geometry=box(east - 20_000, north - 20_000, east + 20_000, north + 20_000),
    )


def _record(key: str, bearing_deg: float, range_m: float) -> CatalogRecordV2:
    longitude, latitude, _ = _GEOD.fwd(*_ORIGIN, bearing_deg, range_m)
    return CatalogRecordV2(
        key=key,
        longitude_deg=float(longitude),
        latitude_deg=float(latitude),
        normalized_type="vessel",
    )


def _catalog(*records: CatalogRecordV2) -> CatalogV2:
    return CatalogV2(
        records=tuple(records),
        timestamp_utc="2026-10-25T12:00:00Z",
        source="integration-test",
        catalog_version="1",
        catalog_id="a35",
    )


def _measurements(*bearings_and_sigmas: tuple[float, float]) -> MeasurementSetV2:
    return MeasurementSetV2(
        tuple(
            MeasurementV2(bearing, "2026-10-25T12:00:00Z", sigma, "vessel")
            for bearing, sigma in bearings_and_sigmas
        )
    )


def test_exact_assignment_and_fixed_candidate_objective_parity() -> None:
    measurements = _measurements((0.0, 0.1), (90.0, 0.5), (180.0, 2.0))
    catalog = _catalog(
        _record("north", 0.0, 1_000.0),
        _record("east", 90.0, 1_500.0),
        _record("south", 180.0, 2_000.0),
    )
    scorer = CandidateScorer(
        measurements,
        catalog,
        _vector_map(),
        SearchConfig(max_range_m=5_000.0, use_types=False),
    )
    position = _origin_xy()
    expected = scorer.evaluate(position)
    assert expected.status == "scored"
    assert expected.costs is not None and expected.hypothesis is not None
    oracle = optimal_assignments(
        expected.costs.matched_costs.tolist(),
        expected.costs.report_clutter_costs.tolist(),
        expected.costs.group_miss_costs.tolist(),
    )
    assert oracle
    oracle_total = expected.costs.baseline_constant + oracle[0].cost
    assert expected.hypothesis.score == pytest.approx(oracle_total, abs=1e-8, rel=1e-10)
    assert len({item.sigma_deg for item in measurements.measurements}) == 3

    runs = (
        run_grid_search_v2(scorer, candidate_positions=[position]),
        run_hierarchical_search_v2(scorer, candidate_positions=[position]),
        run_randomized_search_v2(scorer, candidate_positions=[position]),
    )
    for run in runs:
        assert run.hypotheses[0].score == pytest.approx(oracle_total, abs=1e-8, rel=1e-10)
        assert run.hypotheses[0].score_components == expected.hypothesis.score_components
        assert run.localization.decision_status == "ambiguous"


def test_minimum_range_is_applied_before_angular_grouping() -> None:
    measurements = _measurements((0.0, 0.5))
    catalog = _catalog(
        _record("too-close", 0.0, 5.0),
        _record("eligible", 0.02, 100.0),
    )
    scorer = CandidateScorer(
        measurements,
        catalog,
        _vector_map(),
        SearchConfig(
            min_range_m=20.0,
            max_range_m=5_000.0,
            circular_merge_tolerance_deg=0.1,
        ),
    )

    evaluation = scorer.evaluate(_origin_xy())

    assert evaluation.status == "scored"
    assert evaluation.predicted is not None
    assert len(evaluation.predicted.groups) == 1
    assert evaluation.predicted.groups[0].member_keys == ("eligible",)
    assert evaluation.predicted.groups[0].representative_key == "eligible"
    assert evaluation.hypothesis is not None
    assert evaluation.hypothesis.assignments[0].catalog_key == "eligible"


def test_distant_unsupported_catalog_geometry_does_not_block_local_scoring() -> None:
    longitude, latitude, _ = _GEOD.fwd(*_ORIGIN, 0.0, 15_000.0)
    unsupported = CatalogRecordV2(
        key="distant-unsupported",
        longitude_deg=float(longitude),
        latitude_deg=float(latitude),
        normalized_type="vessel",
        metadata=(("vessel_length_m", 40.0),),
    )
    scorer = CandidateScorer(
        _measurements((0.0, 0.5)),
        _catalog(_record("near", 0.0, 1_000.0), unsupported),
        _vector_map(),
        SearchConfig(max_range_m=5_000.0),
    )

    evaluation = scorer.evaluate(_origin_xy())

    assert evaluation.status == "scored"
    assert evaluation.predicted is not None
    assert evaluation.predicted.unresolved_model is False
    assert evaluation.predicted.unresolved_model_keys == ()
