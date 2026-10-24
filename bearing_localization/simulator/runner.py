"""Run one synthetic sensing trial and keep estimator inputs separate from truth."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import atan2, degrees, hypot, isfinite
from pathlib import Path

import numpy as np

from bearing_localization.scene import Landmark, Scene, ShipPose
from bearing_localization.simulator.angular_grouping import (
    DetectedObject,
    group_angular_detections,
)
from bearing_localization.simulator.measurement_model import add_bearing_noise
from maritime_localization_shared.contracts import (
    AISRecord,
    AISSnapshot,
    EvaluationTruth,
    MeasurementSet,
    TrueObject,
)
from maritime_localization_shared.contracts.map_input import MapInput
from maritime_localization_shared.geospatial import (
    LandPolygon,
    line_of_sight_clear,
    raster_line_of_sight,
)
from maritime_localization_shared.geospatial.water_mask import WaterMask
from maritime_localization_shared.serialization import write_contract


@dataclass(frozen=True, slots=True)
class SimulationRun:
    """Estimator-visible measurements and evaluator-only truth for one trial."""

    measurements: MeasurementSet
    truth: EvaluationTruth
    ais_snapshot: AISSnapshot


def run_simulator(
    scene: Scene,
    observer: ShipPose,
    *,
    seed: int = 0,
    trial_id: str | None = None,
    timestamp_utc: str | None = None,
    angular_resolution_deg: float = 0.0,
    map_input: MapInput | WaterMask | None = None,
    land_polygons: Iterable[LandPolygon] = (),
    measurements_path: str | Path | None = None,
    truth_path: str | Path | None = None,
    ais_path: str | Path | None = None,
) -> SimulationRun:
    """Generate one global-bearing report and separate truth record.

    The existing scene generator supplies stationary objects and AIS membership.
    Range and optional land visibility are applied before angular grouping. Noise
    is added after grouping so it cannot create or remove unresolved detections.
    Search-facing measurements contain no object IDs, ranges, or observer pose.

    Args:
        scene: Generated scene containing AIS and non-AIS landmarks.
        observer: Fixed observer pose in the scene's local east/north frame.
        seed: Random seed used only for measurement noise.
        trial_id: Optional key pairing this measurement set and truth record.
        timestamp_utc: Optional timezone-aware timestamp for the measurement set.
        angular_resolution_deg: Maximum circular bearing gap merged into one report.
        map_input: Optional water raster. If provided, observer must be in water
            and raster line of sight is checked for each in-range target.
        land_polygons: Optional vector land polygons checked as an additional
            reference or visibility constraint.
        measurements_path: Optional separate JSON destination for measurements.
        truth_path: Optional separate JSON destination for evaluator truth.
        ais_path: Optional separate JSON destination for the scene's AIS catalogue.

    Returns:
        A :class:`SimulationRun` with estimator-facing measurements and hidden truth.
    """
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise TypeError("seed must be an integer.")
    if not isfinite(angular_resolution_deg) or not 0.0 <= angular_resolution_deg < 180.0:
        raise ValueError("angular_resolution_deg must be finite and in [0, 180).")
    requested_paths = [
        Path(path).expanduser().resolve()
        for path in (measurements_path, truth_path, ais_path)
        if path is not None
    ]
    if len(requested_paths) != len(set(requested_paths)):
        raise ValueError("AIS, measurement, and truth outputs must use separate files.")

    observer_xy = (float(observer.east_m), float(observer.north_m))
    if map_input is not None and not _is_water(map_input, observer_xy):
        raise ValueError("Observer position must lie in a water cell of map_input.")
    polygons = tuple(land_polygons)

    visible: list[tuple[Landmark, float, float]] = []
    for landmark in scene.landmarks:
        delta_east = float(landmark.east_m - observer.east_m)
        delta_north = float(landmark.north_m - observer.north_m)
        distance = hypot(delta_east, delta_north)
        if distance > scene.config.visibility_radius_m or distance == 0.0:
            continue
        target = (float(landmark.east_m), float(landmark.north_m))
        if map_input is not None and not raster_line_of_sight(map_input, observer_xy, target):
            continue
        if polygons and not line_of_sight_clear(observer_xy, target, polygons):
            continue
        bearing = (degrees(atan2(delta_east, delta_north)) + 180.0) % 360.0 - 180.0
        visible.append((landmark, bearing, distance))

    detections = tuple(
        DetectedObject(
            object_id=str(landmark.object_id),
            object_type=str(landmark.object_type),
            bearing_deg=bearing,
            range_m=distance,
            bearing_sigma_deg=float(scene.config.bearing_noise_sigma_deg),
        )
        for landmark, bearing, distance in visible
    )
    grouped = group_angular_detections(
        detections,
        angular_resolution_deg,
        report_type_labels=bool(scene.config.report_type_labels),
    )
    rng = np.random.default_rng(seed)
    noisy_observations = tuple(
        add_bearing_noise(observation, rng) for observation in grouped.observations
    )
    measurements = MeasurementSet(
        observations=noisy_observations,
        trial_id=trial_id,
        timestamp_utc=timestamp_utc,
    )

    observation_for_object = {
        object_id: observation_index
        for observation_index, group in enumerate(grouped.truth_groups)
        for object_id in group
    }
    ais_ids = {landmark.object_id for landmark in scene.landmarks if landmark.has_ais}
    truth_objects = tuple(
        TrueObject(
            object_id=f"truth:{landmark.object_id}",
            object_type=str(landmark.object_type),
            east_m=float(landmark.east_m),
            north_m=float(landmark.north_m),
            ais_record_id=(f"ais:{landmark.object_id}" if landmark.object_id in ais_ids else None),
            observation_index=observation_for_object.get(str(landmark.object_id)),
        )
        for landmark in scene.landmarks
    )
    truth = EvaluationTruth(
        observer_east_m=float(observer.east_m),
        observer_north_m=float(observer.north_m),
        observer_heading_deg=float(observer.heading_deg),
        objects=truth_objects,
        trial_id=trial_id,
    )
    ais_snapshot = AISSnapshot(
        records=tuple(
            AISRecord(
                record_id=f"ais:{record.ais_id}",
                east_m=float(record.east_m),
                north_m=float(record.north_m),
                ais_class=str(record.object_type),
            )
            for record in scene.ais
        ),
        timestamp_utc=timestamp_utc or "2023-07-15T12:00:00Z",
        crs="local-EN",
        snapshot_id=trial_id or f"synthetic-seed-{seed}",
    )
    result = SimulationRun(
        measurements=measurements,
        truth=truth,
        ais_snapshot=ais_snapshot,
    )

    if measurements_path is not None:
        write_contract(measurements_path, measurements)
    if truth_path is not None:
        write_contract(truth_path, truth)
    if ais_path is not None:
        write_contract(ais_path, ais_snapshot)
    return result


def _is_water(map_input: MapInput | WaterMask, point: tuple[float, float]) -> bool:
    column = int(np.floor((point[0] - map_input.west_m) / map_input.cell_size_m))
    row = int(np.floor((point[1] - map_input.south_m) / map_input.cell_size_m))
    return (
        0 <= row < map_input.height_cells
        and 0 <= column < map_input.width_cells
        and map_input.water_mask[row][column]
        and (not isinstance(map_input, WaterMask) or map_input.valid[row][column])
    )


__all__ = ["SimulationRun", "run_simulator"]
