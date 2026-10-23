"""Tests for bounded coarse-to-fine localization search."""

import numpy as np
import pytest

from bearing_localization.search.hierarchical import hierarchical_search


def _example() -> tuple[list[tuple[str, float]], np.ndarray, list[str]]:
    true_position = np.asarray([1_234.0, 2_345.0])
    ais_xy = np.asarray(
        [[9_000.0, 1_000.0], [5_000.0, 10_000.0], [-6_000.0, 7_000.0]],
        dtype=float,
    )
    bearings = np.degrees(
        np.arctan2(ais_xy[:, 0] - true_position[0], ais_xy[:, 1] - true_position[1])
    )
    observations = [("ship", float(bearing)) for bearing in bearings]
    return observations, ais_xy, ["ship"] * len(ais_xy)


def test_hierarchical_search_recovers_position_with_finer_levels() -> None:
    observations, ais_xy, ais_types = _example()

    result = hierarchical_search(
        observations,
        ais_xy,
        ais_types,
        (0.0, 4_000.0, 0.0, 4_000.0),
        sigma_deg=0.2,
        coarse_spacing_m=2_000.0,
        refinement_levels=6,
        beam_width=16,
    )

    error_m = np.hypot(result.east_m - 1_234.0, result.north_m - 2_345.0)
    assert error_m < 100.0
    assert len(result.matches) == 3
    assert result.evaluations > 4
    assert result.elapsed_s >= 0.0


def test_water_predicate_excludes_land_candidates() -> None:
    observations, ais_xy, ais_types = _example()

    result = hierarchical_search(
        observations,
        ais_xy,
        ais_types,
        (0.0, 4_000.0, 0.0, 4_000.0),
        coarse_spacing_m=1_000.0,
        refinement_levels=2,
        beam_width=12,
        water_predicate=lambda east, _north: east >= 1_500.0,
    )

    assert result.east_m >= 1_500.0


def test_search_is_deterministic() -> None:
    observations, ais_xy, ais_types = _example()
    args = (
        observations,
        ais_xy,
        ais_types,
        (0.0, 4_000.0, 0.0, 4_000.0),
    )
    kwargs = {"coarse_spacing_m": 1_000.0, "refinement_levels": 3, "beam_width": 8}

    first = hierarchical_search(*args, **kwargs)
    second = hierarchical_search(*args, **kwargs)

    assert (first.east_m, first.north_m, first.score_sigma, first.matches) == (
        second.east_m,
        second.north_m,
        second.score_sigma,
        second.matches,
    )
    assert first.evaluations == second.evaluations


def test_search_fails_if_water_mask_rejects_every_candidate() -> None:
    observations, ais_xy, ais_types = _example()

    with pytest.raises(ValueError, match="No water-valid"):
        hierarchical_search(
            observations,
            ais_xy,
            ais_types,
            (0.0, 2_000.0, 0.0, 2_000.0),
            coarse_spacing_m=1_000.0,
            water_predicate=lambda _east, _north: False,
        )


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"coarse_spacing_m": 0.0}, "coarse_spacing_m"),
        ({"refinement_levels": -1}, "refinement_levels"),
        ({"refinement_factor": 1.0}, "refinement_factor"),
        ({"beam_width": 0}, "beam_width"),
    ],
)
def test_invalid_refinement_configuration_is_rejected(
    kwargs: dict[str, float | int], message: str
) -> None:
    observations, ais_xy, ais_types = _example()

    with pytest.raises(ValueError, match=message):
        hierarchical_search(
            observations,
            ais_xy,
            ais_types,
            (0.0, 4_000.0, 0.0, 4_000.0),
            **kwargs,
        )
