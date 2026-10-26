"""A03 packaging, environment, and serialization contract checks."""

from __future__ import annotations

import importlib.util
import sys
from typing import Any, TypeVar, cast

import pytest

from bearing_localization.search.config import SearchConfig
from bearing_localization.search.config_serialization import (
    dumps_search_config,
    loads_search_config,
    search_config_from_dict,
    search_config_to_dict,
)
from maritime_localization_shared import (
    AISRecord,
    AISSnapshot,
    Assignment,
    CatalogRecordV2,
    CatalogV2,
    EvaluationTruth,
    Hypothesis,
    LocalizationResult,
    MapInput,
    MapV2,
    MeasurementSet,
    MeasurementSetV2,
    MeasurementV2,
    Observation,
    PositionEstimate,
    ScoreComponents,
    TrueObject,
    UncertaintyEllipse,
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


def _v2_contracts() -> tuple[tuple[object, type], ...]:
    measurement = MeasurementV2(12.0, "2026-10-25T12:00:00Z", sigma_deg=0.5)
    catalog_record = CatalogRecordV2(
        "feature-1",
        36.9,
        -76.3,
        raw_type="6",
        normalized_type="physical_aid",
        metadata=(("source", "chart"), ("quality", 0.9)),
        provenance=(("edition", "2026"),),
    )
    hypothesis = Hypothesis(
        10.0,
        20.0,
        3.0,
        assignments=(Assignment(0, "feature-1", 0.1, 1.0),),
        uncertainty=UncertaintyEllipse(3.0, 2.0, 45.0),
        score_components=ScoreComponents(1.0, 1.0, 1.0, 3.0),
    )
    return (
        (measurement, MeasurementV2),
        (
            MeasurementSetV2(
                (measurement,),
                trial_id="v2-roundtrip",
                covariance_deg2=((0.25,),),
                calibrated_common_bias_mean_deg=0.0,
                calibrated_common_bias_sigma_deg=0.1,
            ),
            MeasurementSetV2,
        ),
        (catalog_record, CatalogRecordV2),
        (
            CatalogV2(
                (catalog_record,),
                "2026-10-25T12:00:00Z",
                "ENC",
                "2026",
                "catalog-1",
            ),
            CatalogV2,
        ),
        (
            MapV2(crs="EPSG:4326", source="test", map_version="v1"),
            MapV2,
        ),
        (Assignment(0, "feature-1", 0.1, 1.0), Assignment),
        (UncertaintyEllipse(3.0, 2.0, 45.0), UncertaintyEllipse),
        (ScoreComponents(1.0, 1.0, 1.0, 3.0), ScoreComponents),
        (hypothesis, Hypothesis),
        (
            LocalizationResult(
                (hypothesis,),
                0.25,
                "accepted",
                "completed",
                certificate_scope="finite_candidate_set",
                accepted_index=0,
                report_count=1,
            ),
            LocalizationResult,
        ),
    )


def _assert_roundtrip(contract: ContractT, model: type[ContractT]) -> None:
    encoded = dumps_contract(contract)
    assert encoded == dumps_contract(contract)
    assert loads_contract(encoded, model) == contract
    assert contract_from_dict(model, contract_to_dict(contract)) == contract


def _assert_generic_roundtrip(contract: Any, model: Any) -> None:
    encoded = dumps_contract(contract)
    assert encoded == dumps_contract(contract)
    assert loads_contract(encoded, model) == contract
    assert contract_from_dict(model, contract_to_dict(contract)) == contract


def test_all_v1_contracts_have_deterministic_json_roundtrips() -> None:
    for contract, model in _v1_contracts():
        _assert_roundtrip(cast(Any, contract), cast(Any, model))


def test_all_v2_contracts_have_deterministic_nested_json_roundtrips() -> None:
    for contract, model in _v2_contracts():
        _assert_generic_roundtrip(contract, model)


@pytest.mark.parametrize(("contract", "model"), _v1_contracts() + _v2_contracts())
def test_all_contract_versions_reject_unknown_fields(contract: object, model: type) -> None:
    payload = contract_to_dict(contract)
    payload["future_field"] = "unsupported"
    with pytest.raises(ValueError, match="Unexpected"):
        contract_from_dict(cast(Any, model), payload)


def test_nested_v1_fields_are_rejected_without_changing_v1_roundtrips() -> None:
    payload = contract_to_dict(_v1_contracts()[0][0])
    payload["observations"][0]["future_field"] = "unsupported"
    with pytest.raises(ValueError, match="Unexpected Observation fields"):
        contract_from_dict(MeasurementSet, payload)

    payload = contract_to_dict(_v1_contracts()[1][0])
    payload["records"][0]["future_field"] = "unsupported"
    with pytest.raises(ValueError, match="Unexpected AISRecord fields"):
        contract_from_dict(AISSnapshot, payload)


def test_nested_v2_fields_and_mismatched_document_tags_are_rejected() -> None:
    payload = contract_to_dict(_v2_contracts()[-1][0])
    payload["hypotheses"][0]["assignments"][0]["future_field"] = "unsupported"
    with pytest.raises(ValueError, match="Unexpected"):
        contract_from_dict(LocalizationResult, payload)

    payload = contract_to_dict(_v2_contracts()[0][0])
    payload["contract_type"] = "CatalogRecordV2"
    with pytest.raises(ValueError, match="expected 'MeasurementV2'"):
        contract_from_dict(MeasurementV2, payload)


def test_search_config_has_a_separate_strict_roundtrip_adapter() -> None:
    config = SearchConfig(
        bounds_xy=(0.0, 100.0, 10.0, 200.0),
        common_bearing_bias_prior_deg=(0.1, 0.2),
    )
    encoded = dumps_search_config(config)
    assert encoded == dumps_search_config(config)
    assert loads_search_config(encoded) == config
    assert search_config_from_dict(search_config_to_dict(config)) == config

    payload = search_config_to_dict(config)
    payload["future_field"] = "unsupported"
    with pytest.raises(ValueError, match="Unexpected SearchConfig"):
        search_config_from_dict(payload)


def test_default_environment_has_cpu_geometry_dependencies_without_gpu_package() -> None:
    assert sys.version_info[:2] == (3, 11)
    assert importlib.util.find_spec("shapely") is not None
    assert importlib.util.find_spec("pyproj") is not None
    assert importlib.util.find_spec("cupy") is None
