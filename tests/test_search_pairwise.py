from __future__ import annotations

import csv
import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from bearing_localization.evaluation.metrics import evaluate_estimate
from bearing_localization.scene import AISRecord as LegacyAISRecord
from bearing_localization.scene import Landmark, Scene, SceneConfig, ShipPose
from bearing_localization.search import PairwiseSearchConfig, run_pairwise_search
from bearing_localization.search.pairwise import pairwise_hypothesis_search
from bearing_localization.search_cli import _RasterVisibility
from bearing_localization.simulator.runner import run_simulator
from maritime_localization_shared.contracts import (
    AISRecord,
    AISSnapshot,
    MapInput,
    MeasurementSet,
    Observation,
)


def _fixture(
    observer: tuple[float, float] = (120.0, -80.0),
) -> tuple[MeasurementSet, AISSnapshot]:
    targets = (
        ("ship-a", "ship", (-2_000.0, 500.0)),
        ("buoy-a", "buoy", (1_100.0, 1_800.0)),
        ("marker-a", "channel_marker", (2_400.0, -1_300.0)),
        ("ship-b", "ship", (-1_700.0, -2_200.0)),
    )
    observations = tuple(
        Observation(
            bearing_deg=float(np.degrees(np.arctan2(xy[0] - observer[0], xy[1] - observer[1]))),
            bearing_sigma_deg=0.1,
            object_type=kind,
        )
        for _, kind, xy in targets[:3]
    )
    ais = AISSnapshot(
        records=tuple(AISRecord(record_id, *xy, kind) for record_id, kind, xy in targets),
        timestamp_utc="2026-10-24T12:00:00Z",
        crs="local-EN",
        snapshot_id="pairwise-test",
    )
    return MeasurementSet(observations), ais


def test_pairwise_search_recovers_exact_position_and_matches() -> None:
    measurements, ais = _fixture()
    estimate = run_pairwise_search(
        measurements,
        ais,
        (-500.0, 500.0, -500.0, 500.0),
        PairwiseSearchConfig(max_hypotheses=1_000),
    )

    assert estimate.algorithm == "pairwise_hypothesis"
    assert np.hypot(estimate.east_m - 120.0, estimate.north_m + 80.0) < 1e-5
    assert {ais_id for _, ais_id in estimate.matched_pairs} == {
        "ship-a",
        "buoy-a",
        "marker-a",
    }


def test_pairwise_search_prunes_large_catalog_before_building_target_pairs() -> None:
    measurements, ais = _fixture()
    distractors = tuple(
        AISRecord(f"far-{index}", 50_000.0 + index * 10.0, 60_000.0, "ship")
        for index in range(6_000)
    )
    large_catalog = AISSnapshot(
        records=ais.records + distractors,
        timestamp_utc=ais.timestamp_utc,
        crs=ais.crs,
        snapshot_id="large-pairwise-catalog",
    )

    estimate = run_pairwise_search(
        measurements,
        large_catalog,
        (-500.0, 500.0, -500.0, 500.0),
        PairwiseSearchConfig(visibility_radius_m=10_000.0, max_hypotheses=1_000),
    )

    assert np.hypot(estimate.east_m - 120.0, estimate.north_m + 80.0) < 1e-5


def test_pairwise_search_respects_types_or_can_disable_them() -> None:
    measurements, ais = _fixture()
    wrong_types = AISSnapshot(
        records=tuple(
            AISRecord(record.record_id, record.east_m, record.north_m, "other")
            for record in ais.records
        ),
        timestamp_utc=ais.timestamp_utc,
        crs=ais.crs,
        snapshot_id="all-other-types",
    )

    with pytest.raises(ValueError, match="No valid pairwise intersections"):
        run_pairwise_search(
            measurements,
            wrong_types,
            (-500.0, 500.0, -500.0, 500.0),
            PairwiseSearchConfig(use_types=True),
        )

    estimate = run_pairwise_search(
        measurements,
        wrong_types,
        (-500.0, 500.0, -500.0, 500.0),
        PairwiseSearchConfig(use_types=False),
    )
    assert np.hypot(estimate.east_m - 120.0, estimate.north_m + 80.0) < 1e-5


def test_pairwise_search_applies_water_and_line_of_sight_filters() -> None:
    measurements, ais = _fixture()
    rays: list[tuple[tuple[float, float], tuple[float, float]]] = []

    class Visibility:
        def is_water(self, east_m: float, north_m: float) -> bool:
            return east_m > 0.0

        def has_line_of_sight(
            self,
            observer_east_m: float,
            observer_north_m: float,
            target_east_m: float,
            target_north_m: float,
        ) -> bool:
            rays.append(((observer_east_m, observer_north_m), (target_east_m, target_north_m)))
            return target_east_m >= 0.0

    estimate = run_pairwise_search(
        measurements,
        ais,
        (-500.0, 500.0, -500.0, 500.0),
        PairwiseSearchConfig(max_hypotheses=1_000),
        visibility=Visibility(),
    )
    assert estimate.east_m > 0.0
    assert rays
    assert "ship-a" not in {record_id for _, record_id in estimate.matched_pairs}


def test_pairwise_search_rejects_degenerate_bearing_geometry() -> None:
    points = np.asarray([[0.0, 100.0], [100.0, 200.0]])
    with pytest.raises(ValueError, match="No valid pairwise intersections"):
        pairwise_hypothesis_search(
            [("ship", 0.0), ("ship", 0.0)],
            points,
            ["ship", "ship"],
            (-10.0, 10.0, -10.0, 10.0),
            PairwiseSearchConfig(min_crossing_angle_deg=1.0),
        )


def test_pairwise_search_requires_two_observations() -> None:
    with pytest.raises(ValueError, match="at least two observations"):
        pairwise_hypothesis_search(
            [("ship", 15.0)],
            np.asarray([[0.0, 100.0]]),
            ["ship"],
            (-10.0, 10.0, -10.0, 10.0),
        )


# Fixed historical B1 fixture. Load committed input tables directly.
ROOT = Path(__file__).resolve().parents[1]
R_EARTH_M = 6_371_008.8
ORIGIN_LAT_DEG = 38.0
ORIGIN_LON_DEG = -76.2
BOUNDS_LON_LAT = (-76.8, -75.5, 36.5, 39.5)
VISIBILITY_RANGE_M = 10_000.0
BEARING_SIGMA_DEG = 0.5


def _project_b1(lon_deg: float, lat_deg: float) -> tuple[float, float]:
    east = (
        R_EARTH_M * math.radians(lon_deg - ORIGIN_LON_DEG) * math.cos(math.radians(ORIGIN_LAT_DEG))
    )
    north = R_EARTH_M * math.radians(lat_deg - ORIGIN_LAT_DEG)
    return east, north


def _load_b1_inputs() -> tuple[
    Scene,
    MapInput,
    dict[str, tuple[float, float]],
    tuple[float, float, float, float],
]:
    with (ROOT / "data/ais/chesapeake_2023-07-15T1200Z.csv").open(newline="") as stream:
        ais_rows = list(csv.DictReader(stream))
    with (ROOT / "data/chart-aids/chesapeake-enc-aids.csv").open(newline="") as stream:
        aid_rows = list(csv.DictReader(stream))
    with (ROOT / "data/sites/b1-b5.csv").open(newline="") as stream:
        site_rows = list(csv.DictReader(stream))

    ais: list[LegacyAISRecord] = []
    landmarks: list[Landmark] = []
    for index, row in enumerate(ais_rows):
        east, north = _project_b1(float(row["longitude_deg"]), float(row["latitude_deg"]))
        ais.append(LegacyAISRecord(index, east, north, "vessel"))
        landmarks.append(Landmark(index, east, north, "vessel", True))
    for index, row in enumerate(aid_rows):
        east, north = _project_b1(float(row["longitude_deg"]), float(row["latitude_deg"]))
        object_id = len(ais_rows) + index
        landmarks.append(Landmark(object_id, east, north, "aid", False))

    x_min, y_min = _project_b1(BOUNDS_LON_LAT[0], BOUNDS_LON_LAT[2])
    x_max, y_max = _project_b1(BOUNDS_LON_LAT[1], BOUNDS_LON_LAT[3])
    scene = Scene(
        ais=tuple(ais),
        landmarks=tuple(landmarks),
        config=SceneConfig(
            width_m=x_max - x_min,
            height_m=y_max - y_min,
            object_count=len(landmarks),
            ais_fraction=len(ais) / len(landmarks),
            visibility_radius_m=VISIBILITY_RANGE_M,
            bearing_noise_sigma_deg=BEARING_SIGMA_DEG,
            report_type_labels=True,
            object_types=("vessel", "aid"),
        ),
    )

    packed = np.load(ROOT / "data/maps/chesapeake-water-mask-500m.npz")
    water = tuple(tuple(bool(cell) for cell in row) for row in packed["water"])
    map_input = MapInput(
        crs=str(packed["crs"]),
        west_m=float(packed["west_m"]),
        south_m=float(packed["south_m"]),
        cell_size_m=float(packed["cell_size_m"]),
        water_mask=water,
    )
    sites = {
        row["site_id"]: _project_b1(float(row["longitude_deg"]), float(row["latitude_deg"]))
        for row in site_rows
    }
    bounds: tuple[float, float, float, float] = (x_min, x_max, y_min, y_max)
    return scene, map_input, sites, bounds


def _b1_candidate_snapshot(run, aid_landmarks: tuple[Landmark, ...]) -> AISSnapshot:
    """Expose AIS and ENC coordinates as separate typed search candidates."""
    records = list(run.ais_snapshot.records)
    # AIS records are created in the simulator from the legacy scene records.
    # Chart aids have no AIS identifier; their separate prefix keeps that
    # distinction visible in search output and evaluation.
    if not aid_landmarks:
        raise RuntimeError("The ENC aid candidate catalog is empty.")
    for index, item in enumerate(aid_landmarks):
        records.append(AISRecord(f"aid:{index}", item.east_m, item.north_m, "aid", "ENC"))
    return AISSnapshot(
        records=tuple(records),
        timestamp_utc=run.ais_snapshot.timestamp_utc,
        crs=run.ais_snapshot.crs,
        snapshot_id=run.ais_snapshot.snapshot_id,
    )


def test_pairwise_search_realistic_b1_runtime_profile() -> None:
    scene, map_input, sites, bounds = _load_b1_inputs()
    observer = sites["B1"]
    simulated = run_simulator(
        scene,
        ShipPose(*observer, 0.0),
        seed=20_262_023,
        trial_id="B1-r1-pairwise-profile",
        angular_resolution_deg=0.1,
        map_input=map_input,
    )
    catalog = _b1_candidate_snapshot(
        simulated, tuple(item for item in scene.landmarks if not item.has_ais)
    )
    base_visibility = _RasterVisibility(map_input)

    class CountingVisibility:
        water_queries = 0
        ray_queries = 0

        def is_water(self, east_m: float, north_m: float) -> bool:
            self.water_queries += 1
            return base_visibility.is_water(east_m, north_m)

        def has_line_of_sight(
            self,
            observer_east_m: float,
            observer_north_m: float,
            target_east_m: float,
            target_north_m: float,
        ) -> bool:
            self.ray_queries += 1
            return base_visibility.has_line_of_sight(
                observer_east_m, observer_north_m, target_east_m, target_north_m
            )

    visibility = CountingVisibility()
    config = PairwiseSearchConfig(
        visibility_radius_m=10_000.0,
        use_types=True,
        penalize_missing_ais=True,
        max_hypotheses=20_000,
        max_visibility_hypotheses=128,
        refine_starts=16,
    )
    estimate = run_pairwise_search(
        simulated.measurements,
        catalog,
        bounds,
        config,
        visibility=visibility,
    )
    ais_estimate = replace(
        estimate,
        matched_pairs=tuple(pair for pair in estimate.matched_pairs if pair[1].startswith("ais:")),
    )
    metrics = evaluate_estimate(ais_estimate, simulated.truth)

    assert len(simulated.measurements.observations) == 23
    assert len(catalog.records) == 799
    assert metrics.position_error_m < 250.0
    assert metrics.correct_associations >= 3
    assert estimate.elapsed_s > 0.0
    assert visibility.water_queries <= config.max_hypotheses
    assert visibility.ray_queries <= (
        config.max_visibility_hypotheses + config.refine_starts
    ) * len(catalog.records)
    print(
        "B1 pairwise profile: "
        f"reports={len(simulated.measurements.observations)}, catalog={len(catalog.records)}, "
        f"elapsed_s={estimate.elapsed_s:.3f}, error_m={metrics.position_error_m:.2f}, "
        f"correct_AIS={metrics.correct_associations}, "
        f"water_queries={visibility.water_queries}, ray_queries={visibility.ray_queries}"
    )
