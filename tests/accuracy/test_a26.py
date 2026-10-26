from __future__ import annotations

import csv
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from pyproj import CRS, Geod, Transformer
from shapely.geometry import box

from bearing_localization.evaluation.metrics import SupportManifest, evaluate_localization
from maritime_localization_shared.contracts.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MeasurementSetV2,
)
from maritime_localization_shared.contracts.localization import Hypothesis, LocalizationResult
from maritime_localization_shared.contracts.truth import EvaluationTruth
from maritime_localization_shared.geospatial.vector_map import VectorMap
from maritime_localization_shared.serialization import read_contract, write_contract
from tools.run_accuracy_suite import (  # noqa: E402
    _distance_m,
    _extend_nominal_plan_if_needed,
    _load_command_config,
    _load_ports,
    _run_process,
    build_split_manifest,
    create_plan,
    prepare_inputs,
    run_trials,
)


def test_split_manifest_uses_reference_anchor_overlap_groups() -> None:
    manifest = build_split_manifest()
    assignments = {port["source_site_id"]: port["effective_split"] for port in manifest["ports"]}

    assert len(manifest["ports"]) == 24
    assert manifest["schema"] == "accuracy-split-manifest-v2"
    assert assignments["B8"] == "validation"
    assert manifest["split_port_ids"]["development"] == [
        f"port-{index:03d}" for index in (1, 3, 6, 7, 9, 10, 11, 13, 14, 17, 20, 23)
    ]
    assert manifest["split_port_ids"]["validation"] == [
        f"port-{index:03d}" for index in (2, 8, 16, 22)
    ]
    assert len(manifest["split_port_ids"]["heldout"]) == 8
    assert manifest["overlap_decisions"] == []
    assert manifest["legacy_b1_b5_separate"] is True

    ports = {
        row["source_site_id"]: row
        for row in _load_ports(Path("data/sites/east-coast-major-ports-top150-2024.csv"))
    }
    assert _distance_m(ports["B7"], ports["B8"]) == pytest.approx(26_432.49, abs=0.02)
    assert _distance_m(ports["B6"], ports["B7"]) < 26_000.0

    geod = Geod(ellps="WGS84")
    _, _, port_point_distance = geod.inv(
        ports["B7"]["longitude_deg"],
        ports["B7"]["latitude_deg"],
        ports["B8"]["longitude_deg"],
        ports["B8"]["latitude_deg"],
    )
    assert port_point_distance < 26_000.0
    assert _distance_m(ports["B7"], ports["B8"]) > 26_000.0


def test_plan_pins_profiles_and_pairs_type_label_seeds(tmp_path: Path) -> None:
    config_path, table_path = create_plan(tmp_path)
    config = json.loads(config_path.read_text())
    with table_path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))

    assert len(rows) == 12_240
    assert config["trial_count"] == len(rows)
    assert config["seed_excludes"] == ["algorithm", "report_types"]
    assert config["stress_profiles"]["miss"] == {"detection_probability": 0.7}
    assert config["stress_profiles"]["map_buffer"] == {"land_buffer_m": 10.0}
    pair = [
        row
        for row in rows
        if row["split"] == "development"
        and row["port_id"] == "port-001"
        and row["placement_id"] == "placement-001"
        and row["realization"] == "0"
        and row["range_m"] == "1000"
        and row["sensor_condition"] == "nominal"
    ]
    assert len(pair) == 2
    assert {row["physical_seed"] for row in pair} == {pair[0]["physical_seed"]}
    stress = [row for row in rows if row["sensor_condition"] != "nominal"]
    assert all(int(row["placement_id"].rsplit("-", 1)[1]) <= 5 for row in stress)
    assert all(row["realization"] == "0" for row in stress)


def test_command_config_requires_full_coast_search_and_hides_truth(tmp_path: Path) -> None:
    config = {
        "search_argv": [
            "search",
            "{measurements_path}",
            "{search_catalog_path}",
            "{search_map_path}",
            "{search_config_path}",
            "--projected-crs",
            "{projected_crs}",
            "--scope-manifest",
            "{search_scope_manifest_path}",
            "{estimate_path}",
        ],
        "evaluator_argv": [
            "eval",
            "--estimate",
            "{evaluator_estimate_path}",
            "--truth",
            "{truth_path}",
            "--support",
            "{support_path}",
            "--coordinate-crs",
            "{truth_coordinate_crs}",
            "--output",
            "{evaluation_path}",
        ],
        "search_catalog": "catalog.json",
        "search_map": "coast-map.json",
        "search_config": "search.json",
        "search_domain_scope": "full_coast",
        "search_map_scope": "full_coast",
        "projected_crs": "EPSG:32618",
    }
    path = tmp_path / "commands.json"
    path.write_text(json.dumps(config))
    assert _load_command_config(path)["search_domain_scope"] == "full_coast"

    config["search_argv"].append("{truth_path}")
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="evaluator-only"):
        _load_command_config(path)


def test_prepared_measurements_use_catalog_timestamp(tmp_path: Path, monkeypatch) -> None:
    origin_lon, origin_lat = -76.3, 37.0
    stamp = "2023-07-15T12:00:30Z"
    geod = Geod(ellps="WGS84")
    records = []
    for index, bearing in enumerate((10.0, 130.0, 250.0)):
        lon, lat, _ = geod.fwd(origin_lon, origin_lat, bearing, 500.0)
        records.append(
            CatalogRecordV2(
                key=f"mmsi:{index + 1}",
                latitude_deg=lat,
                longitude_deg=lon,
                raw_type="vessel",
                normalized_type="vessel",
                metadata=(("physical_entity_id", f"vessel-{index + 1}"),),
            )
        )
    catalog = CatalogV2(
        records=tuple(records),
        timestamp_utc=stamp,
        source="timestamp-regression",
        catalog_version="1",
        catalog_id="timestamp-regression",
    )
    catalog_path = tmp_path / "catalog.json"
    write_contract(catalog_path, catalog)

    origin_xy = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform(
        origin_lon, origin_lat
    )
    vector_map = VectorMap(
        None,
        crs=CRS.from_epsg(32618),
        coverage_geometry=box(
            origin_xy[0] - 20_000,
            origin_xy[1] - 20_000,
            origin_xy[0] + 20_000,
            origin_xy[1] + 20_000,
        ),
    )
    forward = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True)
    inverse = Transformer.from_crs("EPSG:32618", "EPSG:4326", always_xy=True)
    monkeypatch.setattr(
        "tools.run_accuracy_suite._water_sampler",
        lambda _port, _path: (vector_map, forward, inverse),
    )
    monkeypatch.setattr(
        "tools.run_accuracy_suite._sample_placements",
        lambda _port, _path, *, count, port_split: [
            {
                "placement_id": f"placement-{index + 1:03d}",
                "longitude_deg": origin_lon,
                "latitude_deg": origin_lat,
            }
            for index in range(count)
        ],
    )
    monkeypatch.setitem(
        __import__("tools.run_accuracy_suite", fromlist=["STRESS_PROFILES"]).STRESS_PROFILES,
        "nominal",
        {"detection_probability": 1.0, "expected_clutter_reports": 0.0},
    )

    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    manifest = build_split_manifest()
    (plan_dir / "splits.json").write_text(json.dumps(manifest))
    trial_id = "development/port-001/placement-001/r0/range1000m/typeson/nominal"
    (plan_dir / "config.json").write_text(
        json.dumps({"trial_table": "trials.csv", "split_manifest": "splits.json"})
    )
    (plan_dir / "trials.csv").write_text(
        "trial_id,split,port_id,placement_id,realization,range_m,report_types,physical_seed,sensor_condition,profile_parameters\n"
        f"{trial_id},development,port-001,placement-001,0,1000,True,123,nominal,{{}}\n"
    )
    map_cache = tmp_path / "maps"
    map_cache.mkdir()
    (map_cache / "noaa-enc-port-B1-15km.map-v2.json").write_text("reference-map")
    input_root = tmp_path / "inputs"

    prepare_inputs(
        plan_dir / "config.json",
        catalog_path=catalog_path,
        map_cache=map_cache,
        input_root=input_root,
        split="development",
        only_trial_ids={trial_id},
        _allow_extension=False,
    )

    truth_crs_document = json.loads(
        (
            input_root
            / "development/port-001/placement-001/r0/1000m/types-on/nominal/truth-crs.json"
        ).read_text()
    )
    assert truth_crs_document["coordinate_reference_system"] == "EPSG:32618"
    assert truth_crs_document["trial_id"] == trial_id
    measurements = read_contract(
        input_root
        / "development/port-001/placement-001/r0/1000m/types-on/nominal/measurements.json",
        MeasurementSetV2,
    )
    assert measurements.measurements
    assert {measurement.timestamp_utc for measurement in measurements.measurements} == {stamp}


def test_extension_reads_support_files_written_by_initial_preparation(
    tmp_path: Path, monkeypatch
) -> None:
    origin_lon, origin_lat = -76.3, 37.0
    geod = Geod(ellps="WGS84")
    records = []
    for index, bearing in enumerate((10.0, 130.0, 250.0)):
        lon, lat, _ = geod.fwd(origin_lon, origin_lat, bearing, 500.0)
        records.append(
            CatalogRecordV2(
                key=f"mmsi:{index + 1}",
                latitude_deg=lat,
                longitude_deg=lon,
                raw_type="vessel",
                normalized_type="vessel",
                metadata=(("physical_entity_id", f"vessel-{index + 1}"),),
            )
        )
    catalog = CatalogV2(
        records=tuple(records),
        timestamp_utc="2023-07-15T12:00:30Z",
        source="support-path-regression",
        catalog_version="1",
        catalog_id="support-path-regression",
    )
    catalog_path = tmp_path / "catalog.json"
    write_contract(catalog_path, catalog)
    origin_xy = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform(
        origin_lon, origin_lat
    )
    vector_map = VectorMap(
        None,
        crs=CRS.from_epsg(32618),
        coverage_geometry=box(
            origin_xy[0] - 20_000,
            origin_xy[1] - 20_000,
            origin_xy[0] + 20_000,
            origin_xy[1] + 20_000,
        ),
    )
    forward = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True)
    inverse = Transformer.from_crs("EPSG:32618", "EPSG:4326", always_xy=True)
    monkeypatch.setattr(
        "tools.run_accuracy_suite._water_sampler",
        lambda _port, _path: (vector_map, forward, inverse),
    )
    monkeypatch.setattr(
        "tools.run_accuracy_suite._sample_placements",
        lambda _port, _path, *, count, port_split: [
            {
                "placement_id": f"placement-{index + 1:03d}",
                "longitude_deg": origin_lon,
                "latitude_deg": origin_lat,
            }
            for index in range(count)
        ],
    )
    monkeypatch.setitem(
        __import__("tools.run_accuracy_suite", fromlist=["STRESS_PROFILES"]).STRESS_PROFILES,
        "nominal",
        {"detection_probability": 1.0, "expected_clutter_reports": 0.0},
    )

    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    manifest = build_split_manifest()
    manifest["ports"] = [port for port in manifest["ports"] if port["port_id"] == "port-001"]
    manifest["split_port_ids"] = {"development": ["port-001"]}
    (plan_dir / "splits.json").write_text(json.dumps(manifest))
    (plan_dir / "config.json").write_text(
        json.dumps({"trial_table": "trials.csv", "split_manifest": "splits.json"})
    )
    fieldnames = [
        "trial_id",
        "split",
        "port_id",
        "placement_id",
        "realization",
        "range_m",
        "report_types",
        "physical_seed",
        "sensor_condition",
        "profile_parameters",
    ]
    rows = []
    for placement in range(1, 26):
        placement_id = f"placement-{placement:03d}"
        for realization in range(2):
            for range_m in (1000, 5000, 10000):
                for report_types in (True, False):
                    trial_id = (
                        f"development/port-001/{placement_id}/r{realization}/"
                        f"range{range_m}m/types{'on' if report_types else 'off'}/nominal"
                    )
                    rows.append(
                        {
                            "trial_id": trial_id,
                            "split": "development",
                            "port_id": "port-001",
                            "placement_id": placement_id,
                            "realization": str(realization),
                            "range_m": str(range_m),
                            "report_types": str(report_types),
                            "physical_seed": str(1000 + placement * 10 + realization),
                            "sensor_condition": "nominal",
                            "profile_parameters": "{}",
                        }
                    )
    with (plan_dir / "trials.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    map_cache = tmp_path / "maps"
    map_cache.mkdir()
    (map_cache / "noaa-enc-port-B1-15km.map-v2.json").write_text("reference-map")
    input_root = tmp_path / "inputs"

    prepare_inputs(
        plan_dir / "config.json",
        catalog_path=catalog_path,
        map_cache=map_cache,
        input_root=input_root,
        split="development",
        _allow_extension=False,
    )

    support_files = list(
        (input_root / "development/port-001").glob("placement-*/r*/**/support.json")
    )
    assert len(support_files) == 25 * 2 * 3 * 2
    assert all(path.is_file() for path in support_files)
    # This must inspect the exact preparation layout. A duplicated split component
    # in the reader path would make initial support appear incomplete and return [].
    added = _extend_nominal_plan_if_needed(
        plan_dir / "config.json", input_root=input_root, split="development"
    )
    assert len(added) == 25 * 2 * 3 * 2


def test_runner_retains_failure_attempt_then_resumes(tmp_path: Path, monkeypatch) -> None:
    plan_dir = tmp_path / "plan"
    run_root = tmp_path / "runs"
    input_root = tmp_path / "inputs"
    plan_dir.mkdir()
    input_dir = input_root / "development/port-001/placement-001/r0/1000m/types-on/nominal"
    input_dir.mkdir(parents=True)
    for name in ("measurements.json", "support.json"):
        (input_dir / name).write_text("{}")
    write_contract(
        input_dir / "truth.json",
        EvaluationTruth(
            observer_east_m=0.0,
            observer_north_m=0.0,
            observer_heading_deg=0.0,
            objects=(),
            trial_id="trial-1",
        ),
    )
    table = plan_dir / "trials.csv"
    table.write_text(
        "trial_id,split,port_id,placement_id,realization,range_m,report_types,physical_seed,sensor_condition\n"
        "trial-1,development,port-001,placement-001,0,1000,True,123,nominal\n"
    )
    config_path = plan_dir / "plan.json"
    config_path.write_text(
        json.dumps(
            {
                "trial_table": "trials.csv",
                "split_manifest": "splits.json",
            }
        )
    )
    for name in ("catalog.json", "coast-map.json"):
        (tmp_path / name).write_text("{}")
    (tmp_path / "search.json").write_text(
        json.dumps({"contract_type": "SearchConfig", "bounds_xy": [-10.0, 10.0, -20.0, 20.0]})
    )
    catalog_hash = hashlib.sha256((tmp_path / "catalog.json").read_bytes()).hexdigest()
    (input_dir / "physical-identity-audit.json").write_text(
        json.dumps({"catalog_sha256": catalog_hash})
    )
    local_map = tmp_path / "local-map.json"
    local_map.write_text("local-map")
    (input_dir / "truth-crs.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "trial_id": "trial-1",
                "reference_map_path": str(local_map),
                "reference_map_sha256": hashlib.sha256(local_map.read_bytes()).hexdigest(),
                "coordinate_reference_system": "EPSG:32618",
            }
        )
    )
    commands = tmp_path / "commands.json"
    commands.write_text(
        json.dumps(
            {
                "search_argv": [
                    "stub",
                    "search",
                    "{measurements_path}",
                    "{search_catalog_path}",
                    "{search_map_path}",
                    "{search_config_path}",
                    "--projected-crs",
                    "{projected_crs}",
                    "--scope-manifest",
                    "{search_scope_manifest_path}",
                    "--output",
                    "{estimate_path}",
                ],
                "evaluator_argv": [
                    "stub",
                    "evaluate",
                    "--estimate",
                    "{evaluator_estimate_path}",
                    "--truth",
                    "{truth_path}",
                    "--support",
                    "{support_path}",
                    "--coordinate-crs",
                    "{truth_coordinate_crs}",
                    "--output",
                    "{evaluation_path}",
                ],
                "search_catalog": str(tmp_path / "catalog.json"),
                "search_map": str(tmp_path / "coast-map.json"),
                "search_config": str(tmp_path / "search.json"),
                "search_domain_scope": "full_coast",
                "search_map_scope": "full_coast",
                "projected_crs": "EPSG:32618",
            }
        )
    )
    fail_search = True

    def fake_run(argv, **kwargs):
        nonlocal fail_search
        if argv[1] == "search" and fail_search:
            return {
                "status": "failed",
                "returncode": 17,
                "elapsed_s": 0.01,
                "stdout": "search stdout",
                "stderr": "search failed",
            }
        if argv[1] == "search":
            write_contract(
                Path(argv[-1]),
                LocalizationResult(
                    hypotheses=(),
                    elapsed_s=0.0,
                    decision_status="no_fix",
                    search_status="completed",
                ),
            )
        elif argv[1] == "evaluate":
            Path(argv[-1]).write_text("{}")
        return {
            "status": "completed",
            "returncode": 0,
            "elapsed_s": 0.01,
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr("tools.run_accuracy_suite._run_process", fake_run)
    state_path = run_trials(
        config_path,
        command_config_path=commands,
        input_root=input_root,
        run_root=run_root,
        split="development",
        timeout_s=1.0,
    )
    first = list(csv.DictReader(state_path.open()))
    assert first[0]["status"] == "search_failed"
    run_dir = state_path.parent
    scope_manifest = json.loads((run_dir / "scope-manifest.json").read_text())
    assert scope_manifest == {
        "schema_version": 1,
        "geographic_scope": "full_coast",
        "catalog_sha256": catalog_hash,
        "map_sha256": hashlib.sha256((tmp_path / "coast-map.json").read_bytes()).hexdigest(),
        "projected_crs": "EPSG:32618",
        "bounds_xy_m": [-10.0, 10.0, -20.0, 20.0],
    }
    attempts_path = run_dir / "attempts.csv"
    prior_attempts = list(csv.DictReader(attempts_path.open()))
    assert len(prior_attempts) == 1
    assert prior_attempts[0]["returncode"] == "17"

    fail_search = False
    state_path = run_trials(
        config_path,
        command_config_path=commands,
        input_root=input_root,
        run_root=run_root,
        split="development",
        timeout_s=1.0,
        retry_failures=True,
    )
    resumed = list(csv.DictReader(state_path.open()))
    assert resumed[0]["status"] == "completed"
    attempts = list(csv.DictReader(attempts_path.open()))
    assert [row["status"] for row in attempts] == ["failed", "completed", "completed"]


def test_run_trials_binds_six_conditions_crs_outputs_and_resume_hashes(
    tmp_path: Path, monkeypatch
) -> None:
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    input_root = tmp_path / "inputs"
    run_root = tmp_path / "runs"
    catalog_path = tmp_path / "catalog.json"
    map_path = tmp_path / "coast-map.json"
    search_config_path = tmp_path / "search.json"
    catalog_path.write_text("catalog")
    map_path.write_text("map")
    search_config_path.write_text(
        json.dumps(
            {
                "contract_type": "SearchConfig",
                "algorithm": "pairwise",
                "bounds_xy": [300_000.0, 900_000.0, 3_800_000.0, 4_500_000.0],
                "max_range_m": 10_000.0,
                "use_types": True,
            }
        )
    )
    catalog_hash = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
    local_map = tmp_path / "reference.map"
    local_map.write_text("map")
    local_map_hash = hashlib.sha256(local_map.read_bytes()).hexdigest()
    observer_xy = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform(
        -76.3, 37.0
    )
    trial_rows = []
    six_conditions = [
        (range_m, report_types) for range_m in (1000, 5000, 10000) for report_types in (True, False)
    ]
    for index, (range_m, report_types) in enumerate(six_conditions):
        port_id = "port-001" if index % 2 == 0 else "port-002"
        trial_id = f"trial-{index + 1}"
        row = {
            "trial_id": trial_id,
            "split": "development",
            "port_id": port_id,
            "placement_id": "placement-001",
            "realization": "0",
            "range_m": str(range_m),
            "report_types": str(report_types),
            "physical_seed": str(100 + index),
            "sensor_condition": "nominal",
        }
        trial_rows.append(row)
        trial_dir = (
            input_root
            / "development"
            / port_id
            / "placement-001"
            / "r0"
            / f"{range_m}m"
            / ("types-on" if report_types else "types-off")
            / "nominal"
        )
        trial_dir.mkdir(parents=True)
        (trial_dir / "measurements.json").write_text("{}")
        (trial_dir / "support.json").write_text("{}")
        source_crs = "EPSG:32617" if index % 2 == 0 else "EPSG:32618"
        truth_xy = Transformer.from_crs("EPSG:4326", source_crs, always_xy=True).transform(
            -76.3, 37.0
        )
        write_contract(
            trial_dir / "truth.json",
            EvaluationTruth(
                observer_east_m=truth_xy[0],
                observer_north_m=truth_xy[1],
                observer_heading_deg=0.0,
                objects=(),
                trial_id=trial_id,
            ),
        )
        (trial_dir / "truth-crs.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "trial_id": trial_id,
                    "reference_map_path": str(local_map),
                    "reference_map_sha256": local_map_hash,
                    "coordinate_reference_system": source_crs,
                }
            )
        )
        (trial_dir / "physical-identity-audit.json").write_text(
            json.dumps({"catalog_sha256": catalog_hash})
        )
    # This planned row lacks prepared artifacts and must remain visible in state/manifest.
    trial_rows.append(
        {
            "trial_id": "trial-missing",
            "split": "development",
            "port_id": "port-001",
            "placement_id": "placement-002",
            "realization": "0",
            "range_m": "1000",
            "report_types": "True",
            "physical_seed": "999",
            "sensor_condition": "nominal",
        }
    )
    fieldnames = list(trial_rows[0])
    with (plan_dir / "trials.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(trial_rows)
    plan_path = plan_dir / "plan.json"
    plan_path.write_text(json.dumps({"trial_table": "trials.csv", "split_manifest": "splits.json"}))
    commands_path = tmp_path / "commands.json"
    commands_path.write_text(
        json.dumps(
            {
                "search_argv": [
                    "stub",
                    "search",
                    "--measurements",
                    "{measurements_path}",
                    "--catalog",
                    "{search_catalog_path}",
                    "--map",
                    "{search_map_path}",
                    "--config",
                    "{search_config_path}",
                    "--projected-crs",
                    "{projected_crs}",
                    "--scope-manifest",
                    "{search_scope_manifest_path}",
                    "--output",
                    "{estimate_path}",
                ],
                "evaluator_argv": [
                    "stub",
                    "evaluate",
                    "--estimate",
                    "{evaluator_estimate_path}",
                    "--truth",
                    "{truth_path}",
                    "--support",
                    "{support_path}",
                    "--coordinate-crs",
                    "{truth_coordinate_crs}",
                    "--output",
                    "{evaluation_path}",
                ],
                "search_catalog": str(catalog_path),
                "search_map": str(map_path),
                "search_config": str(search_config_path),
                "search_domain_scope": "full_coast",
                "search_map_scope": "full_coast",
                "projected_crs": "EPSG:32618",
            }
        )
    )
    process_calls = []

    def fake_run(argv, **kwargs):
        process_calls.append(list(argv))
        if argv[1] == "search":
            assert "--truth" not in argv
            output_path = Path(argv[argv.index("--output") + 1])
            write_contract(
                output_path,
                LocalizationResult(
                    hypotheses=(
                        Hypothesis(east_m=observer_xy[0], north_m=observer_xy[1], score=1.0),
                    ),
                    elapsed_s=0.0,
                    decision_status="no_fix",
                    search_status="completed",
                ),
            )
        else:
            Path(argv[argv.index("--output") + 1]).write_text("{}")
        return {
            "status": "completed",
            "returncode": 0,
            "elapsed_s": 0.01,
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr("tools.run_accuracy_suite._run_process", fake_run)
    state_path = run_trials(
        plan_path,
        command_config_path=commands_path,
        input_root=input_root,
        run_root=run_root,
        split="development",
        timeout_s=2.0,
    )
    state = {row["trial_id"]: row for row in csv.DictReader(state_path.open())}
    assert state["trial-missing"]["status"] == "missing_inputs"
    completed = [state[f"trial-{index}"] for index in range(1, 7)]
    assert all(row["status"] == "completed" for row in completed)
    assert len({row["estimate_path"] for row in completed}) == 6
    assert len({row["evaluation_path"] for row in completed}) == 6
    run_dir = state_path.parent
    run_manifest = json.loads((run_dir / "run-manifest.json").read_text())
    assert len(run_manifest["trials"]) == 7
    assert run_manifest["projected_crs"] == "EPSG:32618"
    for row in completed:
        search_config = json.loads((run_dir / row["search_config_path"]).read_text())
        assert search_config["max_range_m"] == float(row["range_m"])
        assert search_config["use_types"] is (row["report_types"] == "True")
        provenance = json.loads((run_dir / row["coordinate_provenance_path"]).read_text())
        assert provenance["source_truth_sha256"] == row["truth_sha256"]
        assert provenance["estimate_target_crs"] == provenance["truth_coordinate_crs"]
        converted_estimate = read_contract(
            run_dir / row["evaluator_estimate_path"], LocalizationResult
        )
        expected = Transformer.from_crs(
            "EPSG:32618", provenance["truth_coordinate_crs"], always_xy=True
        ).transform(observer_xy[0], observer_xy[1])
        assert converted_estimate.hypotheses[0].east_m == pytest.approx(expected[0])
        assert converted_estimate.hypotheses[0].north_m == pytest.approx(expected[1])
    assert len([argv for argv in process_calls if argv[1] == "search"]) == 6
    assert len([argv for argv in process_calls if argv[1] == "evaluate"]) == 6

    changed_row = completed[0]
    changed_input_dir = input_root / "development/port-001/placement-001/r0/1000m/types-on/nominal"
    (changed_input_dir / "support.json").write_text('{"updated":true}')
    old_artifact_path = changed_row["estimate_path"]
    rerun_state_path = run_trials(
        plan_path,
        command_config_path=commands_path,
        input_root=input_root,
        run_root=run_root,
        split="development",
        timeout_s=2.0,
    )
    rerun = {row["trial_id"]: row for row in csv.DictReader(rerun_state_path.open())}
    assert rerun["trial-1"]["status"] == "completed"
    assert rerun["trial-1"]["estimate_path"] != old_artifact_path
    attempts = list(csv.DictReader((run_dir / "attempts.csv").open()))
    assert any(
        row["stage"] == "resume_validation" and row["status"] == "hash_mismatch" for row in attempts
    )
    assert len([argv for argv in process_calls if argv[1] == "search"]) == 7

    search_config_path.write_text(
        json.dumps(
            {
                "contract_type": "SearchConfig",
                "algorithm": "grid",
                "bounds_xy": [300_000.0, 900_000.0, 3_800_000.0, 4_500_000.0],
                "max_range_m": 10_000.0,
                "use_types": True,
            }
        )
    )
    other_algorithm_state = run_trials(
        plan_path,
        command_config_path=commands_path,
        input_root=input_root,
        run_root=run_root,
        split="development",
        timeout_s=2.0,
    )
    other_algorithm = {row["trial_id"]: row for row in csv.DictReader(other_algorithm_state.open())}
    assert other_algorithm_state.parent != run_dir
    assert other_algorithm["trial-1"]["estimate_path"] != rerun["trial-1"]["estimate_path"]
    assert len([argv for argv in process_calls if argv[1] == "search"]) == 13


def test_evaluator_estimate_transform_preserves_geodesic_100m_gate_and_raw_files(
    tmp_path: Path,
) -> None:
    longitude, latitude = -78.05, 37.0
    estimate_crs = "EPSG:32618"
    truth_crs = "EPSG:32617"
    truth_xy = Transformer.from_crs("EPSG:4326", truth_crs, always_xy=True).transform(
        longitude, latitude
    )
    truth_path = tmp_path / "truth.json"
    truth = EvaluationTruth(
        observer_east_m=truth_xy[0],
        observer_north_m=truth_xy[1],
        observer_heading_deg=0.0,
        objects=(),
        trial_id="utm-boundary",
    )
    write_contract(truth_path, truth)
    truth_crs_path = tmp_path / "truth-crs.json"
    truth_crs_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "trial_id": "utm-boundary",
                "coordinate_reference_system": truth_crs,
            }
        )
    )
    support = SupportManifest(
        valid_reference_water=True,
        detected_physical_catalog_matched_groups=3,
    )
    for separation_m, expected_within in ((99.0, True), (101.0, False)):
        estimate_lon, estimate_lat, _ = Geod(ellps="WGS84").fwd(
            longitude, latitude, 90.0, separation_m
        )
        estimate_xy = Transformer.from_crs("EPSG:4326", estimate_crs, always_xy=True).transform(
            estimate_lon, estimate_lat
        )
        estimate_path = tmp_path / f"estimate-{separation_m}.json"
        write_contract(
            estimate_path,
            LocalizationResult(
                hypotheses=(Hypothesis(east_m=estimate_xy[0], north_m=estimate_xy[1], score=0.0),),
                elapsed_s=0.0,
                decision_status="accepted",
                search_status="completed",
                accepted_index=0,
            ),
        )
        raw_estimate_hash = hashlib.sha256(estimate_path.read_bytes()).hexdigest()
        raw_truth_hash = hashlib.sha256(truth_path.read_bytes()).hexdigest()
        evaluator_estimate_path = tmp_path / f"evaluator-estimate-{separation_m}.json"
        provenance_path = tmp_path / f"provenance-{separation_m}.json"
        from tools.run_accuracy_suite import _write_evaluator_estimate

        _write_evaluator_estimate(
            estimate_path,
            truth_path,
            truth_crs_path,
            evaluator_estimate_path,
            provenance_path,
            trial_id="utm-boundary",
            estimate_crs=estimate_crs,
        )
        evaluator_estimate = read_contract(evaluator_estimate_path, LocalizationResult)
        scored = evaluate_localization(
            evaluator_estimate,
            truth,
            support,
            coordinate_crs=truth_crs,
        )
        assert scored.position_error_m == pytest.approx(separation_m, abs=1e-5)
        assert scored.within_100m is expected_within
        assert hashlib.sha256(estimate_path.read_bytes()).hexdigest() == raw_estimate_hash
        assert hashlib.sha256(truth_path.read_bytes()).hexdigest() == raw_truth_hash
        provenance = json.loads(provenance_path.read_text())
        assert provenance["estimate_source_crs"] == estimate_crs
        assert provenance["estimate_target_crs"] == truth_crs
        assert provenance["source_estimate_sha256"] == raw_estimate_hash
        assert provenance["source_truth_sha256"] == raw_truth_hash


def _write_nominal_support_fixture(
    tmp_path: Path, ports: tuple[str, ...]
) -> tuple[Path, Path, int]:
    plan = tmp_path / "config.json"
    table = tmp_path / "trials.csv"
    plan.write_text(json.dumps({"trial_table": "trials.csv", "trial_count": 0}))
    rows = []
    input_root = tmp_path / "inputs"
    for port_id in ports:
        for placement in range(1, 26):
            placement_id = f"placement-{placement:03d}"
            for realization in range(2):
                for range_m in (1000, 5000, 10000):
                    for report_types in (True, False):
                        row = {
                            "trial_id": (
                                f"development/{port_id}/{placement_id}/r{realization}/"
                                f"{range_m}/{report_types}"
                            ),
                            "split": "development",
                            "port_id": port_id,
                            "placement_id": placement_id,
                            "realization": str(realization),
                            "range_m": str(range_m),
                            "report_types": str(report_types),
                            "physical_seed": "123",
                            "sensor_condition": "nominal",
                        }
                        rows.append(row)
                        type_mode = "types-on" if report_types else "types-off"
                        support_path = (
                            input_root
                            / "development"
                            / port_id
                            / placement_id
                            / f"r{realization}"
                            / f"{range_m}m"
                            / type_mode
                            / "nominal"
                            / "support.json"
                        )
                        support_path.parent.mkdir(parents=True, exist_ok=True)
                        support_path.write_text(json.dumps({"supported": True}))
    with table.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return plan, input_root, len(rows)


def test_support_extension_uses_split_aggregate_across_ports(tmp_path: Path) -> None:
    plan, input_root, initial_count = _write_nominal_support_fixture(
        tmp_path, ("port-001", "port-002")
    )
    table = tmp_path / "trials.csv"

    added = _extend_nominal_plan_if_needed(plan, input_root=input_root, split="development")

    assert added == []
    assert len(list(csv.DictReader(table.open()))) == initial_count


def test_support_extension_adds_one_deterministic_25_placement_batch(tmp_path: Path) -> None:
    plan, input_root, initial_count = _write_nominal_support_fixture(tmp_path, ("port-001",))
    table = tmp_path / "trials.csv"

    added = _extend_nominal_plan_if_needed(plan, input_root=input_root, split="development")

    assert len(added) == 25 * 2 * 3 * 2
    rows = list(csv.DictReader(table.open()))
    added_rows = [row for row in rows if row["trial_id"] in set(added)]
    assert len(rows) == initial_count + len(added)
    assert {row["port_id"] for row in added_rows} == {"port-001"}
    assert {row["placement_id"] for row in added_rows} == {
        f"placement-{index:03d}" for index in range(26, 51)
    }


def test_subprocess_timeout_kills_and_reaps_spawned_descendant(tmp_path: Path) -> None:
    pid_path = tmp_path / "grandchild.pid"
    grandchild_code = "import time; time.sleep(60)"
    parent_code = (
        "import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c',sys.argv[1]]); "
        "open(sys.argv[2],'w').write(str(child.pid)); "
        "print('child-started',flush=True); time.sleep(60)"
    )
    outcome = _run_process(
        [sys.executable, "-c", parent_code, grandchild_code, str(pid_path)],
        cwd=Path("."),
        timeout_s=0.4,
    )
    assert outcome["status"] == "timeout"
    assert outcome["process_group_terminated"] is True
    assert outcome["returncode"] is not None
    child_pid = int(pid_path.read_text())
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        proc_stat = Path(f"/proc/{child_pid}/stat")
        if not proc_stat.exists():
            break
        state = proc_stat.read_text().split()[2]
        if state == "Z":
            break
        time.sleep(0.02)
    else:
        pytest.fail(f"timed-out subprocess descendant {child_pid} remained alive")


def test_run_trials_executes_standalone_search_and_geodesic_evaluator(
    tmp_path: Path,
) -> None:
    from pyproj import Geod

    from bearing_localization.search.config import SearchConfig
    from bearing_localization.search.config_serialization import write_search_config
    from maritime_localization_shared.contracts.inputs_v2 import MapV2
    from maritime_localization_shared.serialization import contract_to_dict

    trial_id = "development/port-001/placement-001/r0/range1000m/typeson/nominal"
    timestamp = "2023-07-15T12:00:30Z"
    observer_lon, observer_lat = -78.05, 37.0
    geod = Geod(ellps="WGS84")
    records = []
    for index, (bearing, distance) in enumerate(((20.0, 650.0), (138.0, 900.0), (265.0, 1_100.0))):
        lon, lat, _ = geod.fwd(observer_lon, observer_lat, bearing, distance)
        records.append(
            CatalogRecordV2(
                key=f"mmsi:{index + 1}",
                latitude_deg=lat,
                longitude_deg=lon,
                raw_type="vessel",
                normalized_type="vessel",
                metadata=(("physical_entity_id", f"vessel-{index + 1}"),),
            )
        )
    catalog = CatalogV2(tuple(records), timestamp, "tiny-cli-fixture", "1", "tiny-cli")
    ring = [
        [-79.0, 36.0],
        [-77.0, 36.0],
        [-77.0, 38.0],
        [-79.0, 38.0],
        [-79.0, 36.0],
    ]
    map_input = MapV2(
        crs="EPSG:4326",
        source="tiny-cli-fixture",
        map_version="1",
        coverage_status="known",
        coverage_geometry_geojson=json.dumps(
            {"type": "Polygon", "coordinates": [ring]}, separators=(",", ":")
        ),
    )
    catalog_path = tmp_path / "catalog.json"
    map_path = tmp_path / "coast-map.json"
    write_contract(catalog_path, catalog)
    write_contract(map_path, map_input)

    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    input_root = tmp_path / "inputs"
    trial_dir = input_root / "development/port-001/placement-001/r0/1000m/types-on/nominal"
    trial_dir.mkdir(parents=True)
    fixture_path = tmp_path / "scene.json"
    fixture_path.write_text(
        json.dumps(
            {
                "catalog": contract_to_dict(catalog),
                "map": contract_to_dict(map_input),
                "observer": {
                    "longitude_deg": observer_lon,
                    "latitude_deg": observer_lat,
                    "heading_deg": 37.0,
                },
                "sensor": {
                    "visibility_radius_m": 3_000.0,
                    "angular_resolution_deg": 0.1,
                    "bearing_sigma_deg": 0.05,
                    "detection_probability": 1.0,
                    "type_error_probability": 0.0,
                    "expected_clutter_reports": 0.0,
                    "outlier_probability": 0.0,
                    "report_type_labels": True,
                },
                "timestamp_utc": timestamp,
            }
        ),
        encoding="utf-8",
    )
    measurements_path = trial_dir / "measurements.json"
    truth_path = trial_dir / "truth.json"
    diagnostics_path = trial_dir / "diagnostics.json"
    import subprocess

    subprocess.run(
        [
            sys.executable,
            "-m",
            "bearing_localization.simulator.cli",
            "--schema-version",
            "2",
            "--fixture",
            str(fixture_path),
            "--projected-crs",
            "EPSG:32617",
            "--measurements-out",
            str(measurements_path),
            "--truth-out",
            str(truth_path),
            "--diagnostics-out",
            str(diagnostics_path),
            "--seed",
            "11",
            "--trial-id",
            trial_id,
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )
    support_path = trial_dir / "support.json"
    support_path.write_text(
        json.dumps(
            {
                "valid_reference_water": True,
                "detected_physical_catalog_matched_groups": 3,
                "supported": True,
                "port_id": "port-001",
                "placement_id": "placement-001",
            }
        ),
        encoding="utf-8",
    )
    identity_audit_path = trial_dir / "physical-identity-audit.json"
    identity_audit_path.write_text(
        json.dumps({"catalog_sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest()}),
        encoding="utf-8",
    )
    reference_map = tmp_path / "reference-map.json"
    reference_map.write_bytes(map_path.read_bytes())
    (trial_dir / "truth-crs.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "trial_id": trial_id,
                "reference_map_path": str(reference_map),
                "reference_map_sha256": hashlib.sha256(reference_map.read_bytes()).hexdigest(),
                "coordinate_reference_system": "EPSG:32617",
            }
        ),
        encoding="utf-8",
    )

    search_crs = "EPSG:32618"
    observer_xy = Transformer.from_crs("EPSG:4326", search_crs, always_xy=True).transform(
        observer_lon, observer_lat
    )
    search_config_path = tmp_path / "search.json"
    write_search_config(
        search_config_path,
        SearchConfig(
            algorithm="grid",
            bounds_xy=(
                observer_xy[0] - 1_000.0,
                observer_xy[0] + 1_000.0,
                observer_xy[1] - 1_000.0,
                observer_xy[1] + 1_000.0,
            ),
            max_range_m=1_000.0,
            candidate_budget=500,
        ),
    )
    plan_path, table_path = create_plan(plan_dir)
    planned_rows = list(csv.DictReader(table_path.open()))
    assert trial_id in {row["trial_id"] for row in planned_rows}
    prep_path = input_root / "development" / "preparation.csv"
    prep_path.parent.mkdir(parents=True, exist_ok=True)
    prep_rows = []
    for planned in planned_rows:
        if planned["split"] != "development":
            continue
        type_folder = "types-on" if planned["report_types"] == "True" else "types-off"
        support_dir = (
            input_root
            / "development"
            / planned["port_id"]
            / planned["placement_id"]
            / f"r{planned['realization']}"
            / f"{planned['range_m']}m"
            / type_folder
            / planned["sensor_condition"]
        )
        support_dir.mkdir(parents=True, exist_ok=True)
        supported = planned["trial_id"] == trial_id
        support_count = 3 if supported else 0
        (support_dir / "support.json").write_text(
            json.dumps(
                {
                    "valid_reference_water": supported,
                    "detected_physical_catalog_matched_groups": support_count,
                    "supported": supported,
                    "port_id": planned["port_id"],
                    "placement_id": planned["placement_id"],
                }
            ),
            encoding="utf-8",
        )
        prep_rows.append(
            {
                **{
                    key: planned[key]
                    for key in (
                        "trial_id",
                        "port_id",
                        "placement_id",
                        "realization",
                        "range_m",
                        "report_types",
                        "physical_seed",
                        "sensor_condition",
                    )
                },
                "status": "completed",
                "failure": "",
            }
        )
    with prep_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(prep_rows[0]))
        writer.writeheader()
        writer.writerows(prep_rows)
    from tools.accuracy_eligibility import freeze_eligibility_manifest

    eligibility_path = plan_dir / "eligibility.json"
    freeze_eligibility_manifest(plan_dir, input_root, eligibility_path, "development")
    commands_path = tmp_path / "commands.json"
    commands_path.write_text(
        json.dumps(
            {
                "search_argv": [
                    sys.executable,
                    "-m",
                    "bearing_localization.search_cli",
                    "--schema-version",
                    "2",
                    "--measurements",
                    "{measurements_path}",
                    "--catalog",
                    "{search_catalog_path}",
                    "--map",
                    "{search_map_path}",
                    "--config",
                    "{search_config_path}",
                    "--projected-crs",
                    "{projected_crs}",
                    "--scope-manifest",
                    "{search_scope_manifest_path}",
                    "--algorithm",
                    "grid",
                    "--spacing-m",
                    "100",
                    "--no-refine",
                    "--output",
                    "{estimate_path}",
                ],
                "evaluator_argv": [
                    sys.executable,
                    "-m",
                    "bearing_localization.evaluate_cli",
                    "--schema-version",
                    "2",
                    "--estimate",
                    "{evaluator_estimate_path}",
                    "--truth",
                    "{truth_path}",
                    "--support",
                    "{support_path}",
                    "--coordinate-crs",
                    "{truth_coordinate_crs}",
                    "--output",
                    "{evaluation_path}",
                ],
                "search_catalog": str(catalog_path),
                "search_map": str(map_path),
                "search_config": str(search_config_path),
                "search_domain_scope": "full_coast",
                "search_map_scope": "full_coast",
                "projected_crs": search_crs,
            }
        ),
        encoding="utf-8",
    )

    frozen_support_bytes = support_path.read_bytes()
    state_path = run_trials(
        plan_path,
        command_config_path=commands_path,
        input_root=input_root,
        run_root=tmp_path / "runs",
        split="development",
        timeout_s=60.0,
        eligibility_manifest_path=eligibility_path,
    )
    state = list(csv.DictReader(state_path.open()))
    assert len(state) == 6_120
    row = next(item for item in state if item["trial_id"] == trial_id)
    assert row["status"] == "completed", row["failure"]
    assert support_path.read_bytes() == frozen_support_bytes
    assert Path(row["support_path"]) == support_path
    run_manifest_path = state_path.parent / "run-manifest.json"
    run_manifest = json.loads(run_manifest_path.read_text())
    completed_manifest_row = next(
        item for item in run_manifest["trials"] if item["trial_id"] == trial_id
    )
    assert completed_manifest_row["status"] == "completed"
    assert completed_manifest_row["range_m"] == 1000
    assert completed_manifest_row["report_types"] is True
    expected_seed = int(
        next(item["physical_seed"] for item in planned_rows if item["trial_id"] == trial_id)
    )
    assert completed_manifest_row["physical_seed"] == expected_seed
    assert completed_manifest_row["telemetry_sha256"]
    assert set(run_manifest["condition_contexts"]) == {
        f"{range_m}m_types{label}" for range_m in (1000, 5000, 10000) for label in ("on", "off")
    }
    assert row["truth_coordinate_crs"] == "EPSG:32617"
    provenance = json.loads((state_path.parent / row["coordinate_provenance_path"]).read_text())
    assert provenance["estimate_source_crs"] == search_crs
    assert provenance["estimate_target_crs"] == "EPSG:32617"
    metrics = json.loads((state_path.parent / row["evaluation_path"]).read_text())
    assert "position_error_m" in metrics
    assert measurements_path.is_file() and truth_path.is_file()

    from bearing_localization.search.calibration import (
        CalibrationContext,
        CalibrationOperatingCondition,
    )
    from tools.a38_calibration_v2 import build_condition_document
    from tools.accuracy_eligibility import verify_eligibility_manifest

    eligibility = verify_eligibility_manifest(eligibility_path, plan_dir, input_root, "development")
    binding = run_manifest["condition_contexts"]["1000m_typeson"]
    document = build_condition_document(
        trial_rows=planned_rows,
        run_manifest=run_manifest,
        manifest_path=run_manifest_path,
        input_root=input_root,
        split="development",
        eligibility_snapshot=eligibility,
        eligibility_manifest_path=eligibility_path,
        condition=CalibrationOperatingCondition.from_dict(binding["calibration_condition"]),
        context=CalibrationContext.from_dict(binding["calibration_context"]),
    )
    assert document["population"]["nominal_planned"]["count"] == 600
    assert document["population"]["supported_nominal"]["count"] == 1
    assert any(item["trial_id"] == trial_id for item in document["trials"])
