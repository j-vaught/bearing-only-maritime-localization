"""Deterministic JSON serialization for shared program handoffs.

Each document is one explicitly typed contract. Measurement and truth documents
are serialized independently and may be paired by trial_id. No pickle or
other executable serialization format is used.
"""

from __future__ import annotations

import json
from dataclasses import MISSING, asdict, fields
from pathlib import Path
from typing import Any, TypeVar, cast

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

Contract = TypeVar(
    "Contract",
    MeasurementSet,
    Observation,
    AISRecord,
    TrueObject,
    AISSnapshot,
    MapInput,
    PositionEstimate,
    EvaluationTruth,
    MeasurementV2,
    MeasurementSetV2,
    CatalogRecordV2,
    CatalogV2,
    MapV2,
    Assignment,
    UncertaintyEllipse,
    ScoreComponents,
    Hypothesis,
    LocalizationResult,
)
_SUPPORTED = (
    MeasurementSet,
    Observation,
    AISRecord,
    TrueObject,
    AISSnapshot,
    MapInput,
    PositionEstimate,
    EvaluationTruth,
    MeasurementV2,
    MeasurementSetV2,
    CatalogRecordV2,
    CatalogV2,
    MapV2,
    Assignment,
    UncertaintyEllipse,
    ScoreComponents,
    Hypothesis,
    LocalizationResult,
)


def contract_to_dict(value: object) -> dict[str, Any]:
    """Convert one supported contract into JSON-compatible primitives."""
    if not isinstance(value, _SUPPORTED):
        raise TypeError(f"Unsupported contract type: {type(value).__name__}.")
    result = asdict(value)
    result["contract_type"] = type(value).__name__
    return result


def _strict_fields(model: type, payload: dict[str, Any]) -> None:
    model_fields = fields(cast(Any, model))
    accepted = {field.name for field in model_fields} | {"contract_type"}
    unexpected = set(payload) - accepted
    if unexpected:
        raise ValueError(f"Unexpected {model.__name__} fields: {sorted(unexpected)}.")
    required = {
        field.name
        for field in model_fields
        if field.default is MISSING and field.default_factory is MISSING
    }
    missing = required - set(payload)
    if missing:
        raise ValueError(f"Missing {model.__name__} fields: {sorted(missing)}.")


def _nested(model: type[Contract], payload: Any) -> Contract:
    """Construct a nested model with the same strict field checks as a document."""
    if not isinstance(payload, dict):
        raise TypeError(f"{model.__name__} value must be a JSON object.")
    return contract_from_dict(model, payload)


def _nested_payloads(model: type[Contract], payloads: Any, name: str) -> tuple[Contract, ...]:
    if not isinstance(payloads, (list, tuple)):
        raise TypeError(f"{name} must be a JSON array.")
    return tuple(_nested(model, item) for item in payloads)


def contract_from_dict(model: type[Contract], payload: dict[str, Any]) -> Contract:
    """Validate and construct one supported contract from decoded JSON data."""
    if model not in _SUPPORTED:
        raise TypeError(f"Unsupported contract type: {model.__name__}.")
    if not isinstance(payload, dict):
        raise TypeError("Contract JSON root must be an object.")
    _strict_fields(model, payload)
    declared_type = payload.get("contract_type")
    if declared_type is not None and declared_type != model.__name__:
        raise ValueError(f"Document declares {declared_type!r}, expected {model.__name__!r}.")
    values = {key: value for key, value in payload.items() if key != "contract_type"}
    if model is MeasurementSet:
        values["observations"] = _nested_payloads(
            Observation, values["observations"], "observations"
        )
    elif model is AISSnapshot:
        values["records"] = _nested_payloads(AISRecord, values["records"], "records")
    elif model is EvaluationTruth:
        values["objects"] = _nested_payloads(TrueObject, values["objects"], "objects")
    elif model is MapInput:
        values["water_mask"] = tuple(tuple(row) for row in values["water_mask"])
    elif model is PositionEstimate and "matched_pairs" in values:
        values["matched_pairs"] = tuple(tuple(pair) for pair in values["matched_pairs"])
    elif model is MeasurementSetV2:
        values["measurements"] = _nested_payloads(
            MeasurementV2, values["measurements"], "measurements"
        )
        if values.get("covariance_deg2") is not None:
            values["covariance_deg2"] = tuple(tuple(row) for row in values["covariance_deg2"])
    elif model is CatalogRecordV2:
        for name in ("metadata", "provenance"):
            if name in values:
                values[name] = tuple(tuple(pair) for pair in values[name])
    elif model is CatalogV2:
        values["records"] = _nested_payloads(CatalogRecordV2, values["records"], "records")
    elif model is Hypothesis:
        values["assignments"] = _nested_payloads(
            Assignment, values.get("assignments", []), "assignments"
        )
        if values.get("uncertainty") is not None:
            values["uncertainty"] = _nested(UncertaintyEllipse, values["uncertainty"])
        if values.get("score_components") is not None:
            values["score_components"] = _nested(ScoreComponents, values["score_components"])
    elif model is LocalizationResult:
        values["hypotheses"] = _nested_payloads(Hypothesis, values["hypotheses"], "hypotheses")
    return model(**values)


def dumps_contract(value: object) -> str:
    """Return deterministic compact JSON with sorted keys and finite numbers only."""
    return json.dumps(
        contract_to_dict(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def loads_contract(document: str, model: type[Contract]) -> Contract:
    """Decode JSON and validate it as the explicitly requested contract type."""
    try:
        payload = json.loads(document, parse_constant=_reject_nonfinite)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid contract JSON.") from exc
    return contract_from_dict(model, payload)


def write_contract(path: str | Path, value: object) -> None:
    """Write a contract as UTF-8 JSON with a stable final newline."""
    Path(path).write_text(dumps_contract(value) + "\n", encoding="utf-8")


def read_contract(path: str | Path, model: type[Contract]) -> Contract:
    """Read and validate one contract JSON document from disk."""
    return loads_contract(Path(path).read_text(encoding="utf-8"), model)


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"Non-finite JSON number {value!r} is not permitted.")
