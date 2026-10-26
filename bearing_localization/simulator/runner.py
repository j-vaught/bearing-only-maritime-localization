"""Run one synthetic sensing trial and keep estimator inputs separate from truth."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import blake2b
from math import atan2, degrees, hypot, isfinite
from pathlib import Path
from typing import Any

import numpy as np
from pyproj import Geod, Transformer

from bearing_localization.scene import Landmark, Scene, ShipPose
from bearing_localization.simulator.angular_grouping import (
    DetectedObject,
    group_angular_detections,
)
from bearing_localization.simulator.measurement_model import add_bearing_noise
from bearing_localization.simulator.mismatch import (
    GroupTruthDiagnostic,
    KnownWaterObserver,
    PhysicalSceneV2,
    SensorConfigurationV2,
    SimulationDiagnosticsV2,
    SimulationRunV2,
    SimulatorMismatchConfig,
)
from maritime_localization_shared.contracts import (
    AISRecord,
    AISSnapshot,
    EvaluationTruth,
    MeasurementSet,
    TrueObject,
)
from maritime_localization_shared.contracts.inputs_v2 import MeasurementSetV2, MeasurementV2
from maritime_localization_shared.contracts.map_input import MapInput
from maritime_localization_shared.contracts.object_types import (
    is_visual_entity,
    normalize_object_type,
)
from maritime_localization_shared.geospatial import (
    LandPolygon,
    line_of_sight_clear,
    raster_line_of_sight,
)
from maritime_localization_shared.geospatial.vector_map import VectorMap
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

_GEOD = Geod(ellps="WGS84")
_DEFAULT_TIMESTAMP = "2026-10-25T12:00:00Z"
_TYPE_CHOICES = ("vessel", "physical_aid", "shoreline", "unknown")


@dataclass(frozen=True, slots=True)
class _PhysicalTarget:
    object_id: str
    catalog_key: str | None
    longitude_deg: float
    latitude_deg: float
    object_type: str
    angular_half_width_deg: float
    physical_entity_id: str | None = None
    physical_linkage_status: str = "unresolved"


def _rng(seed: int, stream: str, key: str = "") -> np.random.Generator:
    digest = blake2b(f"{stream}\0{key}".encode(), digest_size=4).digest()
    stream_key = int.from_bytes(digest, "little")
    return np.random.default_rng(np.random.SeedSequence(seed, spawn_key=(stream_key,)))


def _metadata(pairs: tuple[tuple[str, Any], ...]) -> dict[str, Any]:
    return dict(pairs)


def _offset_position(
    longitude_deg: float,
    latitude_deg: float,
    metadata: dict[str, Any],
    displacement_east_m: float = 0.0,
    displacement_north_m: float = 0.0,
) -> tuple[float, float]:
    east = float(metadata.get("visible_feature_offset_east_m", 0.0)) + displacement_east_m
    north = float(metadata.get("visible_feature_offset_north_m", 0.0)) + displacement_north_m
    if not isfinite(east) or not isfinite(north):
        raise ValueError("Visible-feature offsets and catalog displacement must be finite.")
    distance = hypot(east, north)
    if distance == 0.0:
        return longitude_deg, latitude_deg
    bearing = degrees(atan2(east, north))
    lon, lat, _ = _GEOD.fwd(longitude_deg, latitude_deg, bearing, distance)
    return float(lon), float(lat)


def _type_with_error(
    object_type: str, *, seed: int, key: str, probability: float, enabled: bool
) -> str | None:
    if not enabled:
        return None
    if _rng(seed, "type-error", key).random() >= probability:
        return object_type
    alternatives = tuple(value for value in _TYPE_CHOICES if value != object_type)
    if not alternatives:
        return object_type
    return alternatives[int(_rng(seed, "type-choice", key).integers(len(alternatives)))]


def _bearing_error(
    bearing_deg: float,
    *,
    seed: int,
    key: str,
    sigma_deg: float,
    outlier_probability: float,
    heading_bias_deg: float,
) -> float:
    if _rng(seed, "outlier", key).random() < outlier_probability:
        return float(_rng(seed, "outlier-bearing", key).uniform(0.0, 360.0))
    error = float(_rng(seed, "bearing-noise", key).normal(0.0, sigma_deg))
    return (bearing_deg + heading_bias_deg + error) % 360.0


def run_simulator_v2(
    scene: PhysicalSceneV2,
    observer: KnownWaterObserver,
    vector_map: VectorMap,
    sensor: SensorConfigurationV2,
    *,
    seed: int = 0,
    mismatch: SimulatorMismatchConfig | None = None,
    trial_id: str | None = None,
    timestamp_utc: str = _DEFAULT_TIMESTAMP,
) -> SimulationRunV2:
    """Generate a v2 measurement set plus separate evaluator-only truth and diagnostics.

    The supplied vector_map represents the search map. An optional mismatch
    observation_map changes only the physical observation LOS model. Displacement
    moves cataloged physical targets relative to the supplied catalog; uncataloged
    targets remain fixed. Global bearings are never rotated by observer heading.
    """
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise TypeError("seed must be an integer.")
    if not isinstance(scene, PhysicalSceneV2):
        raise TypeError("scene must be a PhysicalSceneV2.")
    if not isinstance(observer, KnownWaterObserver):
        raise TypeError("observer must be a KnownWaterObserver.")
    if not isinstance(vector_map, VectorMap):
        raise TypeError("vector_map must be a VectorMap.")
    if not isinstance(sensor, SensorConfigurationV2):
        raise TypeError("sensor must be a SensorConfigurationV2.")
    mismatch = mismatch or SimulatorMismatchConfig()
    if not isinstance(mismatch, SimulatorMismatchConfig):
        raise TypeError("mismatch must be a SimulatorMismatchConfig.")
    if trial_id is not None and (not isinstance(trial_id, str) or not trial_id.strip()):
        raise ValueError("trial_id must be a nonempty string or None.")
    if not isinstance(timestamp_utc, str) or not timestamp_utc.strip():
        raise ValueError("timestamp_utc must be a nonempty ISO 8601 timestamp.")

    observation_map = mismatch.observation_map or vector_map
    observer_xy = Transformer.from_crs("EPSG:4326", observation_map.crs, always_xy=True).transform(
        observer.longitude_deg, observer.latitude_deg
    )
    observer_xy = (float(observer_xy[0]), float(observer_xy[1]))
    observer_status = observation_map.point_water_status(observer_xy)
    if observer_status != "water":
        raise ValueError("Observer must lie in known water on the observation map.")

    to_map = Transformer.from_crs("EPSG:4326", observation_map.crs, always_xy=True)
    targets: list[_PhysicalTarget] = []
    for record in scene.catalog.records:
        object_type = normalize_object_type(record.normalized_type or record.raw_type)
        if not is_visual_entity(record.normalized_type or record.raw_type):
            continue
        metadata = _metadata(record.metadata)
        catalog_key = record.key
        physical_entity_id = metadata.get("physical_entity_id")
        if physical_entity_id is None:
            physical_entity_id = dict(record.provenance).get("physical_entity_id")
        physical_linkage_status = str(
            metadata.get(
                "physical_linkage_status", "resolved" if physical_entity_id else "unresolved"
            )
        )
        lon, lat = _offset_position(
            record.longitude_deg,
            record.latitude_deg,
            metadata,
            mismatch.catalog_displacement_east_m,
            mismatch.catalog_displacement_north_m,
        )
        raw_width = metadata.get("angular_half_width_deg", 0.0)
        if not isinstance(raw_width, (int, float)) or not isfinite(raw_width) or raw_width < 0.0:
            raise ValueError(f"Catalog feature {record.key!r} has invalid angular_half_width_deg.")
        targets.append(
            _PhysicalTarget(
                record.key,
                catalog_key,
                lon,
                lat,
                object_type or "unknown",
                float(raw_width),
                str(physical_entity_id) if physical_entity_id else None,
                physical_linkage_status,
            )
        )
    for item in scene.uncataloged_physical_objects:
        label = item.normalized_type or item.raw_type
        if not is_visual_entity(label):
            continue
        metadata = _metadata(item.metadata)
        lon, lat = _offset_position(item.longitude_deg, item.latitude_deg, metadata)
        raw_width = metadata.get("angular_half_width_deg", 0.0)
        if not isinstance(raw_width, (int, float)) or not isfinite(raw_width) or raw_width < 0.0:
            raise ValueError(
                f"Physical feature {item.object_id!r} has invalid angular_half_width_deg."
            )
        targets.append(
            _PhysicalTarget(
                item.object_id,
                None,
                lon,
                lat,
                normalize_object_type(label) or "unknown",
                float(raw_width),
            )
        )

    in_range: list[tuple[_PhysicalTarget, float, float]] = []
    visible: list[tuple[_PhysicalTarget, float, float]] = []
    for target in targets:
        bearing, _, distance = _GEOD.inv(
            observer.longitude_deg,
            observer.latitude_deg,
            target.longitude_deg,
            target.latitude_deg,
        )
        distance = float(distance)
        if distance == 0.0 or distance > sensor.visibility_radius_m:
            continue
        in_range.append((target, float(bearing % 360.0), distance))
        target_xy = to_map.transform(target.longitude_deg, target.latitude_deg)
        visibility = observation_map.line_visibility(
            observer_xy, (float(target_xy[0]), float(target_xy[1]))
        )
        if visibility == "clear":
            visible.append((target, float(bearing % 360.0), distance))

    detections = tuple(
        DetectedObject(
            object_id=target.object_id,
            object_type=target.object_type,
            bearing_deg=bearing,
            range_m=distance,
            bearing_sigma_deg=sensor.bearing_sigma_deg,
            angular_half_width_deg=target.angular_half_width_deg,
        )
        for target, bearing, distance in visible
    )
    grouped = group_angular_detections(
        detections,
        sensor.angular_resolution_deg,
        report_type_labels=True,
    )

    measurements: list[MeasurementV2] = []
    group_diagnostics: list[GroupTruthDiagnostic] = []
    truth_observation_for_id: dict[str, int | None] = {target.object_id: None for target in targets}
    target_by_id = {target.object_id: target for target in targets}
    detected_group_count = 0
    for group_index, member_ids in enumerate(grouped.truth_groups):
        members = [target_by_id[object_id] for object_id in member_ids]
        representative = min(
            (
                (target, distance)
                for (target, _, distance) in visible
                if target.object_id in member_ids
            ),
            key=lambda pair: (pair[1], pair[0].object_id),
        )[0]
        group_key = "|".join(sorted(member_ids))
        if _rng(seed, "detection", group_key).random() >= sensor.detection_probability:
            detected = False
            observation_index = None
        else:
            detected = True
            detected_group_count += 1
            observation_index = len(measurements)
            for object_id in member_ids:
                truth_observation_for_id[object_id] = observation_index
            measured_type = _type_with_error(
                representative.object_type,
                seed=seed,
                key=group_key,
                probability=sensor.type_error_probability,
                enabled=sensor.report_type_labels,
            )
            bearing = _bearing_error(
                grouped.observations[group_index].bearing_deg % 360.0,
                seed=seed,
                key=group_key,
                sigma_deg=sensor.bearing_sigma_deg,
                outlier_probability=sensor.outlier_probability,
                heading_bias_deg=mismatch.heading_bias_deg,
            )
            measurements.append(
                MeasurementV2(
                    bearing_deg=bearing,
                    timestamp_utc=timestamp_utc,
                    sigma_deg=sensor.bearing_sigma_deg,
                    object_type=measured_type,
                    angular_half_width_deg=representative.angular_half_width_deg,
                )
            )
        catalog_keys = tuple(
            target.catalog_key for target in members if target.catalog_key is not None
        )
        group_diagnostics.append(
            GroupTruthDiagnostic(
                member_object_ids=tuple(member_ids),
                representative_object_id=representative.object_id,
                catalog_member_keys=catalog_keys,
                representative_catalog_key=representative.catalog_key,
                catalog_member_physical_ids=tuple(
                    sorted(
                        {
                            member.physical_entity_id
                            for member in members
                            if member.physical_entity_id
                        }
                    )
                ),
                identity_limited_catalog_member_keys=tuple(
                    sorted(
                        member.catalog_key
                        for member in members
                        if member.catalog_key is not None and member.physical_entity_id is None
                    )
                ),
                detected=detected,
                observation_index=observation_index,
            )
        )

    clutter_count = int(_rng(seed, "clutter-count").poisson(sensor.expected_clutter_reports))
    clutter_rng = _rng(seed, "clutter-bearing")
    for _ in range(clutter_count):
        measurements.append(
            MeasurementV2(
                bearing_deg=float(clutter_rng.uniform(0.0, 360.0)),
                timestamp_utc=timestamp_utc,
                sigma_deg=sensor.bearing_sigma_deg,
                object_type=None,
                angular_half_width_deg=0.0,
            )
        )

    truth_objects = tuple(
        TrueObject(
            object_id=f"truth:{target.object_id}",
            object_type=target.object_type,
            east_m=float(to_map.transform(target.longitude_deg, target.latitude_deg)[0]),
            north_m=float(to_map.transform(target.longitude_deg, target.latitude_deg)[1]),
            ais_record_id=(
                f"catalog:{target.catalog_key}" if target.catalog_key is not None else None
            ),
            observation_index=truth_observation_for_id[target.object_id],
        )
        for target in targets
    )
    truth = EvaluationTruth(
        observer_east_m=observer_xy[0],
        observer_north_m=observer_xy[1],
        observer_heading_deg=observer.heading_deg,
        objects=truth_objects,
        trial_id=trial_id,
    )
    measurement_set = MeasurementSetV2(tuple(measurements), trial_id=trial_id)
    diagnostics = SimulationDiagnosticsV2(
        in_range_physical_count=len(in_range),
        line_of_sight_visible_physical_count=len(visible),
        merged_physical_group_count=len(grouped.truth_groups),
        detected_physical_group_count=detected_group_count,
        clutter_report_count=clutter_count,
        groups=tuple(group_diagnostics),
        observation_map_coverage="known",
        heading_bias_deg=mismatch.heading_bias_deg,
        catalog_displacement_east_m=mismatch.catalog_displacement_east_m,
        catalog_displacement_north_m=mismatch.catalog_displacement_north_m,
    )
    return SimulationRunV2(measurement_set, truth, diagnostics)
