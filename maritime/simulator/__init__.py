"""Simulator-only scene, sensing, and trial-running utilities."""

from .angular_grouping import DetectedObject, GroupedDetections, group_angular_detections
from .measurement_model import (
    DetectionGeometry,
    add_bearing_noise,
    apparent_angular_half_width_deg,
    make_observation,
)
from .observer import ObserverPosition, sample_observers, to_ship_pose
from .runner import SimulationRun, run_simulator
from .scene_builder import BuiltScene, SceneBuilderConfig, build_scene, to_simulator_scene

__all__ = [
    "BuiltScene",
    "DetectedObject",
    "DetectionGeometry",
    "GroupedDetections",
    "ObserverPosition",
    "SceneBuilderConfig",
    "SimulationRun",
    "add_bearing_noise",
    "apparent_angular_half_width_deg",
    "build_scene",
    "group_angular_detections",
    "make_observation",
    "run_simulator",
    "sample_observers",
    "to_ship_pose",
    "to_simulator_scene",
]
