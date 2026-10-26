from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys

import pytest

from bearing_localization.accuracy_cli import (
    _candidate_outcome_evidence,
    _distant_region_bound_evidence,
    evaluate_v2_main,
)
from bearing_localization.search.config import SearchConfig
from bearing_localization.search.config_serialization import (
    loads_search_config,
    search_config_from_dict,
    search_config_to_dict,
    write_search_config,
)
from maritime_localization_shared.contracts import (
    EvaluationTruth,
    Hypothesis,
    LocalizationResult,
    MeasurementSet,
    Observation,
    PositionEstimate,
    TrueObject,
)
from maritime_localization_shared.serialization import (
    contract_from_dict,
    contract_to_dict,
    loads_contract,
    read_contract,
    write_contract,
)


def test_evaluated_unresolved_outcomes_are_counted_without_branch_sentinels() -> None:
    counts, unresolved = _candidate_outcome_evidence(
        {
            "candidate_count": 10,
            "outcome_counts": {
                "scored": 4,
                "invalid_water": 2,
                "unresolved_coverage": 1,
                "unresolved_model": 3,
            },
            "unresolved_regions": 999,
        }
    )
    assert counts == {
        "scored": 4,
        "invalid_water": 2,
        "unresolved_coverage": 1,
        "unresolved_model": 3,
    }
    assert unresolved == 4
    assert _candidate_outcome_evidence(
        {
            "candidate_count": 11,
            "outcome_counts": {
                "scored": 4,
                "invalid_water": 2,
                "unresolved_coverage": 1,
                "unresolved_model": 3,
            },
        }
    ) == (None, None)
    assert _candidate_outcome_evidence({"unresolved_regions": 999}) == (None, None)


def test_distant_bound_requires_explicit_margin_aware_scope() -> None:
    valid = {
        "distant_region_score_lower_bound": 8.0,
        "distant_region_bound_scope": "all_objective_positions_at_least_100m_from_top",
        "distant_region_bound_top_east_m": 1.0,
        "distant_region_bound_top_north_m": 2.0,
    }
    assert _distant_region_bound_evidence(valid) == (
        8.0,
        "all_objective_positions_at_least_100m_from_top",
        1.0,
        2.0,
    )
    assert _distant_region_bound_evidence({"distant_region_score_lower_bound": 8.0}) == (
        None,
        None,
        None,
        None,
    )
    assert _distant_region_bound_evidence(
        {
            "candidate_count": 10,
            "distant_region_score_lower_bound": float("nan"),
            "distant_region_bound_scope": "all_objective_positions_at_least_100m_from_top",
        }
    ) == (None, None, None, None)


def test_v2_evaluator_cli_passes_explicit_crs_for_geodesic_error(tmp_path) -> None:
    estimate = tmp_path / "estimate.json"
    truth = tmp_path / "truth.json"
    support = tmp_path / "support.json"
    output = tmp_path / "metrics.json"
    write_contract(
        estimate,
        LocalizationResult(
            hypotheses=(Hypothesis(0.0, 0.0, 0.0),),
            elapsed_s=0.1,
            decision_status="accepted",
            search_status="completed",
            accepted_index=0,
        ),
    )
    write_contract(
        truth,
        EvaluationTruth(1.0, 0.0, 0.0, ()),
    )
    support.write_text(
        json.dumps(
            {
                "valid_reference_water": True,
                "detected_physical_catalog_matched_groups": 3,
            }
        ),
        encoding="utf-8",
    )

    assert (
        evaluate_v2_main(
            [
                "--estimate",
                str(estimate),
                "--truth",
                str(truth),
                "--support",
                str(support),
                "--output",
                str(output),
                "--coordinate-crs",
                "EPSG:4326",
            ]
        )
        == 0
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["position_error_m"] == pytest.approx(111319.490793, abs=0.01)


def test_completed_result_can_use_final_budget_unit_but_exhaustion_cannot_be_accepted() -> None:
    hypothesis = Hypothesis(1.0, 2.0, 0.0)
    completed = LocalizationResult(
        (hypothesis,),
        0.1,
        "accepted",
        "completed",
        accepted_index=0,
        budget_limit=10,
        budget_used=10,
    )
    assert completed.accepted_index == 0

    with pytest.raises(ValueError, match="unless the search completed"):
        LocalizationResult(
            (hypothesis,),
            0.1,
            "accepted",
            "budget_exhausted",
            accepted_index=0,
            budget_limit=10,
            budget_used=10,
        )


def test_legacy_v1_json_without_optional_tags_remains_readable() -> None:
    old_document = {
        "east_m": 10.0,
        "north_m": 20.0,
        "score": 1.0,
        "elapsed_s": 0.01,
        "algorithm": "historical-grid",
        "matched_pairs": [[0, "legacy-feature"]],
    }
    estimate = contract_from_dict(PositionEstimate, old_document)
    assert estimate == PositionEstimate(
        10.0, 20.0, 1.0, 0.01, "historical-grid", ((0, "legacy-feature"),)
    )

    measurement = MeasurementSet(
        observations=(Observation(15.0, 0.5, "vessel", 0.1),),
        trial_id="old-trial",
        timestamp_utc="2026-10-25T12:00:00Z",
    )
    assert loads_contract(json.dumps(contract_to_dict(measurement)), MeasurementSet) == measurement


def test_strict_readers_reject_unknown_nested_fields_and_nonfinite_json(tmp_path) -> None:
    truth = EvaluationTruth(1.0, 2.0, 3.0, (TrueObject("t", "vessel", 4.0, 5.0),))
    payload = contract_to_dict(truth)
    payload["objects"][0]["future_field"] = "x"
    with pytest.raises(ValueError, match="Unexpected TrueObject fields"):
        contract_from_dict(EvaluationTruth, payload)

    bad = tmp_path / "nan.json"
    bad.write_text('{"east_m":NaN}')
    with pytest.raises(ValueError, match="Non-finite JSON"):
        read_contract(bad, PositionEstimate)


def test_search_config_rejects_untagged_or_unknown_documents() -> None:
    config = SearchConfig(bounds_xy=(0.0, 10.0, 0.0, 10.0))
    tagged = search_config_to_dict(config)
    with pytest.raises(ValueError, match="contract_type"):
        search_config_from_dict(
            {key: value for key, value in tagged.items() if key != "contract_type"}
        )
    tagged["future_field"] = 1
    with pytest.raises(ValueError, match="Unexpected SearchConfig"):
        search_config_from_dict(tagged)
    with pytest.raises(ValueError, match="Non-finite JSON"):
        loads_search_config('{"contract_type":"SearchConfig","candidate_budget":NaN}')


def test_malformed_cli_stages_fail_cleanly_and_gpu_requires_explicit_devices(tmp_path) -> None:
    fixture = tmp_path / "bad-scene.json"
    fixture.write_text("{}")
    measurements = tmp_path / "measurements.json"
    truth = tmp_path / "truth.json"
    diagnostics = tmp_path / "diagnostics.json"
    malformed = subprocess.run(
        [
            sys.executable,
            "-m",
            "bearing_localization.simulator.cli",
            "--schema-version",
            "2",
            "--fixture",
            str(fixture),
            "--projected-crs",
            "EPSG:32618",
            "--measurements-out",
            str(measurements),
            "--truth-out",
            str(truth),
            "--diagnostics-out",
            str(diagnostics),
        ],
        capture_output=True,
        text=True,
    )
    assert malformed.returncode == 2
    assert "KeyError" not in malformed.stderr
    assert not measurements.exists()
    assert not truth.exists()
    assert not diagnostics.exists()

    config_path = tmp_path / "gpu-config.json"
    write_search_config(
        config_path,
        SearchConfig(backend="gpu", bounds_xy=(0.0, 10.0, 0.0, 10.0)),
    )
    result_path = tmp_path / "gpu-result.json"
    unsupported = subprocess.run(
        [
            sys.executable,
            "-m",
            "bearing_localization.search_cli",
            "--schema-version",
            "2",
            "--measurements",
            str(tmp_path / "missing-measurements.json"),
            "--catalog",
            str(tmp_path / "missing-catalog.json"),
            "--map",
            str(tmp_path / "missing-map.json"),
            "--config",
            str(config_path),
            "--projected-crs",
            "EPSG:32618",
            "--output",
            str(result_path),
        ],
        capture_output=True,
        text=True,
    )
    assert unsupported.returncode == 2
    assert "requires one or more explicit --device-id values" in unsupported.stderr
    assert not result_path.exists()


def test_missing_required_contract_fields_fail_as_schema_errors() -> None:
    with pytest.raises(ValueError, match="Missing PositionEstimate fields"):
        contract_from_dict(PositionEstimate, {"east_m": 1.0, "north_m": 2.0})


def test_v2_pipeline_forwards_each_gpu_device_id_and_keeps_truth_out_of_search(
    tmp_path, monkeypatch
) -> None:
    main_path = __import__("pathlib").Path(__file__).resolve().parents[2] / "main.py"
    spec = importlib.util.spec_from_file_location("pipeline_main", main_path)
    assert spec is not None and spec.loader is not None
    pipeline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pipeline)

    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if any("evaluate_cli" in argument for argument in command):
            output = __import__("pathlib").Path(command[command.index("--output") + 1])
            output.write_text('{"status":"ok"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    args = argparse.Namespace(
        measurements=tmp_path / "measurements.json",
        catalog=tmp_path / "catalog.json",
        map_path=tmp_path / "map.json",
        config=tmp_path / "config.json",
        truth=tmp_path / "truth.json",
        support=tmp_path / "support.json",
        calibration=None,
        estimate_output=tmp_path / "estimate.json",
        metrics_output=tmp_path / "metrics.json",
        projected_crs="EPSG:32618",
        spacing_m=100.0,
        seed=0,
        device_id=[0, 2],
        algorithm="grid",
        no_refine=True,
        scope_manifest=None,
        max_grid_bearing_error_deg=None,
    )

    assert pipeline.run_pipeline_v2(args) == {"status": "ok"}
    search_command = commands[0]
    assert search_command.count("--device-id") == 2
    first_device = search_command.index("--device-id")
    second_device = search_command.index("--device-id", first_device + 1)
    assert search_command[first_device + 1] == "0"
    assert search_command[second_device + 1] == "2"
    assert str(args.truth) not in search_command
    assert str(args.support) not in search_command
