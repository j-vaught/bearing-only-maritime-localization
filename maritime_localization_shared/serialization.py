"""Deterministic JSON serialization for shared program handoffs.

Each document is one versioned contract. Measurement and truth documents are
serialized independently and may be paired by ``trial_id``. No pickle or other
executable serialization format is used.
"""

from __future__ import annotations

import json
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any, TypeVar, cast

from maritime_localization_shared.contracts.ais import AISRecord, AISSnapshot
from maritime_localization_shared.contracts.estimate import PositionEstimate
from maritime_localization_shared.contracts.map_input import MapInput
from maritime_localization_shared.contracts.observation import MeasurementSet, Observation
from maritime_localization_shared.contracts.truth import EvaluationTruth, TrueObject

Contract = TypeVar(
    "Contract", MeasurementSet, AISSnapshot, MapInput, PositionEstimate, EvaluationTruth
)
_SUPPORTED = (MeasurementSet, AISSnapshot, MapInput, PositionEstimate, EvaluationTruth)


def contract_to_dict(value: object) -> dict[str, Any]:
    """Convert one supported contract into JSON-compatible primitives."""
    if not isinstance(value, _SUPPORTED):
        raise TypeError(f"Unsupported contract type: {type(value).__name__}.")
    result = asdict(value)
    result["contract_type"] = type(value).__name__
    return result


def _strict_fields(model: type, payload: dict[str, Any]) -> None:
    accepted = {field.name for field in fields(cast(Any, model))} | {"contract_type"}
    unexpected = set(payload) - accepted
    # Dataclass constructors provide the authoritative required-field check;
    # this pass rejects misspellings and unsupported schema expansion.
    if unexpected:
        raise ValueError(f"Unexpected {model.__name__} fields: {sorted(unexpected)}.")


def contract_from_dict(model: type[Contract], payload: dict[str, Any]) -> Contract:
    """Validate and construct a supported contract from decoded JSON data."""
    if model not in _SUPPORTED:
        raise TypeError(f"Unsupported contract type: {model.__name__}.")
    if not isinstance(payload, dict):
        raise TypeError("Contract JSON root must be an object.")
    _strict_fields(model, payload)
    declared_type = payload.get("contract_type")
    if declared_type is not None and declared_type != model.__name__:
        raise ValueError(
            f"Document declares {declared_type!r}, expected {model.__name__!r}."
        )
    values = {key: value for key, value in payload.items() if key != "contract_type"}
    if model is MeasurementSet:
        values["observations"] = tuple(Observation(**item) for item in values["observations"])
    elif model is AISSnapshot:
        values["records"] = tuple(AISRecord(**item) for item in values["records"])
    elif model is EvaluationTruth:
        values["objects"] = tuple(TrueObject(**item) for item in values["objects"])
    elif model is MapInput:
        values["water_mask"] = tuple(tuple(row) for row in values["water_mask"])
    elif model is PositionEstimate and "matched_pairs" in values:
        values["matched_pairs"] = tuple(tuple(pair) for pair in values["matched_pairs"])
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
