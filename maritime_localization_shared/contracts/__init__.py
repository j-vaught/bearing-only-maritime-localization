"""Versioned, program-neutral data contracts."""

from maritime_localization_shared.contracts.ais import AISRecord, AISSnapshot
from maritime_localization_shared.contracts.estimate import PositionEstimate
from maritime_localization_shared.contracts.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MapV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime_localization_shared.contracts.localization import (
    Assignment,
    Hypothesis,
    LocalizationResult,
    ScoreComponents,
    UncertaintyEllipse,
)
from maritime_localization_shared.contracts.map_input import MapInput
from maritime_localization_shared.contracts.observation import MeasurementSet, Observation
from maritime_localization_shared.contracts.truth import EvaluationTruth, TrueObject

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
