"""Configurable 2-D maritime scene and bearing-observation simulator.

The estimator-facing objects are deliberately separate from :class:`Truth`.
Observations contain no object identity or distance.  AIS records contain only
the objects that transmit AIS, while the scene truth retains all objects.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

OBJECT_TYPES = ("ship", "buoy", "channel_marker", "shore_structure")


@dataclass(frozen=True)
class SceneConfig:
    """Parameters used to generate one static scene."""

    width_m: float = 20_000.0
    height_m: float = 20_000.0
    object_count: int = 100
    ais_fraction: float = 0.7
    visibility_radius_m: float = 5_000.0
    bearing_noise_sigma_deg: float = 0.5
    report_type_labels: bool = True
    object_types: Sequence[str] = OBJECT_TYPES


@dataclass(frozen=True)
class Landmark:
    """A stationary scene object.  ``object_id`` is truth-only."""

    object_id: int
    east_m: float
    north_m: float
    object_type: str
    has_ais: bool


@dataclass(frozen=True)
class AISRecord:
    """Estimator-visible AIS entry."""

    ais_id: int
    east_m: float
    north_m: float
    object_type: str


@dataclass(frozen=True)
class ShipPose:
    """Ship pose in the local east/north frame."""

    east_m: float
    north_m: float
    heading_deg: float


@dataclass(frozen=True)
class Observation:
    """One sensor report, without range or object identity."""

    bearing_deg: float
    object_type: str | None


@dataclass(frozen=True)
class Scene:
    """Generated scene plus the estimator-visible AIS catalog."""

    ais: tuple[AISRecord, ...]
    landmarks: tuple[Landmark, ...]
    config: SceneConfig


@dataclass(frozen=True)
class Truth:
    """Evaluation-only truth associated with one observation set."""

    ship: ShipPose
    visible_object_ids: tuple[int, ...]
    visible_ais_ids: tuple[int, ...]
    bearings_without_noise_deg: tuple[float, ...]


def _validate_config(config: SceneConfig) -> None:
    if config.width_m <= 0 or config.height_m <= 0:
        raise ValueError("Scene dimensions must be positive.")
    if config.object_count < 0:
        raise ValueError("object_count must be nonnegative.")
    if not 0.0 <= config.ais_fraction <= 1.0:
        raise ValueError("ais_fraction must be in [0, 1].")
    if config.visibility_radius_m <= 0 or config.bearing_noise_sigma_deg < 0:
        raise ValueError("Visibility radius must be positive and noise nonnegative.")
    if not config.object_types or any(t not in OBJECT_TYPES for t in config.object_types):
        raise ValueError(f"object_types must use values from {OBJECT_TYPES}.")


def generate_scene(
    config: SceneConfig | None = None, rng: np.random.Generator | None = None
) -> Scene:
    """Generate random stationary objects and expose only AIS entries."""
    config = config or SceneConfig()
    _validate_config(config)
    rng = rng or np.random.default_rng()
    ais_count = int(round(config.object_count * config.ais_fraction))
    flags = np.zeros(config.object_count, dtype=bool)
    flags[:ais_count] = True
    rng.shuffle(flags)
    landmarks = tuple(
        Landmark(
            object_id=i,
            east_m=float(rng.uniform(-config.width_m / 2, config.width_m / 2)),
            north_m=float(rng.uniform(-config.height_m / 2, config.height_m / 2)),
            object_type=str(rng.choice(config.object_types)),
            has_ais=bool(flags[i]),
        )
        for i in range(config.object_count)
    )
    ais = tuple(
        AISRecord(landmark.object_id, landmark.east_m, landmark.north_m, landmark.object_type)
        for landmark in landmarks
        if landmark.has_ais
    )
    return Scene(ais=ais, landmarks=landmarks, config=config)


def simulate_observations(
    scene: Scene,
    ship: ShipPose,
    rng: np.random.Generator | None = None,
) -> tuple[tuple[Observation, ...], Truth]:
    """Report every object within range as noisy global bearings.

    Bearings use degrees clockwise from north and are wrapped to ``[-180, 180)``.
    This assumes a calibrated sensor reference to north; ship heading is not part
    of the estimator input.
    """
    if scene.config.bearing_noise_sigma_deg < 0:
        raise ValueError("bearing noise must be nonnegative")
    rng = rng or np.random.default_rng()
    visible = []
    for landmark in scene.landmarks:
        distance = np.hypot(landmark.east_m - ship.east_m, landmark.north_m - ship.north_m)
        if distance <= scene.config.visibility_radius_m:
            absolute = np.degrees(
                np.arctan2(
                    landmark.east_m - ship.east_m,
                    landmark.north_m - ship.north_m,
                )
            )
            global_bearing = ((absolute + 180.0) % 360.0) - 180.0
            visible.append((landmark, float(global_bearing)))
    visible.sort(key=lambda item: item[1])
    clean = tuple(angle for _, angle in visible)
    noise = rng.normal(0.0, scene.config.bearing_noise_sigma_deg, len(visible))
    observations = tuple(
        Observation(
            bearing_deg=float(((angle + err + 180.0) % 360.0) - 180.0),
            object_type=landmark.object_type if scene.config.report_type_labels else None,
        )
        for (landmark, angle), err in zip(visible, noise)
    )
    truth = Truth(
        ship=ship,
        visible_object_ids=tuple(landmark.object_id for landmark, _ in visible),
        visible_ais_ids=tuple(landmark.object_id for landmark, _ in visible if landmark.has_ais),
        bearings_without_noise_deg=clean,
    )
    return observations, truth


def random_ship_pose(
    scene: Scene,
    rng: np.random.Generator | None = None,
    edge_margin_m: float = 0.0,
) -> ShipPose:
    """Sample a ship pose uniformly inside the scene, optionally away from edges."""
    rng = rng or np.random.default_rng()
    if edge_margin_m < 0 or edge_margin_m * 2 >= min(scene.config.width_m, scene.config.height_m):
        raise ValueError("edge_margin_m must be nonnegative and smaller than half the scene size.")
    return ShipPose(
        float(
            rng.uniform(
                -scene.config.width_m / 2 + edge_margin_m,
                scene.config.width_m / 2 - edge_margin_m,
            )
        ),
        float(
            rng.uniform(
                -scene.config.height_m / 2 + edge_margin_m,
                scene.config.height_m / 2 - edge_margin_m,
            )
        ),
        float(rng.uniform(-180.0, 180.0)),
    )
