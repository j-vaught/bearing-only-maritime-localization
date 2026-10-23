"""Build reproducible local east/north scenes with separate AIS and truth data.

The AIS snapshot is the estimator-visible catalogue. ``truth_objects`` retains
all generated objects and uses identifiers that are deliberately distinct
from AIS identifiers, so evaluator truth cannot be mistaken for AIS keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.random import Generator, default_rng

from bearing_localization.scene import (
    AISRecord as LegacyAISRecord,
)
from bearing_localization.scene import (
    Landmark,
    Scene,
    SceneConfig,
)
from maritime_localization_shared.contracts import AISRecord, AISSnapshot, TrueObject

DEFAULT_OBJECT_TYPES: Final[tuple[str, ...]] = (
    "ship",
    "buoy",
    "channel_marker",
    "shore_structure",
)


@dataclass(frozen=True, slots=True)
class SceneBuilderConfig:
    """Counts and bounds for a rectangular local east/north scene."""

    east_bounds_m: tuple[float, float] = (-10_000.0, 10_000.0)
    north_bounds_m: tuple[float, float] = (-10_000.0, 10_000.0)
    ais_count: int = 50
    non_ais_count: int = 25
    object_types: tuple[str, ...] = DEFAULT_OBJECT_TYPES
    crs: str = "local-EN"
    timestamp_utc: str = "2023-07-15T12:00:00Z"

    def __post_init__(self) -> None:
        _validate_bounds("east_bounds_m", self.east_bounds_m)
        _validate_bounds("north_bounds_m", self.north_bounds_m)
        if self.ais_count < 0 or self.non_ais_count < 0:
            raise ValueError("Object counts must be nonnegative.")
        if not self.object_types or any(not kind.strip() for kind in self.object_types):
            raise ValueError("object_types must contain nonempty type names.")
        if len(set(self.object_types)) != len(self.object_types):
            raise ValueError("object_types must not contain duplicates.")
        if not self.crs.strip():
            raise ValueError("crs must identify the local coordinate frame.")


@dataclass(frozen=True, slots=True)
class BuiltScene:
    """One scene split into estimator-visible AIS and evaluator truth."""

    ais_snapshot: AISSnapshot
    non_ais_objects: tuple[TrueObject, ...]
    truth_objects: tuple[TrueObject, ...]
    config: SceneBuilderConfig


def build_scene(
    config: SceneBuilderConfig | None = None,
    *,
    seed: int,
) -> BuiltScene:
    """Build the same scene for the same config and seed.

    AIS identifiers use the ``ais:`` namespace. Truth identifiers use the
    separate ``truth:`` namespace. AIS objects appear in both the AIS snapshot
    and the complete truth tuple; non-AIS objects appear only in truth.
    """
    selected = config or SceneBuilderConfig()
    rng = default_rng(seed)
    ais_objects = tuple(
        _make_object(rng, selected, "ais", index, has_ais=True)
        for index in range(selected.ais_count)
    )
    non_ais_objects = tuple(
        _make_object(rng, selected, "non-ais", index, has_ais=False)
        for index in range(selected.non_ais_count)
    )

    records = tuple(
        AISRecord(
            record_id=f"ais-record:{index:06d}",
            east_m=obj.east_m,
            north_m=obj.north_m,
            ais_class=obj.object_type,
        )
        for index, obj in enumerate(ais_objects)
    )
    all_truth = ais_objects + non_ais_objects
    snapshot = AISSnapshot(
        records=records,
        timestamp_utc=selected.timestamp_utc,
        crs=selected.crs,
        snapshot_id=f"synthetic-scene:{seed}",
    )
    return BuiltScene(
        ais_snapshot=snapshot,
        non_ais_objects=non_ais_objects,
        truth_objects=all_truth,
        config=selected,
    )


def to_simulator_scene(
    built_scene: BuiltScene,
    *,
    visibility_radius_m: float,
    bearing_noise_sigma_deg: float,
    report_type_labels: bool = True,
) -> Scene:
    """Adapt a contract-based ``BuiltScene`` to the established runner API.

    The adapter keeps the legacy runner stable while allowing the new scene
    builder and shared AIS/truth contracts to compose with it. Source AIS keys
    are mapped to scene-local integer IDs; the runner emits a fresh opaque AIS
    snapshot for the estimator.
    """
    if not isinstance(built_scene, BuiltScene):
        raise TypeError("built_scene must be a BuiltScene.")
    if not np.isfinite(visibility_radius_m) or visibility_radius_m <= 0.0:
        raise ValueError("visibility_radius_m must be finite and positive.")
    if not np.isfinite(bearing_noise_sigma_deg) or bearing_noise_sigma_deg < 0.0:
        raise ValueError("bearing_noise_sigma_deg must be finite and nonnegative.")
    if not isinstance(report_type_labels, bool):
        raise TypeError("report_type_labels must be a bool.")

    ais_record_ids = {record.record_id for record in built_scene.ais_snapshot.records}
    unknown_ais_ids = {
        item.ais_record_id
        for item in built_scene.truth_objects
        if item.ais_record_id is not None and item.ais_record_id not in ais_record_ids
    }
    if unknown_ais_ids:
        raise ValueError(f"Truth contains unknown AIS record IDs: {sorted(unknown_ais_ids)}")

    object_ids = {item.object_id: index for index, item in enumerate(built_scene.truth_objects)}
    landmarks = tuple(
        Landmark(
            object_id=index,
            east_m=float(item.east_m),
            north_m=float(item.north_m),
            object_type=item.object_type,
            has_ais=item.ais_record_id is not None,
        )
        for index, item in enumerate(built_scene.truth_objects)
    )
    ais = tuple(
        LegacyAISRecord(
            ais_id=object_ids[item.object_id],
            east_m=float(item.east_m),
            north_m=float(item.north_m),
            object_type=item.object_type,
        )
        for item in built_scene.truth_objects
        if item.ais_record_id is not None
    )
    config = SceneConfig(
        width_m=built_scene.config.east_bounds_m[1] - built_scene.config.east_bounds_m[0],
        height_m=built_scene.config.north_bounds_m[1] - built_scene.config.north_bounds_m[0],
        object_count=len(landmarks),
        ais_fraction=len(ais) / len(landmarks) if landmarks else 0.0,
        visibility_radius_m=visibility_radius_m,
        bearing_noise_sigma_deg=bearing_noise_sigma_deg,
        report_type_labels=report_type_labels,
        object_types=tuple(dict.fromkeys(item.object_type for item in landmarks))
        or DEFAULT_OBJECT_TYPES,
    )
    return Scene(ais=ais, landmarks=landmarks, config=config)


def _make_object(
    rng: Generator,
    config: SceneBuilderConfig,
    namespace: str,
    index: int,
    *,
    has_ais: bool,
) -> TrueObject:
    east = float(rng.uniform(*config.east_bounds_m))
    north = float(rng.uniform(*config.north_bounds_m))
    return TrueObject(
        object_id=f"truth:{namespace}:{index:06d}",
        object_type=str(rng.choice(config.object_types)),
        east_m=east,
        north_m=north,
        ais_record_id=f"ais-record:{index:06d}" if has_ais else None,
    )


def _validate_bounds(name: str, bounds: tuple[float, float]) -> None:
    if len(bounds) != 2 or not np.isfinite(bounds).all() or bounds[0] >= bounds[1]:
        raise ValueError(f"{name} must be a finite (minimum, maximum) pair.")
