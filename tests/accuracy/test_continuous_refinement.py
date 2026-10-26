from __future__ import annotations

from math import hypot

from pyproj import CRS, Geod, Transformer
from shapely.geometry import box

from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime.schemas.localization import Assignment, Hypothesis
from maritime.search.config import SearchConfig
from maritime.search.refinement import refine_modes
from maritime.search.scoring import CandidateScorer

CRS_UTM = CRS.from_epsg(32618)
ORIGIN = (-76.3, 37.0)
_TO_XY = Transformer.from_crs("EPSG:4326", CRS_UTM, always_xy=True)
_TO_LONLAT = Transformer.from_crs(CRS_UTM, "EPSG:4326", always_xy=True)
_GEOD = Geod(ellps="WGS84")


def _xy():
    east, north = _TO_XY.transform(*ORIGIN)
    return float(east), float(north)


def _geographic_targets():
    targets = []
    for index, bearing in enumerate((18.0, 101.0, 203.0, 298.0)):
        lon, lat, _ = _GEOD.fwd(*ORIGIN, bearing, 1200.0 + index * 850.0)
        targets.append((f"target-{index}", bearing, lon, lat))
    return targets


def _inputs(*, noisy=False):
    targets = _geographic_targets()
    sigmas = (0.15, 0.5, 1.2, 0.3)
    noise = (0.08, -0.12, 0.2, -0.04) if noisy else (0.0,) * 4
    measurements = MeasurementSetV2(
        tuple(
            MeasurementV2(
                (bearing + noise[index]) % 360.0,
                "2026-10-25T12:00:00Z",
                sigmas[index],
                "vessel",
            )
            for index, (_key, bearing, _lon, _lat) in enumerate(targets)
        )
    )
    catalog = CatalogV2(
        tuple(
            CatalogRecordV2(
                key=key,
                longitude_deg=lon,
                latitude_deg=lat,
                normalized_type="vessel",
            )
            for key, _bearing, lon, lat in targets
        ),
        timestamp_utc="2026-10-25T12:00:00Z",
        source="test",
        catalog_version="1",
        catalog_id="a20",
    )
    x, y = _xy()
    vector_map = VectorMap(
        None,
        crs=CRS_UTM,
        coverage_geometry=box(x - 15_000, y - 15_000, x + 15_000, y + 15_000),
    )
    return measurements, catalog, vector_map


def test_noisy_mixed_sigma_refinement_recovers_from_coarse_cell_and_matches_scorer():
    measurements, catalog, vector_map = _inputs(noisy=True)
    x, y = _xy()
    bounds = (x - 1000, x + 1000, y - 1000, y + 1000)
    config = SearchConfig(bounds_xy=bounds, max_range_m=8_000)
    scorer = CandidateScorer(measurements, catalog, vector_map, config)
    seed = scorer.evaluate((x + 250, y - 250)).hypothesis
    assert seed is not None

    result = refine_modes(
        measurements,
        catalog,
        vector_map,
        config,
        [seed],
        initial_step_m=250,
    )
    mode = result.modes[0]
    assert mode.hypothesis is not None
    direct = scorer.evaluate((mode.hypothesis.east_m, mode.hypothesis.north_m))
    assert direct.hypothesis is not None
    assert mode.hypothesis.score == direct.hypothesis.score
    assert mode.hypothesis.assignments == direct.hypothesis.assignments
    assert mode.hypothesis.score < seed.score
    assert hypot(mode.hypothesis.east_m - x, mode.hypothesis.north_m - y) < 100
    assert all(right.score <= left.score for left, right in zip(mode.history, mode.history[1:]))
    assert mode.uncertainty_rank == 2
    assert not mode.uncertainty_singular
    assert mode.estimated_95_radius_m is not None
    assert mode.estimated_95_radius_m > 0


def test_modes_refine_independently_without_crossing_local_basins(monkeypatch):
    import maritime.search.refinement as refinement

    class BasinScorer:
        def __init__(self, *_args):
            pass

        def evaluate(self, position):
            east, north = position
            left = (east + 5.0) ** 2 + north**2
            right = (east - 5.0) ** 2 + north**2 + 1.0
            score = min(left, right)
            return type(
                "Outcome",
                (),
                {"status": "scored", "hypothesis": Hypothesis(east, north, score)},
            )()

    monkeypatch.setattr(refinement, "CandidateScorer", BasinScorer)
    monkeypatch.setattr(
        refinement,
        "_conditional_uncertainty",
        lambda *_args: (None, 0, True, "test basin"),
    )
    measurements, catalog, vector_map = _inputs()
    config = SearchConfig(bounds_xy=(-10.0, 10.0, -10.0, 10.0))
    result = refine_modes(
        measurements,
        catalog,
        vector_map,
        config,
        (Hypothesis(-7.0, 0.0, 4.0), Hypothesis(7.0, 0.0, 5.0)),
        initial_step_m=1.0,
    )
    assert len(result.modes) == 2
    first, second = result.modes
    assert first.hypothesis is not None and second.hypothesis is not None
    assert first.hypothesis.east_m == -5.0
    assert second.hypothesis.east_m == 5.0
    assert first.hypothesis.score == 0.0
    assert second.hypothesis.score == 1.0
    assert all(mode.improved_hypothesis for mode in result.modes)


def test_assignment_switch_is_rescored_and_equal_score_keeps_pose(monkeypatch):
    import maritime.search.refinement as refinement

    class SwitchingScorer:
        def __init__(self, *_args):
            self.seen = []

        def evaluate(self, position):
            east, north = position
            self.seen.append(position)
            key = "left" if east < 0 else "right"
            hypothesis = Hypothesis(
                east,
                north,
                (east - 0.4) ** 2 + north**2,
                (Assignment(0, key),),
            )
            return type("Outcome", (), {"status": "scored", "hypothesis": hypothesis})()

    monkeypatch.setattr(refinement, "CandidateScorer", SwitchingScorer)
    monkeypatch.setattr(
        refinement,
        "_conditional_uncertainty",
        lambda *_args: (None, 1, True, "one bearing"),
    )
    measurements, catalog, vector_map = _inputs()
    config = SearchConfig(bounds_xy=(-2.0, 2.0, -2.0, 2.0))
    seed = Hypothesis(-0.2, 0.0, 0.36, (Assignment(0, "left"),))
    result = refinement.refine_modes(
        measurements,
        catalog,
        vector_map,
        config,
        [seed],
        initial_step_m=0.5,
    )
    mode = result.modes[0]
    assert mode.hypothesis is not None
    assert mode.hypothesis.assignments[0].catalog_key == "right"
    assert abs(mode.hypothesis.east_m - 0.4) < 0.01
    assert mode.hypothesis.score < seed.score
    assert all(b.score <= a.score for a, b in zip(mode.history, mode.history[1:]))


def test_water_boundary_and_unknown_coverage_trials_never_become_poses():
    measurements, catalog, _ = _inputs()
    x, y = _xy()
    coverage = box(x - 15_000, y - 15_000, x + 15_000, y + 15_000)
    land = box(x + 50, y - 250, x + 250, y + 250)
    unknown = box(x + 10, y - 20, x + 40, y + 20)
    vector_map = VectorMap(land, crs=CRS_UTM, coverage_geometry=coverage, unknown_geometry=unknown)
    config = SearchConfig(bounds_xy=(x - 250, x + 250, y - 250, y + 250), max_range_m=8_000)
    scorer = CandidateScorer(measurements, catalog, vector_map, config)
    seed = scorer.evaluate((x - 80, y)).hypothesis
    assert seed is not None
    assert scorer.evaluate((x + 20, y)).status == "unresolved_coverage"
    result = refine_modes(
        measurements,
        catalog,
        vector_map,
        config,
        [seed],
        initial_step_m=100,
    )
    assert result.modes[0].hypothesis is not None
    assert result.modes[0].hypothesis.east_m < x + 40
    assert (
        vector_map.point_water_status(
            (result.modes[0].hypothesis.east_m, result.modes[0].hypothesis.north_m)
        )
        == "water"
    )
    assert all(
        scorer.evaluate((step.east_m, step.north_m)).status == "scored"
        for step in result.modes[0].history
    )


def test_collinear_assignments_report_singular_uncertainty_without_ellipse():
    measurements, catalog, vector_map = _inputs()
    measurements = MeasurementSetV2((measurements.measurements[0],))
    catalog = CatalogV2(
        (catalog.records[0],),
        timestamp_utc="2026-10-25T12:00:00Z",
        source="test",
        catalog_version="1",
        catalog_id="a20-single-bearing",
    )
    x, y = _xy()
    config = SearchConfig(bounds_xy=(x - 1, x + 1, y - 1, y + 1), max_range_m=8_000)
    scorer = CandidateScorer(measurements, catalog, vector_map, config)
    hypothesis = scorer.evaluate((x, y)).hypothesis
    assert hypothesis is not None
    result = refine_modes(measurements, catalog, vector_map, config, [hypothesis], max_iterations=1)
    mode = result.modes[0]
    assert mode.uncertainty_singular
    assert mode.hypothesis is not None
    assert mode.hypothesis.uncertainty is None
    assert mode.estimated_95_radius_m is None


def test_equal_objective_trials_preserve_seed_pose(monkeypatch):
    import maritime.search.refinement as refinement

    class EqualScorer:
        def __init__(self, *_args):
            pass

        def evaluate(self, position):
            east, north = position
            return type(
                "Outcome",
                (),
                {"status": "scored", "hypothesis": Hypothesis(east, north, 2.0)},
            )()

    monkeypatch.setattr(refinement, "CandidateScorer", EqualScorer)
    monkeypatch.setattr(
        refinement,
        "_conditional_uncertainty",
        lambda *_args: (None, 0, True, "no assignments"),
    )
    measurements, catalog, vector_map = _inputs()
    config = SearchConfig(bounds_xy=(-2.0, 2.0, -2.0, 2.0))
    seed = Hypothesis(0.0, 0.0, 2.0)
    result = refinement.refine_modes(
        measurements, catalog, vector_map, config, [seed], initial_step_m=0.5
    )
    mode = result.modes[0]
    assert mode.hypothesis is not None
    assert (mode.hypothesis.east_m, mode.hypothesis.north_m) == (0.0, 0.0)
    assert all((step.east_m, step.north_m) == (0.0, 0.0) for step in mode.history)
    assert not mode.improved_hypothesis
