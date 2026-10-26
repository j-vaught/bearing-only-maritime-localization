from __future__ import annotations

import json

import pytest
from pyproj import CRS, Geod, Transformer
from shapely.geometry import box, mapping

from bearing_localization.search.config import SearchConfig
from bearing_localization.search.grid import run_grid_search_v2
from bearing_localization.search.scoring import CandidateScorer
from maritime_localization_shared.contracts.estimate import PositionEstimate
from maritime_localization_shared.contracts.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MapV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime_localization_shared.geospatial.vector_map import VectorMap
from maritime_localization_shared.serialization import (
    contract_from_dict,
    contract_to_dict,
    dumps_contract,
    loads_contract,
)

from .reference_assignment import optimal_assignments

_CRS = CRS.from_epsg(32618)
_FORWARD = Transformer.from_crs("EPSG:4326", _CRS, always_xy=True)
_GEOD = Geod(ellps="WGS84")
_ORIGIN = (-76.3, 37.0)
_TIME = "2026-10-25T12:00:00Z"


def _origin_xy() -> tuple[float, float]:
    east, north = _FORWARD.transform(*_ORIGIN)
    return float(east), float(north)


def _record(
    key: str,
    bearing_deg: float,
    range_m: float,
    *,
    kind: str = "vessel",
    metadata: tuple[tuple[str, object], ...] = (),
) -> CatalogRecordV2:
    longitude, latitude, _ = _GEOD.fwd(*_ORIGIN, bearing_deg, range_m)
    return CatalogRecordV2(
        key=key,
        longitude_deg=float(longitude),
        latitude_deg=float(latitude),
        normalized_type=kind,
        metadata=metadata,
    )


def _catalog(*records: CatalogRecordV2) -> CatalogV2:
    return CatalogV2(
        records=tuple(records),
        timestamp_utc=_TIME,
        source="a37 adversarial fixture",
        catalog_version="1",
        catalog_id="a37",
    )


def _measurements(*items: tuple[float, float, str | None]) -> MeasurementSetV2:
    return MeasurementSetV2(
        tuple(MeasurementV2(bearing, _TIME, sigma, label) for bearing, sigma, label in items)
    )


def _map(*, land=None, coverage=None, unknown=None) -> VectorMap:
    east, north = _origin_xy()
    return VectorMap(
        land,
        crs=_CRS,
        coverage_geometry=coverage
        if coverage is not None
        else box(east - 20_000, north - 20_000, east + 20_000, north + 20_000),
        unknown_geometry=unknown,
    )


def test_v1_estimate_migrates_explicitly_and_v2_contracts_stay_strict() -> None:
    legacy = {
        "east_m": 10.0,
        "north_m": 20.0,
        "score": 1.0,
        "elapsed_s": 0.25,
        "algorithm": "historical-grid",
        "matched_pairs": [[0, "old-key"]],
    }
    legacy_estimate = contract_from_dict(PositionEstimate, legacy)
    assert legacy_estimate == PositionEstimate(
        10.0, 20.0, 1.0, 0.25, "historical-grid", ((0, "old-key"),)
    )
    assert contract_to_dict(legacy_estimate)["schema_version"] == 1

    v2 = _measurements((359.0, 0.05, "vessel"), (1.0, 2.0, None))
    encoded = dumps_contract(v2)
    assert loads_contract(encoded, MeasurementSetV2) == v2
    payload = json.loads(encoded)
    payload["measurements"][0]["truth_range_m"] = 42.0
    with pytest.raises(ValueError, match="Unexpected MeasurementV2 fields"):
        contract_from_dict(MeasurementSetV2, payload)


def test_search_serialization_and_cli_contract_have_no_truth_channel(tmp_path) -> None:
    position = _origin_xy()
    result = run_grid_search_v2(
        CandidateScorer(
            _measurements((0.0, 0.5, "vessel")),
            _catalog(_record("north", 0.0, 1000.0)),
            _map(),
            SearchConfig(max_range_m=5000.0),
        ),
        candidate_positions=[position],
    ).localization
    serialized = dumps_contract(result)
    assert "truth" not in serialized.lower()
    assert "observer_east_m" not in serialized
    assert "truth_range_m" not in serialized
    assert set(contract_to_dict(result)) == {
        "hypotheses",
        "elapsed_s",
        "decision_status",
        "search_status",
        "certificate_scope",
        "accepted_index",
        "budget_limit",
        "budget_used",
        "report_count",
        "objective_version",
        "bound_gap",
        "contract_type",
    }

    from bearing_localization.accuracy_cli import search_v2_main

    with pytest.raises(SystemExit) as error:
        search_v2_main(["--truth", str(tmp_path / "truth.json")])
    assert error.value.code == 2


def test_mixed_labels_and_heteroscedastic_reports_match_independent_assignment() -> None:
    measurements = _measurements(
        (0.0, 0.05, "vessel"),
        (90.0, 0.8, "physical_aid"),
        (180.0, 4.0, "mystery craft"),
    )
    catalog = _catalog(
        _record("north-vessel", 0.0, 1000.0),
        _record("east-aid", 90.0, 1200.0, kind="physical_aid"),
        _record("south-vessel", 180.0, 1500.0),
    )
    scorer = CandidateScorer(
        measurements,
        catalog,
        _map(),
        SearchConfig(max_range_m=5000.0, use_types=True),
    )
    evaluated = scorer.evaluate(_origin_xy())
    assert evaluated.hypothesis is not None and evaluated.costs is not None
    assert [item.sigma_deg for item in measurements.measurements] == [0.05, 0.8, 4.0]
    oracle = optimal_assignments(
        evaluated.costs.matched_costs.tolist(),
        evaluated.costs.report_clutter_costs.tolist(),
        evaluated.costs.group_miss_costs.tolist(),
    )
    assert oracle
    expected = evaluated.costs.baseline_constant + oracle[0].cost
    assert evaluated.hypothesis.score == pytest.approx(expected, abs=1e-8, rel=1e-10)
    components = evaluated.hypothesis.score_components
    assert components is not None
    assert components.total_cost == pytest.approx(
        components.matched_cost + components.clutter_cost + components.missed_cost,
        abs=1e-8,
        rel=1e-10,
    )


def test_unmatched_predicted_group_cost_is_included_in_score_decomposition() -> None:
    scorer = CandidateScorer(
        _measurements((0.0, 0.5, "vessel")),
        _catalog(_record("north", 0.0, 1000.0), _record("east", 90.0, 1000.0)),
        _map(),
        SearchConfig(max_range_m=5000.0, detection_probability=0.8),
    )
    evaluation = scorer.evaluate(_origin_xy())
    assert evaluation.hypothesis is not None
    assert evaluation.costs is not None
    assert evaluation.predicted is not None
    used = {assignment.catalog_key for assignment in evaluation.hypothesis.assignments}
    unused_indices = [
        index
        for index, group in enumerate(evaluation.predicted.groups)
        if group.representative_key not in used
    ]
    assert len(unused_indices) == 1
    expected_missed = sum(evaluation.costs.group_miss_costs[index] for index in unused_indices)
    components = evaluation.hypothesis.score_components
    assert components is not None
    assert expected_missed > 0.0
    assert components.missed_cost == pytest.approx(expected_missed, abs=1e-12)


def test_candidate_stream_deduplicates_positions_but_accounts_for_every_unique_outcome() -> None:
    east, north = _origin_xy()
    vector_map = _map(
        land=box(east + 20.0, north - 20.0, east + 40.0, north + 20.0),
        coverage=box(east - 5000.0, north - 5000.0, east + 5000.0, north + 5000.0),
        unknown=box(east + 40.0, north - 20.0, east + 50.0, north + 20.0),
    )
    scorer = CandidateScorer(
        _measurements((0.0, 0.5, "vessel")),
        _catalog(_record("north", 0.0, 1000.0)),
        vector_map,
        SearchConfig(max_range_m=5000.0, candidate_budget=2),
    )
    run = run_grid_search_v2(
        scorer,
        candidate_positions=[
            (east, north),
            (east, north),
            (east + 30.0, north),
            (east + 45.0, north),
        ],
    )

    assert run.candidate_count == 2
    counts = dict(run.outcome_counts)
    assert counts["scored"] == 1
    assert counts["invalid_water"] == 1
    assert counts["unresolved_coverage"] == 0
    assert sum(counts.values()) == run.candidate_count
    assert run.localization.search_status == "budget_exhausted"
    assert run.localization.budget_limit == run.localization.budget_used == 2
    assert run.localization.decision_status != "accepted"
    assert run.localization.certificate_scope == "none"

    full_scorer = CandidateScorer(
        _measurements((0.0, 0.5, "vessel")),
        _catalog(_record("north", 0.0, 1000.0)),
        vector_map,
        SearchConfig(max_range_m=5000.0),
    )
    full_run = run_grid_search_v2(
        full_scorer,
        candidate_positions=[
            (east, north),
            (east, north),
            (east + 30.0, north),
            (east + 45.0, north),
        ],
    )
    full_counts = dict(full_run.outcome_counts)
    assert full_run.candidate_count == 3
    assert full_counts["scored"] == 1
    assert full_counts["invalid_water"] == 1
    assert full_counts["unresolved_coverage"] == 1
    assert sum(full_counts.values()) == full_run.candidate_count
    assert full_run.localization.certificate_scope == "none"


def test_identity_limited_geometry_is_scoreable_but_unknown_coverage_is_not() -> None:
    east, north = _origin_xy()
    ambiguous = _record(
        "uncertain-aid",
        0.0,
        1000.0,
        kind="physical_aid",
        metadata=(
            ("identity_ambiguous", True),
            ("source_record_identity_status", "qualified_source_key"),
            ("physical_linkage_status", "unresolved"),
            ("observation_association_status", "candidate_geometry_only"),
        ),
    )
    scorer = CandidateScorer(
        _measurements((0.0, 0.5, "physical_aid")),
        _catalog(ambiguous),
        _map(),
        SearchConfig(max_range_m=5000.0),
    )
    candidate = scorer.evaluate((east, north))
    assert candidate.status == "scored"
    assert candidate.predicted is not None
    assert candidate.predicted.identity_limited_keys == ("uncertain-aid",)
    assert candidate.predicted.unresolved_model is False
    assert candidate.hypothesis is not None
    assert candidate.hypothesis.assignments[0].catalog_key == "uncertain-aid"

    result = run_grid_search_v2(scorer, candidate_positions=[(east, north)])
    assert result.candidate_count == 1
    assert dict(result.outcome_counts)["scored"] == 1
    assert len(result.hypotheses) == 1
    assert result.localization.decision_status == "ambiguous"
    assert result.localization.accepted_index is None
    assert result.localization.certificate_scope == "finite_candidate_set"

    unknown_map = VectorMap(
        None, crs=_CRS, coverage_geometry=box(east + 1.0, north + 1.0, east + 2.0, north + 2.0)
    )
    outside = CandidateScorer(
        _measurements((0.0, 0.5, "physical_aid")),
        _catalog(ambiguous),
        unknown_map,
        SearchConfig(max_range_m=5000.0),
    )
    assert outside.evaluate((east, north)).status == "unresolved_coverage"


def test_unknown_coverage_contract_remains_explicit_in_serialized_map() -> None:
    geometry = json.dumps(mapping(box(-76.0, 36.0, -75.0, 37.0)), sort_keys=True)
    partial = MapV2(
        crs="EPSG:4326",
        source="port mosaic",
        map_version="source-manifest-version",
        coverage_status="partial",
        coverage_geometry_geojson=geometry,
        unknown_geometry_geojson=json.dumps(mapping(box(-75.5, 36.0, -75.0, 37.0)), sort_keys=True),
    )
    restored = loads_contract(dumps_contract(partial), MapV2)
    assert restored.coverage_status == "partial"
    assert restored.coverage_geometry_geojson is not None
    assert restored.unknown_geometry_geojson is not None
    with pytest.raises(ValueError, match="requires both coverage and unknown"):
        MapV2(
            "EPSG:4326",
            "port mosaic",
            "v",
            coverage_status="partial",
            coverage_geometry_geojson=geometry,
        )
