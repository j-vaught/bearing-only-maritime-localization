"""Neutral data contracts shared by maritime localization programs.

The package deliberately contains no simulator, search, or evaluation logic.
Estimator-facing measurements are separate from evaluator-only truth.
"""

from maritime_localization_shared.contracts.ais import AISRecord, AISSnapshot
from maritime_localization_shared.contracts.estimate import PositionEstimate
from maritime_localization_shared.contracts.map_input import MapInput
from maritime_localization_shared.contracts.observation import MeasurementSet, Observation
from maritime_localization_shared.contracts.truth import EvaluationTruth, TrueObject
from maritime_localization_shared.serialization import (
    contract_from_dict,
    contract_to_dict,
    dumps_contract,
    loads_contract,
    read_contract,
    write_contract,
)

__all__ = [
    "AISRecord",
    "AISSnapshot",
    "EvaluationTruth",
    "MapInput",
    "MeasurementSet",
    "Observation",
    "PositionEstimate",
    "TrueObject",
    "contract_from_dict",
    "contract_to_dict",
    "dumps_contract",
    "loads_contract",
    "read_contract",
    "write_contract",
]
