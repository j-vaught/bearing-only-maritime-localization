import pytest
from pyproj import Geod

from bearing_localization.search.proposals.geodesic_wedge import (
    generate_geographic_reverse_wedge_proposals_geodesic,
)
from bearing_localization.search.proposals.wedge import (
    GeographicWedgeCatalogObject,
    WedgeObservation,
)
from maritime_localization_shared.geospatial.coordinates import LocalFrame


def _small_fixture():
    frame = LocalFrame((-76.43, 39.32))
    source_lon, source_lat, _ = Geod(ellps="WGS84").fwd(
        *frame.origin_lonlat, 215.0, 1_000.0
    )
    return frame, [WedgeObservation(0, 35.0)], [
        GeographicWedgeCatalogObject("source-0", source_lon, source_lat)
    ]


def test_generator_exposes_solver_tolerance_and_observed_residual_metadata():
    frame, observations, catalog = _small_fixture()
    result = generate_geographic_reverse_wedge_proposals_geodesic(
        observations,
        catalog,
        frame=frame,
        max_range_m=1_500.0,
        cell_size_m=100.0,
    )

    assert result.solver_tolerance_deg == 1e-7
    assert 0.0 <= result.max_solver_residual_deg <= result.solver_tolerance_deg
    assert result.source_pairs_processed == 1
    assert result.source_pairs_failed == 0


def test_solver_tolerance_is_capped_by_angular_agreement_and_sigma():
    frame, observations, catalog = _small_fixture()
    with pytest.raises(ValueError, match="must not exceed"):
        generate_geographic_reverse_wedge_proposals_geodesic(
            observations,
            catalog,
            frame=frame,
            max_range_m=1_500.0,
            cell_size_m=100.0,
            observation_sigmas_deg={0: 0.01},
            solver_tolerance_deg=0.00011,
        )


@pytest.mark.parametrize("value", [0.0, -1e-9, float("nan"), float("inf")])
def test_solver_tolerance_must_be_finite_and_positive(value):
    frame, observations, catalog = _small_fixture()
    with pytest.raises(ValueError, match="finite and positive"):
        generate_geographic_reverse_wedge_proposals_geodesic(
            observations,
            catalog,
            frame=frame,
            max_range_m=1_500.0,
            cell_size_m=100.0,
            solver_tolerance_deg=value,
        )
