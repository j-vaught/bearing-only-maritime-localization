from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
from pyproj import Geod, Transformer

import bearing_localization.search as search_api
from bearing_localization.accuracy_cli import _validate_scope_manifest
from bearing_localization.algorithms import SearchResult
from bearing_localization.evaluation.metrics import SupportManifest
from bearing_localization.search.config import SearchConfig
from bearing_localization.search.config_serialization import write_search_config
from maritime_localization_shared.contracts import (
    AISRecord,
    AISSnapshot,
    CatalogRecordV2,
    CatalogV2,
    MapV2,
    MeasurementSet,
    Observation,
)
from maritime_localization_shared.contracts.estimate import PositionEstimate
from maritime_localization_shared.contracts.localization import LocalizationResult
from maritime_localization_shared.serialization import (
    contract_to_dict,
    read_contract,
    write_contract,
)

ROOT = Path(__file__).resolve().parents[2]


def _v2_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    timestamp = "2026-10-25T12:00:00Z"
    longitude, latitude = -76.0, 37.0
    geod = Geod(ellps="WGS84")
    records = []
    for index, (bearing, distance) in enumerate(((20.0, 650.0), (138.0, 900.0), (265.0, 1_100.0))):
        lon, lat, _ = geod.fwd(longitude, latitude, bearing, distance)
        records.append(
            CatalogRecordV2(
                key=f"target-{index}",
                latitude_deg=lat,
                longitude_deg=lon,
                raw_type="vessel",
                normalized_type="vessel",
            )
        )
    catalog = CatalogV2(tuple(records), timestamp, "tiny-fixture", "1", "tiny-catalog")
    ring = [[-77.0, 36.0], [-75.0, 36.0], [-75.0, 38.0], [-77.0, 38.0], [-77.0, 36.0]]
    map_input = MapV2(
        crs="EPSG:4326",
        source="tiny-fixture",
        map_version="1",
        coverage_status="known",
        coverage_geometry_geojson=json.dumps(
            {"type": "Polygon", "coordinates": [ring]}, separators=(",", ":")
        ),
    )
    catalog_path = tmp_path / "catalog.json"
    map_path = tmp_path / "map.json"
    fixture_path = tmp_path / "scene.json"
    measurement_path = tmp_path / "measurements.json"
    truth_path = tmp_path / "truth.json"
    output_dir = tmp_path / "sim"
    write_contract(catalog_path, catalog)
    write_contract(map_path, map_input)
    fixture_path.write_text(
        json.dumps(
            {
                "catalog": contract_to_dict(catalog),
                "map": contract_to_dict(map_input),
                "observer": {
                    "longitude_deg": longitude,
                    "latitude_deg": latitude,
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
            "EPSG:32618",
            "--measurements-out",
            str(measurement_path),
            "--truth-out",
            str(truth_path),
            "--diagnostics-out",
            str(output_dir / "diagnostics.json"),
            "--seed",
            "11",
            "--trial-id",
            "tiny-trial",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    config_path = tmp_path / "config.json"
    x, y = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform(
        longitude, latitude
    )
    config = SearchConfig(
        algorithm="grid",
        bounds_xy=(x - 100.0, x + 100.0, y - 100.0, y + 100.0),
        max_range_m=3_000.0,
        candidate_budget=100,
    )
    write_search_config(config_path, config)
    return measurement_path, catalog_path, map_path, config_path, truth_path


def test_v2_cli_simulator_search_after_truth_removal_and_evaluator(tmp_path: Path) -> None:
    measurement_path, catalog_path, map_path, config_path, truth_path = _v2_fixture(tmp_path)
    result_path = tmp_path / "result.json"
    search_args = [
        sys.executable,
        "-m",
        "bearing_localization.search_cli",
        "--schema-version",
        "2",
        "--measurements",
        str(measurement_path),
        "--catalog",
        str(catalog_path),
        "--map",
        str(map_path),
        "--config",
        str(config_path),
        "--projected-crs",
        "EPSG:32618",
        "--output",
        str(result_path),
        "--algorithm",
        "grid",
        "--spacing-m",
        "100",
        "--no-refine",
    ]
    truth_document = truth_path.read_bytes()
    truth_path.unlink()
    subprocess.run(search_args, cwd=ROOT, check=True, capture_output=True, text=True)
    first = read_contract(result_path, LocalizationResult)
    assert first.search_status == "completed"
    assert first.decision_status == "ambiguous"
    assert first.hypotheses
    telemetry = json.loads(result_path.with_suffix(".json.telemetry.json").read_text())
    assert telemetry["telemetry"]["backend"] == "cpu"
    assert telemetry["telemetry"]["stages"][0]["name"] == "load_inputs"
    assert telemetry["diagnostics"]["decision_reason"] == "missing_frozen_calibration"

    repeat_path = tmp_path / "repeat.json"
    repeat_args = [
        str(repeat_path) if value == str(result_path) else value for value in search_args
    ]
    subprocess.run(repeat_args, cwd=ROOT, check=True, capture_output=True, text=True)
    repeat = read_contract(repeat_path, LocalizationResult)
    assert repeat.hypotheses == first.hypotheses
    assert not truth_path.exists()

    truth_path.write_bytes(truth_document)
    support_path = tmp_path / "support.json"
    support_path.write_text(
        json.dumps(asdict(SupportManifest(True, 3, "port-01", "placement-00"))),
        encoding="utf-8",
    )
    metrics_path = tmp_path / "metrics.json"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "bearing_localization.evaluate_cli",
            "--schema-version",
            "2",
            "--estimate",
            str(result_path),
            "--truth",
            str(truth_path),
            "--support",
            str(support_path),
            "--output",
            str(metrics_path),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    metrics = json.loads(metrics_path.read_text())
    assert metrics["acceptance_status"] == "ambiguous"
    assert metrics["manifest"]["detected_physical_catalog_matched_groups"] == 3


def test_v2_cli_malformed_config_exits_nonzero_without_result(tmp_path: Path) -> None:
    measurement_path, catalog_path, map_path, _config, _truth = _v2_fixture(tmp_path)
    bad_config = tmp_path / "bad-config.json"
    bad_config.write_text("{", encoding="utf-8")
    result_path = tmp_path / "failed.json"
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "bearing_localization.search_cli",
            "--schema-version",
            "2",
            "--measurements",
            str(measurement_path),
            "--catalog",
            str(catalog_path),
            "--map",
            str(map_path),
            "--config",
            str(bad_config),
            "--projected-crs",
            "EPSG:32618",
            "--output",
            str(result_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert process.returncode != 0
    assert not result_path.exists()


def test_legacy_hierarchical_adapter_preserves_each_report_sigma(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    measurements = MeasurementSet(
        observations=(
            Observation(object_type="vessel", bearing_deg=10.0, bearing_sigma_deg=0.1),
            Observation(object_type="vessel", bearing_deg=30.0, bearing_sigma_deg=1.5),
            Observation(object_type="vessel", bearing_deg=80.0, bearing_sigma_deg=0.0),
        ),
        trial_id="sigma-test",
        timestamp_utc="2026-10-25T12:00:00Z",
    )
    ais = AISSnapshot(
        records=(AISRecord(record_id="a", east_m=1.0, north_m=2.0, ais_class="vessel"),),
        timestamp_utc="2026-10-25T12:00:00Z",
        crs="local-EN",
        snapshot_id="sigma-test",
    )
    captured: dict[str, object] = {}

    def stub(*args, **kwargs):
        captured["sigma_deg"] = kwargs["sigma_deg"]
        return SearchResult(0.0, 0.0, 0.0, (), 0.0, 1)

    monkeypatch.setattr(search_api, "hierarchical_search_core", stub)
    estimate = search_api.run_hierarchical_search(
        measurements,
        ais,
        (-10.0, 10.0, -10.0, 10.0),
        search_api.HierarchicalSearchConfig(bearing_sigma_deg=0.5),
    )
    assert captured["sigma_deg"] == [0.1, 1.5, 0.5]
    assert isinstance(estimate, PositionEstimate)


def test_v2_main_orchestrates_independent_search_and_evaluator_files(tmp_path: Path) -> None:
    measurement_path, catalog_path, map_path, config_path, truth_path = _v2_fixture(tmp_path)
    support_path = tmp_path / "support.json"
    support_path.write_text(
        json.dumps(asdict(SupportManifest(True, 3, "port-01", "placement-00"))),
        encoding="utf-8",
    )
    estimate_path = tmp_path / "pipeline-result.json"
    metrics_path = tmp_path / "pipeline-metrics.json"
    subprocess.run(
        [
            sys.executable,
            "main.py",
            "--schema-version",
            "2",
            "--measurements",
            str(measurement_path),
            "--catalog",
            str(catalog_path),
            "--map",
            str(map_path),
            "--config",
            str(config_path),
            "--projected-crs",
            "EPSG:32618",
            "--truth",
            str(truth_path),
            "--support",
            str(support_path),
            "--estimate-output",
            str(estimate_path),
            "--metrics-output",
            str(metrics_path),
            "--algorithm",
            "grid",
            "--spacing-m",
            "100",
            "--no-refine",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    result = read_contract(estimate_path, LocalizationResult)
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert result.search_status == "completed"
    assert result.decision_status == "ambiguous"
    assert metrics["acceptance_status"] == "ambiguous"


def test_scope_manifest_binds_full_coast_inputs_and_projected_bounds(tmp_path: Path) -> None:
    measurement_path, catalog_path, map_path, config_path, _truth_path = _v2_fixture(tmp_path)
    del measurement_path
    from bearing_localization.search.config_serialization import read_search_config

    search_config = read_search_config(config_path)
    assert search_config.bounds_xy is not None
    manifest_path = tmp_path / "scope.json"
    manifest = {
        "schema_version": 1,
        "geographic_scope": "full_coast",
        "catalog_sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
        "map_sha256": hashlib.sha256(map_path.read_bytes()).hexdigest(),
        "projected_crs": "EPSG:32618",
        "bounds_xy_m": list(search_config.bounds_xy),
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _validate_scope_manifest(
        manifest_path,
        catalog_path=catalog_path,
        map_path=map_path,
        projected_crs="EPSG:32618",
        bounds_xy=search_config.bounds_xy,
    )

    manifest["catalog_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        _validate_scope_manifest(
            manifest_path,
            catalog_path=catalog_path,
            map_path=map_path,
            projected_crs="EPSG:32618",
            bounds_xy=search_config.bounds_xy,
        )
