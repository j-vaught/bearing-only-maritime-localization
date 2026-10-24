"""Tests for bounded coarse-to-fine localization search."""

from dataclasses import dataclass

import numpy as np
import pytest

from bearing_localization.search.hierarchical import hierarchical_search


def _example() -> tuple[list[tuple[str | None, float]], np.ndarray, list[str | None]]:
    true_position = np.asarray([1_234.0, 2_345.0])
    ais_xy = np.asarray(
        [[9_000.0, 1_000.0], [5_000.0, 10_000.0], [-6_000.0, 7_000.0]],
        dtype=float,
    )
    bearings = np.degrees(
        np.arctan2(ais_xy[:, 0] - true_position[0], ais_xy[:, 1] - true_position[1])
    )
    observations: list[tuple[str | None, float]] = [
        ("ship", float(bearing)) for bearing in bearings
    ]
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
    bounds_xy = (
        0.0,
        4_000.0,
        0.0,
        4_000.0,
    )

    first = hierarchical_search(
        observations,
        ais_xy,
        ais_types,
        bounds_xy,
        coarse_spacing_m=1_000.0,
        refinement_levels=3,
        beam_width=8,
    )
    second = hierarchical_search(
        observations,
        ais_xy,
        ais_types,
        bounds_xy,
        coarse_spacing_m=1_000.0,
        refinement_levels=3,
        beam_width=8,
    )

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


@dataclass(frozen=True)
class _InvalidSearchConfig:
    coarse_spacing_m: float = 1_000.0
    refinement_levels: int = 2
    refinement_factor: float = 2.0
    beam_width: int = 8
    message: str = ""


@pytest.mark.parametrize(
    "config",
    [
        _InvalidSearchConfig(coarse_spacing_m=0.0, message="coarse_spacing_m"),
        _InvalidSearchConfig(refinement_levels=-1, message="refinement_levels"),
        _InvalidSearchConfig(refinement_factor=1.0, message="refinement_factor"),
        _InvalidSearchConfig(beam_width=0, message="beam_width"),
    ],
)
def test_invalid_refinement_configuration_is_rejected(config: _InvalidSearchConfig) -> None:
    observations, ais_xy, ais_types = _example()

    with pytest.raises(ValueError, match=config.message):
        hierarchical_search(
            observations,
            ais_xy,
            ais_types,
            (0.0, 4_000.0, 0.0, 4_000.0),
            coarse_spacing_m=config.coarse_spacing_m,
            refinement_levels=config.refinement_levels,
            refinement_factor=config.refinement_factor,
            beam_width=config.beam_width,
        )


def test_missing_penalty_counts_unreported_visible_targets() -> None:
    from bearing_localization.algorithms import associate

    score, matches = associate(
        np.asarray([0.0, 0.0]),
        [],
        np.asarray([[0.0, 100.0], [100.0, 0.0]]),
        ["aid", "vessel"],
        sigma_deg=0.5,
        unmatched_penalty_deg=12.0,
        penalize_missing_ais=True,
    )

    assert score == pytest.approx(48.0)
    assert matches == ()


def test_missing_penalty_respects_angularly_merged_catalog_targets() -> None:
    observations = [("aid", 0.0)]
    ais_xy = np.asarray([[0.0, 100.0], [0.05, 100.0]])
    ais_types = ["aid", "aid"]
    bounds = (0.0, 1.0, 0.0, 1.0)

    unmerged = hierarchical_search(
        observations,
        ais_xy,
        ais_types,
        bounds,
        sigma_deg=0.5,
        coarse_spacing_m=10.0,
        refinement_levels=0,
        visibility_radius_m=1_000.0,
        penalize_missing_ais=True,
    )
    merged = hierarchical_search(
        observations,
        ais_xy,
        ais_types,
        bounds,
        sigma_deg=0.5,
        coarse_spacing_m=10.0,
        refinement_levels=0,
        visibility_radius_m=1_000.0,
        penalize_missing_ais=True,
        angular_resolution_deg=0.1,
    )

    assert unmerged.score_sigma > 20.0
    assert merged.score_sigma < 1.0
    assert merged.matches == ((0, 0),)
