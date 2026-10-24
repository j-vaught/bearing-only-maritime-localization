"""Shared geometric primitives used by independent programs."""

from .angular_grouping import DetectedObject, GroupedDetections, group_angular_detections

__all__ = ["DetectedObject", "GroupedDetections", "group_angular_detections"]
