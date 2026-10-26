from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from dataclasses import replace
from typing import Any, cast

import pytest
from pyproj import Geod, Transformer

from bearing_localization.accuracy_cli import (
    SearchV2Session,
    _calibration_binding,
    _candidate_outcome_evidence,
    _distant_region_bound_evidence,
    _order_geodesic_proposals_for_scoring,
    _order_wedge_proposals_for_scoring,
    _run_jsonl_searches,
    evaluate_v2_files,
    evaluate_v2_main,
    search_v2_files,
    search_v2_main,
)
from bearing_localization.search.config import SearchConfig
from bearing_localization.search.config_serialization import (
    loads_search_config,
    search_config_from_dict,
    search_config_to_dict,
    write_search_config,
)
from bearing_localization.search.proposals.geodesic_wedge import GeodesicWedgeProposal
from bearing_localization.search.proposals.wedge import (
    WedgeCatalogObject,
    WedgeObservation,
    WedgeProposal,
    generate_reverse_wedge_proposals_complete,
)
from maritime_localization_shared.contracts import (
    CatalogV2,
    EvaluationTruth,
    Hypothesis,
    LocalizationResult,
    MapV2,
    MeasurementSet,
    Observation,
    PositionEstimate,
    TrueObject,
)
from maritime_localization_shared.contracts.inputs_v2 import (
    CatalogRecordV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime_localization_shared.geospatial.coordinates import LocalFrame
from maritime_localization_shared.serialization import (
    contract_from_dict,
    contract_to_dict,
    loads_contract,
    read_contract,
    write_contract,
)


def test_search_sidecar_emits_versioned_calibration_context_without_truth(tmp_path) -> None:
    timestamp = "2026-10-25T12:00:00Z"
    measurements = tmp_path / "measurements.json"
    catalog = tmp_path / "catalog.json"
    map_path = tmp_path / "map.json"
    config_path = tmp_path / "config.json"
    output = tmp_path / "estimate.json"
    write_contract(measurements, MeasurementSetV2((), trial_id="context-trial"))
    write_contract(
        catalog,
        CatalogV2((), timestamp, "context-test", "1", "context-catalog"),
    )
    ring = [[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0], [0.0, 0.0]]
    write_contract(
        map_path,
        MapV2(
            crs="EPSG:32618",
            source="context-test",
            map_version="1",
            coverage_status="known",
            coverage_geometry_geojson=json.dumps(
                {"type": "Polygon", "coordinates": [ring]}, separators=(",", ":")
            ),
        ),
    )
    write_search_config(
        config_path,
        SearchConfig(
            algorithm="grid",
            bounds_xy=(10.0, 20.0, 10.0, 20.0),
            candidate_budget=4,
        ),
    )

    search_v2_files(
        measurements,
        catalog,
        map_path,
        config_path,
        "EPSG:32618",
        output,
        spacing_m=10.0,
        refine=False,
    )
    telemetry = json.loads(output.with_suffix(".json.telemetry.json").read_text())
    evidence = telemetry["diagnostics"]["public_evidence"]
    assert evidence["calibration_context_schema"] == "calibration-context-v2"
    assert evidence["calibration_condition"] == {
        "range_m": 10000.0,
        "use_types": True,
        "objective_version": "bearing_set_map_v1",
        "algorithm": "grid",
    }
    context = evidence["calibration_context"]
    assert set(context) == {
        "catalog_sha256",
        "map_sha256",
        "scope_manifest_sha256",
        "search_execution_sha256",
        "implementation_sha256",
    }
    assert all(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
        for value in context.values()
    )
    assert context["catalog_sha256"] == hashlib.sha256(catalog.read_bytes()).hexdigest()
    assert context["map_sha256"] == hashlib.sha256(map_path.read_bytes()).hexdigest()
    assert evidence["search_config_sha256"] == hashlib.sha256(config_path.read_bytes()).hexdigest()
    assert evidence["seed_policy"] == "fixed"
    assert evidence["seed"] == 0
    assert evidence["scope_manifest_verified"] is False
    assert evidence["calibration_binding_status"] == "not_requested"
    assert not (tmp_path / "truth.json").exists()


def test_calibration_binding_is_path_independent_and_records_seed_policy(tmp_path) -> None:
    catalog_a = tmp_path / "a" / "catalog.json"
    map_a = tmp_path / "a" / "map.json"
    catalog_b = tmp_path / "b" / "catalog.json"
    map_b = tmp_path / "b" / "map.json"
    catalog_a.parent.mkdir()
    catalog_b.parent.mkdir()
    catalog_a.write_bytes(b"catalog contents")
    map_a.write_bytes(b"map contents")
    catalog_b.write_bytes(catalog_a.read_bytes())
    map_b.write_bytes(map_a.read_bytes())
    config = SearchConfig(
        algorithm="grid",
        bounds_xy=(0.0, 10.0, 0.0, 10.0),
        max_range_m=5000.0,
        use_types=False,
    )

    scope_manifest = tmp_path / "scope.json"
    scope_manifest.write_bytes(b"verified scope manifest")

    def bind(
        catalog_path,
        map_path,
        *,
        scope_manifest_path=None,
        seed=17,
        seed_policy="fixed",
        spacing_m=50.0,
        config_override=None,
    ):
        return _calibration_binding(
            config=config if config_override is None else config_override,
            algorithm="grid",
            projected_crs="EPSG:32618",
            catalog_path=catalog_path,
            map_path=map_path,
            scope_manifest_path=scope_manifest_path,
            spacing_m=spacing_m,
            seed=seed,
            seed_policy=seed_policy,
            wedge_error_deg=None,
            wedge_tolerance_deg=0.5,
            wedge_cell_size_m=250.0,
            branch_max_regions=999,
            refine=False,
            refine_modes_count=8,
            refine_initial_step_m=100.0,
            refine_iterations=50,
            device_ids=(),
        )

    condition_a, context_a = bind(catalog_a, map_a)
    condition_b, context_b = bind(catalog_b, map_b)
    assert condition_a.as_dict() == {
        "range_m": 5000.0,
        "use_types": False,
        "objective_version": config.objective_version,
        "algorithm": "grid",
    }
    assert context_a.as_dict() == context_b.as_dict()
    assert context_a.catalog_sha256 == hashlib.sha256(b"catalog contents").hexdigest()
    assert context_a.map_sha256 == hashlib.sha256(b"map contents").hexdigest()
    assert len(context_a.scope_manifest_sha256) == 64

    _, fixed_other_seed = bind(catalog_a, map_a, seed=18)
    assert context_a.search_execution_sha256 != fixed_other_seed.search_execution_sha256
    _, variable_seed_a = bind(catalog_a, map_a, seed=17, seed_policy="per_trial_variable")
    _, variable_seed_b = bind(catalog_a, map_a, seed=18, seed_policy="per_trial_variable")
    assert variable_seed_a.search_execution_sha256 == variable_seed_b.search_execution_sha256
    _, scoped_context = bind(catalog_a, map_a, scope_manifest_path=scope_manifest)
    assert (
        scoped_context.scope_manifest_sha256
        == hashlib.sha256(scope_manifest.read_bytes()).hexdigest()
    )
    _, changed_spacing = bind(catalog_a, map_a, spacing_m=25.0)
    assert context_a.search_execution_sha256 != changed_spacing.search_execution_sha256

    for field, value in (
        ("proposal_source_pair_budget", 10),
        ("proposal_cell_budget", 10),
        ("proposal_chunk_size", 10),
    ):
        changed_condition, changed_context = bind(
            catalog_a,
            map_a,
            config_override=replace(config, **{field: value}),
        )
        assert changed_condition == condition_a
        assert changed_context.search_execution_sha256 != context_a.search_execution_sha256


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
        seed_policy="per_trial_variable",
        device_id=[0, 2],
        algorithm="grid",
        no_refine=True,
        scope_manifest=None,
        max_grid_bearing_error_deg=None,
    )

    assert pipeline.run_pipeline_v2(args) == {"status": "ok"}
    search_command = commands[0]
    assert search_command[search_command.index("--seed-policy") + 1] == "per_trial_variable"
    assert search_command.count("--device-id") == 2
    first_device = search_command.index("--device-id")
    second_device = search_command.index("--device-id", first_device + 1)
    assert search_command[first_device + 1] == "0"
    assert search_command[second_device + 1] == "2"
    assert str(args.truth) not in search_command
    assert str(args.support) not in search_command

    commands.clear()
    del args.seed_policy
    assert pipeline.run_pipeline_v2(args) == {"status": "ok"}
    default_search_command = commands[0]
    assert default_search_command[default_search_command.index("--seed-policy") + 1] == "fixed"


def _write_session_inputs(tmp_path, *, trial_id="session-trial", bounds=(10.0, 20.0, 10.0, 20.0)):
    timestamp = "2026-10-25T12:00:00Z"
    measurements = tmp_path / f"{trial_id}-measurements.json"
    catalog = tmp_path / "catalog-session.json"
    map_path = tmp_path / "map-session.json"
    config_path = tmp_path / f"{trial_id}-config.json"
    write_contract(measurements, MeasurementSetV2((), trial_id=trial_id))
    write_contract(catalog, CatalogV2((), timestamp, "session-test", "1", "session-catalog"))
    ring = [[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0], [0.0, 0.0]]
    write_contract(
        map_path,
        MapV2(
            crs="EPSG:32618",
            source="session-test",
            map_version="1",
            coverage_status="known",
            coverage_geometry_geojson=json.dumps(
                {"type": "Polygon", "coordinates": [ring]}, separators=(",", ":")
            ),
        ),
    )
    write_search_config(
        config_path,
        SearchConfig(algorithm="grid", bounds_xy=bounds, candidate_budget=4),
    )
    return measurements, catalog, map_path, config_path


def test_search_v2_session_matches_cold_search_and_isolates_requests(tmp_path):
    measurements_a, catalog, map_path, config_a = _write_session_inputs(tmp_path, trial_id="a")
    measurements_b, _, _, config_b = _write_session_inputs(
        tmp_path, trial_id="b", bounds=(30.0, 40.0, 30.0, 40.0)
    )
    cold_a = tmp_path / "cold-a.json"
    warm_a = tmp_path / "warm-a.json"
    warm_b = tmp_path / "warm-b.json"
    cold_result = search_v2_files(
        measurements_a,
        catalog,
        map_path,
        config_a,
        "EPSG:32618",
        cold_a,
        spacing_m=5.0,
        refine=False,
    )
    session = SearchV2Session(catalog, map_path, "EPSG:32618")
    warm_result = session.run_request(
        measurements_a,
        config_a,
        warm_a,
        spacing_m=5.0,
        refine=False,
    )
    second_result = session.run_request(
        measurements_b,
        config_b,
        warm_b,
        spacing_m=5.0,
        refine=False,
    )
    assert warm_result.hypotheses == cold_result.hypotheses
    assert warm_result.search_status == cold_result.search_status
    assert warm_result.decision_status == cold_result.decision_status
    assert warm_result.budget_used == cold_result.budget_used
    assert second_result.hypotheses != warm_result.hypotheses
    warm_telemetry = json.loads(warm_a.with_suffix(".json.telemetry.json").read_text())
    second_telemetry = json.loads(warm_b.with_suffix(".json.telemetry.json").read_text())
    assert warm_telemetry["request_timing"]["scope"] == "warm_session_request"
    assert warm_telemetry["request_timing"]["result_serialization_completed"] is True
    assert warm_telemetry["request_timing"]["session_reuse_policy"] == "catalog_map_projection_only"
    assert second_telemetry["diagnostics"]["declared_bounds_xy_m"] == [30.0, 40.0, 30.0, 40.0]


@pytest.mark.parametrize("changed_static", ["catalog", "map"])
def test_search_v2_session_rejects_changed_static_inputs_before_output(tmp_path, changed_static):
    measurements, catalog, map_path, config = _write_session_inputs(tmp_path)
    session = SearchV2Session(catalog, map_path, "EPSG:32618")
    changed = catalog if changed_static == "catalog" else map_path
    changed.write_bytes(changed.read_bytes() + b" ")
    output = tmp_path / "must-not-exist.json"
    with pytest.raises(ValueError, match="changed after SearchV2Session"):
        session.run_request(measurements, config, output, spacing_m=5.0, refine=False)
    assert not output.exists()


def test_search_v2_session_rejects_changed_crs_and_times_full_result_serialization(tmp_path):
    measurements, catalog, map_path, config = _write_session_inputs(tmp_path)
    ticks = iter(float(i) / 10 for i in range(10000))
    session = SearchV2Session(catalog, map_path, "EPSG:32618", clock=lambda: next(ticks))
    output = tmp_path / "timed.json"
    with pytest.raises(ValueError, match="projection does not match"):
        session.run_request(measurements, config, output, projected_crs="EPSG:32617", refine=False)
    assert not output.exists()
    session.run_request(measurements, config, output, spacing_m=5.0, refine=False)
    report = json.loads(output.with_suffix(".json.telemetry.json").read_text())
    timing = report["request_timing"]
    assert timing["request_elapsed_s"] > 0
    assert timing["session_startup_s"] > 0
    assert timing["includes_static_session_startup"] is False
    assert timing["measurement_boundary"].endswith("before_sidecar_write")


def test_jsonl_batch_rejects_malformed_records_before_any_output(tmp_path):
    _, catalog, map_path, _ = _write_session_inputs(tmp_path)
    request_file = tmp_path / "requests.jsonl"
    output = tmp_path / "must-not-exist.json"
    request_file.write_text(
        json.dumps({"measurements": "missing.json", "config": "config.json", "output": str(output)})
        + "\n"
        + json.dumps({"measurements": "x", "config": "y", "output": "z", "future": True})
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="optional telemetry_output only"):
        _run_jsonl_searches(
            request_file,
            catalog,
            map_path,
            "EPSG:32618",
            options={"refine": False},
        )
    assert not output.exists()


def test_jsonl_cli_runs_multiple_requests_with_distinct_outputs(tmp_path, capsys):
    measurements_a, catalog, map_path, config_a = _write_session_inputs(
        tmp_path, trial_id="jsonl-a"
    )
    measurements_b, _, _, config_b = _write_session_inputs(
        tmp_path, trial_id="jsonl-b", bounds=(30.0, 40.0, 30.0, 40.0)
    )
    request_file = tmp_path / "requests.jsonl"
    output_a = tmp_path / "request-a.json"
    output_b = tmp_path / "request-b.json"
    records = [
        {"measurements": measurements_a.name, "config": config_a.name, "output": output_a.name},
        {"measurements": measurements_b.name, "config": config_b.name, "output": output_b.name},
    ]
    request_file.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )
    assert (
        search_v2_main(
            [
                "--requests-jsonl",
                str(request_file),
                "--catalog",
                str(catalog),
                "--map",
                str(map_path),
                "--projected-crs",
                "EPSG:32618",
                "--spacing-m",
                "5",
                "--no-refine",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {"requests_completed": 2}
    assert output_a.exists() and output_b.exists()
    sidecar_a = json.loads(output_a.with_suffix(".json.telemetry.json").read_text())
    sidecar_b = json.loads(output_b.with_suffix(".json.telemetry.json").read_text())
    assert sidecar_a["request_timing"]["scope"] == "warm_session_request"
    assert sidecar_a["request_timing"]["batch_preflight_included"] is False
    assert sidecar_a["request_timing"]["batch_preflight_s"] > 0
    assert (
        sidecar_b["request_timing"]["session_startup_s"]
        == sidecar_a["request_timing"]["session_startup_s"]
    )
    assert sidecar_b["diagnostics"]["declared_bounds_xy_m"] == [30.0, 40.0, 30.0, 40.0]


def _write_evaluation_handoff(tmp_path, support_payload):
    estimate = tmp_path / "estimate-support.json"
    truth = tmp_path / "truth-support.json"
    support = tmp_path / "prepared-support.json"
    output = tmp_path / "metrics-support.json"
    write_contract(
        estimate,
        LocalizationResult(
            hypotheses=(Hypothesis(0.0, 0.0, 0.0),),
            elapsed_s=0.1,
            decision_status="ambiguous",
            search_status="completed",
        ),
    )
    write_contract(truth, EvaluationTruth(0.0, 0.0, 0.0, ()))
    support.write_text(json.dumps(support_payload), encoding="utf-8")
    return estimate, truth, support, output


def test_evaluator_accepts_actual_prepared_support_manifest_shape(tmp_path):
    payload = {
        "valid_reference_water": True,
        "detected_physical_catalog_matched_groups": 3,
        "port_id": "port-001",
        "placement_id": "placement-001",
        "supported": True,
    }
    estimate, truth, support, output = _write_evaluation_handoff(tmp_path, payload)
    result = evaluate_v2_files(estimate, truth, support, output)
    assert result["supported"] is True
    assert json.loads(output.read_text(encoding="utf-8"))["supported"] is True


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"supported": False}, "disagrees"),
        ({"supported": "true"}, "must be bool"),
        ({"unexpected": "field"}, "Unexpected SupportManifest fields"),
        ({"detected_physical_catalog_matched_groups": -1}, "nonnegative integer"),
        ({"detected_physical_catalog_matched_groups": True}, "nonnegative integer"),
    ],
)
def test_evaluator_rejects_invalid_prepared_support_fields_without_output(
    tmp_path, mutation, message
):
    payload = {
        "valid_reference_water": True,
        "detected_physical_catalog_matched_groups": 3,
        "port_id": "port-001",
        "placement_id": "placement-001",
        "supported": True,
    }
    payload.update(mutation)
    estimate, truth, support, output = _write_evaluation_handoff(tmp_path, payload)
    with pytest.raises((TypeError, ValueError), match=message):
        evaluate_v2_files(estimate, truth, support, output)
    assert not output.exists()


def test_evaluator_accepts_prepared_support_without_derived_flag(tmp_path):
    payload = {
        "valid_reference_water": True,
        "detected_physical_catalog_matched_groups": 3,
        "port_id": "port-001",
        "placement_id": "placement-001",
    }
    estimate, truth, support, output = _write_evaluation_handoff(tmp_path, payload)
    result = evaluate_v2_files(estimate, truth, support, output)
    assert result["supported"] is True


def test_wedge_complete_generates_full_vote_set_before_scoring_budget() -> None:
    # One bearing and a dense range create far more than the historical 64-cell cap.
    generated = generate_reverse_wedge_proposals_complete(
        [WedgeObservation(0, 90.0)],
        [WedgeCatalogObject("target", 1_000.0, 0.0)],
        max_range_m=5_000.0,
        cell_size_m=50.0,
        angular_tolerance_deg=1.0,
        max_source_pairs=1,
        max_candidate_cells=100_000,
        source_chunk_size=1,
    )
    assert generated.generation_status == "completed"
    assert generated.source_pairs_processed == generated.source_pairs_total == 1
    assert generated.candidate_cells_total == len(generated.proposals)
    assert len(generated.proposals) > 64


def test_wedge_complete_orders_votes_then_spreads_equal_vote_prefix() -> None:
    # The later proposal has strictly more votes and must survive a small score cap.
    proposals = (
        WedgeProposal("cell:0:0", 0, 0, -900.0, -900.0, 2),
        WedgeProposal("cell:1:0", 1, 0, -800.0, -800.0, 3),
        WedgeProposal("cell:2:0", 2, 0, 800.0, 800.0, 3),
        WedgeProposal("cell:3:0", 3, 0, 900.0, 900.0, 3),
        WedgeProposal("cell:4:0", 4, 0, -700.0, 700.0, 3),
    )
    positions = tuple((item.east_m, item.north_m) for item in proposals)
    order = _order_wedge_proposals_for_scoring(
        proposals,
        positions,
        water_status=lambda _position: "water",
        bounds=(-1_000, 1_000, -1_000, 1_000),
    )
    assert order[0] != 0
    assert proposals[order[0]].vote_count == 3
    first_three = [positions[index] for index in order[:3]]
    assert all(proposals[index].vote_count == 3 for index in order[:3])
    assert (
        max(
            ((left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2) ** 0.5
            for offset, left in enumerate(first_three)
            for right in first_three[offset + 1 :]
        )
        > 1_000.0
    )
    assert set(order) == set(range(len(proposals)))
    assert len({proposals[index].candidate_id for index in order}) == len(proposals)


def test_wedge_proposal_limits_are_validated_and_old_configs_keep_defaults() -> None:
    config = SearchConfig(algorithm="wedge_complete", bounds_xy=(0.0, 10.0, 0.0, 10.0))
    document = search_config_to_dict(config)
    assert document["proposal_source_pair_budget"] is None
    assert document["proposal_cell_budget"] == 1_000_000
    assert document["proposal_chunk_size"] == 4_096
    assert document["proposal_solver_tolerance_deg"] == 1e-7
    old_document = {
        key: value for key, value in document.items() if not key.startswith("proposal_")
    }
    legacy = search_config_from_dict(old_document)
    assert legacy.proposal_source_pair_budget is None
    assert legacy.proposal_cell_budget == 1_000_000
    assert legacy.proposal_chunk_size == 4_096
    assert legacy.proposal_solver_tolerance_deg == 1e-7
    for field, value in (
        ("proposal_source_pair_budget", 0),
        ("proposal_cell_budget", 0),
        ("proposal_chunk_size", True),
        ("proposal_solver_tolerance_deg", 0.0),
        ("proposal_solver_tolerance_deg", float("nan")),
    ):
        with pytest.raises((TypeError, ValueError)):
            search_config_from_dict(document | {field: value})


def test_geodesic_wedge_ordering_preserves_matching_vote_priority() -> None:
    proposals = (
        GeodesicWedgeProposal("water-high-votes", 0, 0, 0.0, 0.0, 9, 2, (), (), 0.2, 1.0),
        GeodesicWedgeProposal("water-high-matching", 1, 0, 1.0, 0.0, 1, 3, (), (), 9.0, 0.0),
        GeodesicWedgeProposal("land-same-tier", 2, 0, 2.0, 0.0, 9, 2, (), (), 0.1, 1.0),
    )
    positions = ((0.0, 0.0), (1.0, 0.0), (2.0, 0.0))
    order = _order_geodesic_proposals_for_scoring(
        proposals,
        positions,
        water_status=lambda position: "land" if position == (2.0, 0.0) else "water",
    )
    assert order == (1, 0, 2)


def _write_geodesic_search_fixture(tmp_path, *, source_budget=30, candidate_budget=1_000):
    timestamp = "2026-10-25T12:00:00Z"
    frame = LocalFrame((0.0, 0.0))
    truth = frame.to_geographic(50.0, 50.0)
    geod = Geod(ellps="WGS84")
    records = []
    measurements = []
    for index, bearing in enumerate((0.0, 90.0, 180.0, 270.0, 45.0)):
        longitude, latitude, _ = geod.fwd(truth[0], truth[1], bearing, 1_000.0)
        records.append(
            CatalogRecordV2(
                key=f"true-{index}",
                latitude_deg=latitude,
                longitude_deg=longitude,
            )
        )
        inverse_bearing, _, _ = geod.inv(truth[0], truth[1], longitude, latitude)
        measurements.append(
            MeasurementV2(
                bearing_deg=inverse_bearing % 360.0,
                timestamp_utc=timestamp,
                sigma_deg=0.5,
            )
        )
    artifact = frame.to_geographic(550.0, 550.0)
    records.append(CatalogRecordV2("artifact", artifact[1], artifact[0]))

    catalog_path = tmp_path / "geodesic-catalog.json"
    map_path = tmp_path / "geodesic-map.json"
    measurements_path = tmp_path / "geodesic-measurements.json"
    config_path = tmp_path / "geodesic-config.json"
    output_path = tmp_path / "geodesic-estimate.json"
    write_contract(
        catalog_path,
        CatalogV2(tuple(records), timestamp, "geodesic-test", "1", "geodesic-catalog"),
    )
    projected_crs = frame.projected_crs.to_string()
    to_projected = Transformer.from_crs("EPSG:4326", projected_crs, always_xy=True)
    origin_x, origin_y = to_projected.transform(*frame.origin_lonlat)
    ring = [
        [origin_x - 10_000.0, origin_y - 10_000.0],
        [origin_x + 10_000.0, origin_y - 10_000.0],
        [origin_x + 10_000.0, origin_y + 10_000.0],
        [origin_x - 10_000.0, origin_y + 10_000.0],
        [origin_x - 10_000.0, origin_y - 10_000.0],
    ]
    write_contract(
        map_path,
        MapV2(
            crs=projected_crs,
            source="geodesic-test",
            map_version="1",
            coverage_status="known",
            coverage_geometry_geojson=json.dumps(
                {"type": "Polygon", "coordinates": [ring]}, separators=(",", ":")
            ),
            land_geometry_geojson=json.dumps(
                {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [origin_x + 7_000.0, origin_y - 100.0],
                            [origin_x + 7_100.0, origin_y - 100.0],
                            [origin_x + 7_100.0, origin_y + 100.0],
                            [origin_x + 7_000.0, origin_y + 100.0],
                            [origin_x + 7_000.0, origin_y - 100.0],
                        ]
                    ],
                },
                separators=(",", ":"),
            ),
        ),
    )
    write_contract(
        measurements_path,
        MeasurementSetV2(tuple(measurements), trial_id="geodesic-test"),
    )
    write_search_config(
        config_path,
        SearchConfig(
            algorithm="wedge_geodesic",
            bounds_xy=(
                origin_x - 2_000.0,
                origin_x + 2_000.0,
                origin_y - 2_000.0,
                origin_y + 2_000.0,
            ),
            max_range_m=1_500.0,
            candidate_budget=candidate_budget,
            proposal_source_pair_budget=source_budget,
            proposal_cell_budget=100_000,
            proposal_chunk_size=3,
        ),
    )
    return measurements_path, catalog_path, map_path, config_path, output_path, projected_crs


def test_geodesic_wedge_cli_reports_complete_generation_witnesses_and_stage_timing(tmp_path):
    (
        measurements_path,
        catalog_path,
        map_path,
        config_path,
        output_path,
        projected_crs,
    ) = _write_geodesic_search_fixture(tmp_path)
    result = search_v2_files(
        measurements_path,
        catalog_path,
        map_path,
        config_path,
        projected_crs,
        output_path,
        wedge_cell_size_m=100.0,
        refine=False,
    )
    sidecar = json.loads(output_path.with_suffix(".json.telemetry.json").read_text())
    diagnostics = sidecar["diagnostics"]
    generation = diagnostics["proposal_generation"]
    ordering = diagnostics["proposal_ordering"]
    scoring = diagnostics["exact_scoring"]
    assert result.search_status == "completed"
    assert generation["generation_complete"] is True
    assert generation["source_pairs_processed"] == generation["source_pairs_total"] == 30
    assert generation["candidate_cells_total"] > 0
    assert generation["geodesic_samples_failed"] == 0
    assert generation["solver_tolerance_deg"] == 1e-7
    assert generation["max_solver_residual_deg"] <= generation["solver_tolerance_deg"]
    assert generation["unique_witness_edge_count"] == generation["witness_edge_count"]
    assert ordering["proposal_count"] == generation["candidate_cells_total"]
    assert scoring["candidate_count"] > 0
    assert len(scoring["evaluated_proposal_witnesses"]) == scoring["candidate_count"]
    assert all(
        record["candidate_id"] and record["witness_report_keys"]
        for record in scoring["evaluated_proposal_witnesses"]
    )
    stages = sidecar["telemetry"]["stages"]
    stage_names = [stage["name"] for stage in stages]
    assert {"proposal_generation", "proposal_ordering", "exact_scoring"} <= set(stage_names)
    assert sidecar["request_timing"]["result_serialization_completed"] is True
    assert diagnostics["result_elapsed_s_scope"].startswith(
        "exact_scoring_plus_continuous_refinement"
    )
    assert diagnostics["elapsed_time_semantics"]["complete_request_wall_s"].startswith(
        "request_timing.request_elapsed_s"
    )


def test_wedge_algorithms_keep_missing_sigma_reports_and_geodesic_uses_floor(
    tmp_path, monkeypatch
) -> None:
    import bearing_localization.accuracy_cli as accuracy_cli

    captured_observations: tuple[WedgeObservation, ...] = ()
    captured_sigmas: dict[int, float] = {}
    original_generator = accuracy_cli.generate_geographic_reverse_wedge_proposals_geodesic

    def capture_geodesic_inputs(*args, **kwargs):
        nonlocal captured_observations, captured_sigmas
        captured_observations = args[0]
        captured_sigmas = kwargs["observation_sigmas_deg"]
        return original_generator(*args, **kwargs)

    monkeypatch.setattr(
        accuracy_cli,
        "generate_geographic_reverse_wedge_proposals_geodesic",
        capture_geodesic_inputs,
    )
    for algorithm in ("wedge", "wedge_complete", "wedge_geodesic"):
        request_dir = tmp_path / algorithm
        request_dir.mkdir()
        (
            measurements_path,
            catalog_path,
            map_path,
            config_path,
            output_path,
            projected_crs,
        ) = _write_geodesic_search_fixture(request_dir, candidate_budget=1_000)
        measurement_set = loads_contract(measurements_path.read_text(), MeasurementSetV2)
        measurements = tuple(
            replace(item, sigma_deg=None)
            if index == 0
            else replace(item, sigma_deg=0.01)
            if index == 1
            else item
            for index, item in enumerate(measurement_set.measurements)
        )
        write_contract(
            measurements_path,
            replace(measurement_set, measurements=measurements),
        )
        config = loads_search_config(config_path.read_text())
        write_search_config(
            config_path,
            replace(
                config,
                algorithm=algorithm,
                generated_sigma_deg=0.7,
                sigma_floor_deg=0.2,
            ),
        )
        result = search_v2_files(
            measurements_path,
            catalog_path,
            map_path,
            config_path,
            projected_crs,
            output_path,
            wedge_error_deg=0.5,
            wedge_cell_size_m=100.0,
            refine=False,
        )
        assert result.search_status in ("completed", "budget_exhausted")
        sidecar = json.loads(output_path.with_suffix(".json.telemetry.json").read_text())
        if algorithm == "wedge_geodesic":
            generation = sidecar["diagnostics"]["proposal_generation"]
            assert len(captured_observations) == 5
            assert generation["source_pairs_total"] == 30
            assert captured_sigmas == {
                0: 0.7,
                1: 0.2,
                2: 0.5,
                3: 0.5,
                4: 0.5,
            }
        else:
            assert sidecar["diagnostics"]["source_pairs_total"] == 30


def test_geodesic_solver_tolerance_is_bounded_by_effective_sigma(tmp_path) -> None:
    (
        measurements_path,
        catalog_path,
        map_path,
        config_path,
        output_path,
        projected_crs,
    ) = _write_geodesic_search_fixture(tmp_path)
    config = loads_search_config(config_path.read_text())
    write_search_config(
        config_path,
        replace(config, proposal_solver_tolerance_deg=0.0011),
    )
    with pytest.raises(ValueError, match="exceeds the geodesic solver bound"):
        search_v2_files(
            measurements_path,
            catalog_path,
            map_path,
            config_path,
            projected_crs,
            output_path,
            wedge_cell_size_m=100.0,
            refine=False,
        )


def test_geodesic_wedge_cli_propagates_source_budget_and_failed_samples_block_acceptance(
    tmp_path, monkeypatch
):
    import bearing_localization.accuracy_cli as accuracy_cli

    (
        measurements_path,
        catalog_path,
        map_path,
        config_path,
        output_path,
        projected_crs,
    ) = _write_geodesic_search_fixture(tmp_path, source_budget=1, candidate_budget=256)
    original = accuracy_cli.generate_geographic_reverse_wedge_proposals_geodesic

    def failed_samples(*args, **kwargs):
        proposals = original(*args, **kwargs)
        return replace(proposals, geodesic_samples_failed=1, source_pairs_failed=1)

    monkeypatch.setattr(
        accuracy_cli, "generate_geographic_reverse_wedge_proposals_geodesic", failed_samples
    )
    result = search_v2_files(
        measurements_path,
        catalog_path,
        map_path,
        config_path,
        projected_crs,
        output_path,
        wedge_cell_size_m=100.0,
        refine=False,
    )
    sidecar = json.loads(output_path.with_suffix(".json.telemetry.json").read_text())
    generation = sidecar["diagnostics"]["proposal_generation"]
    assert generation["generation_status"] == "budget_exhausted"
    assert generation["generation_budget_reason"] == "source_pairs"
    assert generation["source_pairs_processed"] == 1
    assert generation["generation_complete"] is False
    assert generation["unresolved_model_count"] == 2
    assert result.search_status == "failed"
    assert result.decision_status != "accepted"
    public_evidence = sidecar["diagnostics"]["public_evidence"]
    assert public_evidence["mode_evidence"] == []
    assert public_evidence["top_mode_converged"] is False


def test_seeded_geodesic_cli_scores_cell_centers_then_seed_candidates_and_reports_budgets(
    tmp_path,
):
    (
        measurements_path,
        catalog_path,
        map_path,
        config_path,
        output_path,
        projected_crs,
    ) = _write_geodesic_search_fixture(tmp_path, candidate_budget=16)
    config = loads_search_config(config_path.read_text())
    outcomes = []
    for workers in (1, 2, 4):
        worker_config_path = tmp_path / f"seeded-config-{workers}.json"
        worker_output_path = tmp_path / f"seeded-estimate-{workers}.json"
        write_search_config(
            worker_config_path,
            replace(
                config,
                algorithm="wedge_geodesic_seeded",
                seed_cell_budget=20_000,
                seed_pair_budget=100_000,
                seed_candidate_budget=20_000,
                candidate_budget=16,
                candidate_workers=workers,
                candidate_pool_memory_budget_bytes=8 * 1024**3,
            ),
        )
        result = search_v2_files(
            measurements_path,
            catalog_path,
            map_path,
            worker_config_path,
            projected_crs,
            worker_output_path,
            wedge_error_deg=0.5,
            wedge_cell_size_m=100.0,
            refine=False,
        )
        sidecar = json.loads(worker_output_path.with_suffix(".json.telemetry.json").read_text())
        diagnostics = sidecar["diagnostics"]
        seed = diagnostics["seed_generation"]
        scoring = diagnostics["exact_scoring"]
        stage_names = {stage["name"] for stage in sidecar["telemetry"]["stages"]}

        assert seed["generation_status"] == "completed"
        assert seed["candidate_seeds_generated"] > 0
        assert seed["pairs_considered"] + seed["pairs_remaining"] == seed["pair_combinations_total"]
        assert seed["candidate_seeds_generated"] == len(seed["seeds"])
        assert scoring["candidate_count"] == diagnostics["candidate_count"]
        assert len(scoring["evaluated_candidate_ids"]) == scoring["candidate_count"]
        assert all(
            candidate_id.startswith("seed:") for candidate_id in scoring["evaluated_candidate_ids"]
        )
        assert scoring["scored_seed_candidate_count"] == 16
        assert scoring["scored_cell_center_candidate_count"] == 0
        assert scoring["scored_seed_cell_count"] == 16
        assert scoring["remaining_unique_candidates"] > 400
        assert diagnostics["proposal_ordering"]["candidate_stream_policy"] == (
            "seed_round_robin_by_selected_ranked_cell_then_all_ranked_cell_centers"
        )
        assert {
            "proposal_generation",
            "seed_generation",
            "proposal_ordering",
            "candidate_pool_startup",
            "exact_scoring",
        } <= stage_names
        assert diagnostics["candidate_pool"]["workers"] == workers
        assert diagnostics["elapsed_time_semantics"]["complete_request_wall_s"].startswith(
            "request_timing.request_elapsed_s"
        )
        outcomes.append((result, scoring["outcome_counts"], scoring["evaluated_candidate_ids"]))

    baseline = outcomes[0]
    for outcome in outcomes[1:]:
        assert outcome[0].search_status == baseline[0].search_status
        assert outcome[0].decision_status == baseline[0].decision_status
        assert outcome[0].hypotheses == baseline[0].hypotheses
        assert outcome[1:] == baseline[1:]

    incomplete_config_path = tmp_path / "seeded-config-incomplete.json"
    incomplete_output_path = tmp_path / "seeded-estimate-incomplete.json"
    write_search_config(
        incomplete_config_path,
        replace(config, algorithm="wedge_geodesic_seeded", seed_cell_budget=1),
    )
    incomplete_result = search_v2_files(
        measurements_path,
        catalog_path,
        map_path,
        incomplete_config_path,
        projected_crs,
        incomplete_output_path,
        wedge_error_deg=0.5,
        wedge_cell_size_m=100.0,
        refine=False,
    )
    incomplete_diagnostics = json.loads(
        incomplete_output_path.with_suffix(".json.telemetry.json").read_text()
    )["diagnostics"]
    assert incomplete_diagnostics["seed_generation"]["unselected_cells"] > 0
    assert incomplete_result.search_status == "budget_exhausted"
    assert incomplete_result.accepted_index is None


def test_seeded_geodesic_pool_startup_failure_is_failed_and_unaccepted(tmp_path, monkeypatch):
    import bearing_localization.accuracy_cli as accuracy_cli

    (
        measurements_path,
        catalog_path,
        map_path,
        config_path,
        output_path,
        projected_crs,
    ) = _write_geodesic_search_fixture(tmp_path, candidate_budget=20_000)
    config = loads_search_config(config_path.read_text())
    write_search_config(
        config_path,
        replace(
            config,
            algorithm="wedge_geodesic_seeded",
            candidate_workers=2,
            seed_cell_budget=20_000,
        ),
    )

    def fail_pool(*_args, **_kwargs):
        raise RuntimeError("injected worker startup failure")

    monkeypatch.setattr(accuracy_cli, "PersistentCandidatePool", fail_pool)
    result = search_v2_files(
        measurements_path,
        catalog_path,
        map_path,
        config_path,
        projected_crs,
        output_path,
        wedge_error_deg=0.5,
        wedge_cell_size_m=100.0,
        refine=False,
    )
    diagnostics = json.loads(output_path.with_suffix(".json.telemetry.json").read_text())[
        "diagnostics"
    ]
    assert result.search_status == "failed"
    assert result.accepted_index is None
    assert diagnostics["candidate_pool"]["failure"]["failure_kind"] == "worker_startup_failed"
    assert "injected worker startup failure" in diagnostics["candidate_pool"]["failure"]["error"]


@pytest.mark.parametrize(
    "field",
    (
        "seed_cell_budget",
        "seed_pair_budget",
        "seed_candidate_budget",
        "candidate_workers",
        "candidate_pool_memory_budget_bytes",
    ),
)
def test_seeded_geodesic_budgets_require_positive_integers(field):
    with pytest.raises(ValueError, match=field):
        cast(Any, SearchConfig)(**{field: 0})
    with pytest.raises(ValueError, match=field):
        cast(Any, SearchConfig)(**{field: True})


def test_seeded_geodesic_prioritizes_water_seeds_over_land_cell_centers(tmp_path, monkeypatch):
    import bearing_localization.accuracy_cli as accuracy_cli
    from maritime_localization_shared.geospatial.vector_map import VectorMap

    (
        measurements_path,
        catalog_path,
        map_path,
        config_path,
        output_path,
        projected_crs,
    ) = _write_geodesic_search_fixture(tmp_path, candidate_budget=16)
    config = loads_search_config(config_path.read_text())
    write_search_config(
        config_path,
        replace(
            config,
            algorithm="wedge_geodesic_seeded",
            candidate_budget=16,
            candidate_workers=1,
            seed_cell_budget=20_000,
        ),
    )

    def key(position):
        return (round(position[0], 9), round(position[1], 9))

    land_center_keys = set()
    centers_by_candidate_id = {}
    original_point_status = VectorMap.point_water_status
    original_order = accuracy_cli._order_geodesic_proposals_for_scoring
    original_seed_generator = accuracy_cli.generate_continuous_witness_seeds
    original_score = accuracy_cli.score_candidate_positions
    scored = {}

    def classify_selected_center_as_land(self, position):
        if key(position) in land_center_keys:
            return "land"
        return original_point_status(self, position)

    def capture_order(proposals, positions_xy, *, water_status):
        centers_by_candidate_id.update(
            (proposal.candidate_id, key(position))
            for proposal, position in zip(proposals, positions_xy, strict=True)
        )
        monkeypatch.setattr(VectorMap, "point_water_status", classify_selected_center_as_land)
        return original_order(proposals, positions_xy, water_status=water_status)

    def capture_seeds(*args, **kwargs):
        seeds = original_seed_generator(*args, **kwargs)
        land_center_keys.update(
            centers_by_candidate_id[seed.cell_candidate_id] for seed in seeds.seeds
        )
        return seeds

    def capture_score(scorer, positions_xy, **kwargs):
        positions = list(positions_xy)
        scored["scorer"] = scorer
        scored["positions"] = positions
        return original_score(scorer, positions, **kwargs)

    monkeypatch.setattr(accuracy_cli, "_order_geodesic_proposals_for_scoring", capture_order)
    monkeypatch.setattr(accuracy_cli, "generate_continuous_witness_seeds", capture_seeds)
    monkeypatch.setattr(accuracy_cli, "score_candidate_positions", capture_score)
    result = search_v2_files(
        measurements_path,
        catalog_path,
        map_path,
        config_path,
        projected_crs,
        output_path,
        wedge_error_deg=0.5,
        wedge_cell_size_m=100.0,
        refine=False,
    )
    diagnostics = json.loads(output_path.with_suffix(".json.telemetry.json").read_text())[
        "diagnostics"
    ]
    candidate_ids = diagnostics["exact_scoring"]["evaluated_candidate_ids"]
    assert result.search_status == "budget_exhausted"
    assert len(candidate_ids) == 16
    assert all(candidate_id.startswith("seed:") for candidate_id in candidate_ids)
    assert any(
        classify_selected_center_as_land(scored["scorer"].vector_map, position) == "water"
        for position in scored["positions"][:16]
    )
    assert land_center_keys
    assert all(
        classify_selected_center_as_land(scored["scorer"].vector_map, position) == "land"
        for position in land_center_keys
    )
