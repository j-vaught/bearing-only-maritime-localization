"""A03 packaging, environment, and serialization contract checks."""

from __future__ import annotations

import importlib.util
import sys
from typing import Any, TypeVar, cast

import pytest

from maritime_localization_shared.contracts import (
    AISRecord,
    AISSnapshot,
    EvaluationTruth,
    MapInput,
    MeasurementSet,
    Observation,
    PositionEstimate,
    TrueObject,
)
from maritime_localization_shared.serialization import (
    contract_from_dict,
    contract_to_dict,
    dumps_contract,
    loads_contract,
)

ContractT = TypeVar(
    "ContractT", MeasurementSet, AISSnapshot, MapInput, PositionEstimate, EvaluationTruth
)


def _v1_contracts() -> tuple[tuple[object, type], ...]:
    return (
        (
            MeasurementSet(
                observations=(Observation(12.5, 0.5, "vessel", 0.1),),
                trial_id="v1-roundtrip",
                timestamp_utc="2026-10-25T12:00:00Z",
            ),
            MeasurementSet,
        ),
        (
            AISSnapshot(
                records=(AISRecord("opaque-1", 100.0, 200.0, "vessel", "cargo"),),
                timestamp_utc="2026-10-25T12:00:00Z",
                crs="EPSG:32618",
                snapshot_id="snapshot-1",
            ),
            AISSnapshot,
        ),
        (
            MapInput(
                crs="EPSG:32618",
                west_m=0.0,
                south_m=0.0,
                cell_size_m=10.0,
                water_mask=((True, False), (False, True)),
            ),
            MapInput,
        ),
        (
            PositionEstimate(
                east_m=30.0,
                north_m=40.0,
                score=1.5,
                elapsed_s=0.02,
                algorithm="test",
                matched_pairs=((0, "opaque-1"),),
            ),
            PositionEstimate,
        ),
        (
            EvaluationTruth(
                observer_east_m=10.0,
                observer_north_m=20.0,
                observer_heading_deg=0.0,
                objects=(TrueObject("truth-1", "vessel", 100.0, 200.0, "opaque-1", 0),),
                trial_id="v1-roundtrip",
            ),
            EvaluationTruth,
        ),
    )


def _assert_roundtrip(contract: ContractT, model: type[ContractT]) -> None:
    encoded = dumps_contract(contract)
    assert encoded == dumps_contract(contract)
    assert loads_contract(encoded, model) == contract
    assert contract_from_dict(model, contract_to_dict(contract)) == contract


def test_all_v1_contracts_have_deterministic_json_roundtrips() -> None:
    for contract, model in _v1_contracts():
        _assert_roundtrip(cast(Any, contract), cast(Any, model))


@pytest.mark.parametrize(("contract", "model"), _v1_contracts())
def test_all_v1_contracts_reject_unknown_fields(contract: object, model: type) -> None:
    payload = contract_to_dict(contract)
    payload["future_field"] = "unsupported"
    with pytest.raises(ValueError, match="Unexpected"):
        contract_from_dict(cast(type[MeasurementSet], model), payload)


def test_default_environment_has_cpu_geometry_dependencies_without_gpu_package() -> None:
    assert sys.version_info[:2] == (3, 11)
    assert importlib.util.find_spec("shapely") is not None
    assert importlib.util.find_spec("pyproj") is not None
    assert importlib.util.find_spec("cupy") is None
