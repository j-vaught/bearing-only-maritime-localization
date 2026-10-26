"""Simulator-facing import path for the shared angular grouping primitive."""

from maritime.geometry.angular_grouping import (
    DetectedObject,
    GroupedDetections,
    group_angular_detections,
)

__all__ = ["DetectedObject", "GroupedDetections", "group_angular_detections"]
