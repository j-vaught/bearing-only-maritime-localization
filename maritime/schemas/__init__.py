"""Versioned, program-neutral data contracts."""

from maritime.schemas.ais import AISRecord, AISSnapshot
from maritime.schemas.estimate import PositionEstimate
from maritime.schemas.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MapV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime.schemas.localization import (
    Assignment,
    Hypothesis,
    LocalizationResult,
    ScoreComponents,
    UncertaintyEllipse,
)
from maritime.schemas.map_input import MapInput
from maritime.schemas.observation import MeasurementSet, Observation
from maritime.schemas.truth import EvaluationTruth, TrueObject

__all__ = [
    "AISRecord",
    "AISSnapshot",
    "Assignment",
    "CatalogRecordV2",
    "CatalogV2",
    "EvaluationTruth",
    "Hypothesis",
    "LocalizationResult",
    "MapInput",
    "MapV2",
    "MeasurementSet",
    "MeasurementSetV2",
    "MeasurementV2",
    "Observation",
    "PositionEstimate",
    "ScoreComponents",
    "TrueObject",
    "UncertaintyEllipse",
]
