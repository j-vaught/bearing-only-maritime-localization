"""Simulator-facing import path for the shared angular grouping primitive."""

from maritime_localization_shared.geometry.angular_grouping import (
    DetectedObject,
    GroupedDetections,
    group_angular_detections,
)

__all__ = ["DetectedObject", "GroupedDetections", "group_angular_detections"]
