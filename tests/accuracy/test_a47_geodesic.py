from pyproj import Geod

from bearing_localization.search.proposals.geodesic_wedge import (
    generate_geographic_reverse_wedge_proposals_geodesic,
)
from bearing_localization.search.proposals.wedge import (
    GeographicWedgeCatalogObject,
    WedgeObservation,
)
from maritime_localization_shared.geospatial.coordinates import LocalFrame


def _matching_fixture():
    frame = LocalFrame((0.0, 0.0))
    truth = frame.to_geographic(50.0, 50.0)
    geod = Geod(ellps="WGS84")
    observations = []
    catalog = []
    for index, bearing_deg in enumerate((0.0, 90.0, 180.0, 270.0, 45.0)):
        longitude_deg, latitude_deg, _ = geod.fwd(truth[0], truth[1], bearing_deg, 1_000.0)
        catalog.append(GeographicWedgeCatalogObject(f"true-{index}", longitude_deg, latitude_deg))
        observations.append(WedgeObservation(index, bearing_deg))
    artifact = frame.to_geographic(550.0, 550.0)
    catalog.append(GeographicWedgeCatalogObject("artifact", *artifact))
    return frame, observations, catalog


def test_geodesic_wedge_uses_distinct_witness_matching():
    frame, observations, catalog = _matching_fixture()
    result = generate_geographic_reverse_wedge_proposals_geodesic(
        observations,
        catalog,
        frame=frame,
        max_range_m=1_500.0,
        cell_size_m=100.0,
        angular_tolerance_deg=0.5,
        max_source_pairs=30,
        max_candidate_cells=100_000,
        source_chunk_size=3,
    )
    assert result.generation_status == "completed"
    assert result.source_pairs_processed == result.source_pairs_total == 30
    assert result.source_pairs_failed == 0
    true_cell = next(item for item in result.proposals if item.candidate_id == "cell:0:0")
    artifact_cell = next(item for item in result.proposals if item.candidate_id == "cell:5:5")
    assert true_cell.vote_count == true_cell.matching_count == 5
    assert artifact_cell.vote_count == 5
    assert artifact_cell.matching_count == 2
    assert result.proposals[0].candidate_id == true_cell.candidate_id
    assert set(true_cell.witness_keys) >= {f"true-{index}" for index in range(5)}


def test_geodesic_wedge_zero_range_has_no_angular_evidence_and_wraps():
    frame = LocalFrame((-73.0, 35.0))
    source = GeographicWedgeCatalogObject("source", *frame.origin_lonlat)
    result = generate_geographic_reverse_wedge_proposals_geodesic(
        [WedgeObservation(4, 359.9)],
        [source],
        frame=frame,
        max_range_m=100.0,
        cell_size_m=100.0,
        angular_tolerance_deg=0.5,
        max_source_pairs=1,
        max_candidate_cells=100_000,
        source_chunk_size=1,
    )
    cell = next(item for item in result.proposals if item.evidence_score == 0.0)
    assert result.geodesic_samples_degenerate >= 1
    assert cell.normalized_angular_residual == 1.0


def test_geodesic_wedge_reports_explicit_source_and_storage_budgets():
    frame, observations, catalog = _matching_fixture()
    source_limited = generate_geographic_reverse_wedge_proposals_geodesic(
        observations,
        catalog,
        frame=frame,
        max_range_m=1_500.0,
        cell_size_m=100.0,
        angular_tolerance_deg=0.5,
        max_source_pairs=1,
        max_candidate_cells=100_000,
        source_chunk_size=3,
    )
    assert source_limited.generation_status == "budget_exhausted"
    assert source_limited.budget_reason == "source_pairs"
    assert source_limited.source_pairs_processed == 1
    storage_limited = generate_geographic_reverse_wedge_proposals_geodesic(
        observations,
        catalog,
        frame=frame,
        max_range_m=1_500.0,
        cell_size_m=100.0,
        angular_tolerance_deg=0.5,
        max_source_pairs=30,
        max_candidate_cells=3,
        source_chunk_size=3,
    )
    assert storage_limited.generation_status == "budget_exhausted"
    assert storage_limited.budget_reason == "candidate_cells"
    assert storage_limited.candidate_cells_total == 3


def test_geodesic_wedge_accepts_variable_sigma_for_evidence():
    frame, observations, catalog = _matching_fixture()
    result = generate_geographic_reverse_wedge_proposals_geodesic(
        observations,
        catalog,
        frame=frame,
        max_range_m=1_500.0,
        cell_size_m=100.0,
        angular_tolerance_deg=0.5,
        observation_sigmas_deg={0: 0.1, 1: 2.0, 2: 0.5, 3: 1.0, 4: 3.0},
        max_source_pairs=30,
        max_candidate_cells=100_000,
        source_chunk_size=3,
    )
    true_cell = next(item for item in result.proposals if item.candidate_id == "cell:0:0")
    assert true_cell.matching_count == 5
    assert true_cell.evidence_score > 0.0
    assert result.angular_sample_gap_deg > 0.0
