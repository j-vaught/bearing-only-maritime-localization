from __future__ import annotations

import numpy as np
import pytest
from pyproj import CRS, Geod, Transformer
from shapely.geometry import box

from bearing_localization.algorithms import associate
from bearing_localization.search.config import SearchConfig
from bearing_localization.search.grid import run_grid_search_v2
from bearing_localization.search.hierarchical import run_hierarchical_search_v2
from bearing_localization.search.pairwise import run_pairwise_search_v2
from bearing_localization.search.randomized import run_randomized_search_v2
from bearing_localization.search.scoring import CandidateScorer
from maritime_localization_shared.contracts.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime_localization_shared.geospatial.vector_map import VectorMap

CRS_UTM = CRS.from_epsg(32618)
_TO_XY = Transformer.from_crs("EPSG:4326", CRS_UTM, always_xy=True)
_FROM_XY = Transformer.from_crs(CRS_UTM, "EPSG:4326", always_xy=True)
_GEOD = Geod(ellps="WGS84")
_ORIGIN = (-76.3, 37.0)


def _xy() -> tuple[float, float]:
    east, north = _TO_XY.transform(*_ORIGIN)
    return float(east), float(north)


def _map(*, land=None, coverage=None, unknown=None) -> VectorMap:
    x, y = _xy()
    if coverage is None:
        coverage = box(x - 20_000, y - 20_000, x + 20_000, y + 20_000)
    return VectorMap(land, crs=CRS_UTM, coverage_geometry=coverage, unknown_geometry=unknown)


def _record(
    key: str,
    bearing_deg: float = 0.0,
    range_m: float = 1_000.0,
    *,
    metadata: tuple[tuple[str, object], ...] = (),
) -> CatalogRecordV2:
    lon, lat, _ = _GEOD.fwd(*_ORIGIN, bearing_deg, range_m)
    return CatalogRecordV2(
        key=key,
        longitude_deg=float(lon),
        latitude_deg=float(lat),
        normalized_type="vessel",
        metadata=metadata,
    )


def _catalog(*records: CatalogRecordV2) -> CatalogV2:
    return CatalogV2(
        records=tuple(records),
        timestamp_utc="2026-10-25T12:00:00Z",
        source="test",
        catalog_version="1",
        catalog_id="a17",
    )


def _measurements(*items: tuple[float, float | None]) -> MeasurementSetV2:
    return MeasurementSetV2(
        tuple(
            MeasurementV2(bearing, "2026-10-25T12:00:00Z", sigma, "vessel")
            for bearing, sigma in items
        )
    )


def test_candidate_scorer_returns_complete_hypothesis_and_decomposition() -> None:
    scorer = CandidateScorer(
        _measurements((0.0, 0.5)),
        _catalog(_record("north")),
        _map(),
        SearchConfig(max_range_m=5_000.0),
    )

    result = scorer.evaluate(_xy())

    assert result.status == "scored"
    assert result.hypothesis is not None
    assert result.hypothesis.assignments[0].catalog_key == "north"
    assert result.hypothesis.score_components is not None
    parts = result.hypothesis.score_components
    assert parts.total_cost == pytest.approx(
        parts.matched_cost + parts.clutter_cost + parts.missed_cost
    )
    assert result.costs is not None
    assert result.costs.matched_costs.shape == (1, 1)


def test_candidate_scorer_preserves_per_report_sigma_in_legacy_objective() -> None:
    scorer = CandidateScorer(
        _measurements((0.0, 0.5), (2.0, 2.0)),
        _catalog(_record("north")),
        _map(),
        SearchConfig(objective_version="legacy_corrected_v0", max_range_m=5_000.0),
    )

    result = scorer(_xy())

    assert result.status == "scored"
    assert result.costs is not None
    assert result.costs.matched_costs[0, 0] == pytest.approx(0.0)
    assert result.costs.report_clutter_costs.tolist() == pytest.approx([24.0, 6.0])


def test_candidate_scorer_never_scores_land_or_unknown_coverage() -> None:
    origin = _xy()
    land = box(origin[0] - 50, origin[1] - 50, origin[0] + 50, origin[1] + 50)
    scorer_land = CandidateScorer(
        _measurements((0.0, None)), _catalog(), _map(land=land), SearchConfig()
    )
    assert scorer_land(origin).status == "invalid_water"
    assert scorer_land(origin).hypothesis is None

    scorer_unknown = CandidateScorer(_measurements((0.0, None)), _catalog(), _map(), SearchConfig())
    outside_coverage = (origin[0] + 30_000, origin[1])
    result = scorer_unknown(outside_coverage)
    assert result.status == "unresolved_coverage"
    assert result.hypothesis is None


def test_candidate_scorer_does_not_score_unsupported_nearby_model() -> None:
    scorer = CandidateScorer(
        _measurements((0.0, None)),
        _catalog(_record("unsupported", metadata=(("vessel_length_m", 40.0),))),
        _map(),
        SearchConfig(max_range_m=5_000.0),
    )

    result = scorer(_xy())

    assert result.status == "unresolved_model"
    assert result.hypothesis is None


def test_mixed_label_legacy_association_does_not_match_wrong_type() -> None:
    score, matches = associate(
        np.asarray([0.0, 0.0]),
        [("vessel", 0.0), (None, 90.0)],
        np.asarray([[0.0, 1_000.0]]),
        ["other"],
        sigma_deg=0.5,
        use_types=True,
    )

    assert matches == ()
    assert score == pytest.approx(48.0)


def test_v2_dispatch_entrypoints_share_candidate_scores_and_never_accept() -> None:
    scorer = CandidateScorer(
        _measurements((0.0, 0.5)),
        _catalog(_record("north")),
        _map(),
        SearchConfig(max_range_m=5_000.0),
    )
    positions = [_xy()]
    runs = (
        run_grid_search_v2(scorer, iter(positions)),
        run_hierarchical_search_v2(scorer, iter(positions)),
        run_randomized_search_v2(scorer, iter(positions)),
    )

    expected = scorer(_xy())
    assert expected.hypothesis is not None
    assert [run.hypotheses[0].score for run in runs] == pytest.approx(
        [expected.hypothesis.score] * 3
    )
    for run in runs:
        assert run.localization.decision_status == "ambiguous"
        assert run.localization.certificate_scope == "none"
        assert run.localization.search_status == "completed"


def test_pairwise_v2_uses_a19_stream_and_keeps_completion_uncertified() -> None:
    x, y = _xy()
    scorer = CandidateScorer(
        _measurements((0.0, 0.5), (90.0, 0.5)),
        _catalog(_record("north", 0.0), _record("east", 90.0)),
        _map(),
        SearchConfig(
            max_range_m=5_000.0,
            candidate_budget=100,
            bounds_xy=(x - 2_000.0, x + 2_000.0, y - 2_000.0, y + 2_000.0),
        ),
    )

    run = run_pairwise_search_v2(scorer, batch_size=8)

    assert run.localization.search_status == "completed"
    assert run.localization.decision_status == "ambiguous"
    assert run.localization.certificate_scope == "none"
    assert run.candidate_count > 0
    first = run.hypotheses[0]
    position = [(first.east_m, first.north_m)]
    parity_runs = (
        run_grid_search_v2(scorer, position),
        run_hierarchical_search_v2(scorer, position),
        run_randomized_search_v2(scorer, position),
    )
    assert [item.hypotheses[0].score for item in parity_runs] == pytest.approx([first.score] * 3)
