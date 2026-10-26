from __future__ import annotations

import json
import sys
from pathlib import Path

from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from maritime_localization_shared.contracts.inputs_v2 import MapV2
from maritime_localization_shared.serialization import write_contract
from tools.run_accuracy_pilot import full_domain_bounds, select_pilot_rows


def _row(
    trial_id: str, range_m: int, labels: bool, *, condition: str = "nominal"
) -> dict[str, str]:
    return {
        "trial_id": trial_id,
        "split": "development",
        "port_id": "port-001",
        "placement_id": trial_id.split("/")[2],
        "realization": "0",
        "range_m": str(range_m),
        "report_types": str(labels),
        "physical_seed": "100",
        "sensor_condition": condition,
    }


def test_pilot_selects_by_presearch_support_and_retains_failure(tmp_path: Path) -> None:
    rows = []
    preparation = []
    input_root = tmp_path / "inputs"
    for range_m in (1000, 5000, 10000):
        for labels in (True, False):
            trial_id = (
                f"development/port-001/placement-{range_m}-{labels}/r0/range{range_m}/nominal"
            )
            row = _row(trial_id, range_m, labels)
            rows.append(row)
            trial_dir = (
                input_root
                / "development/port-001"
                / row["placement_id"]
                / "r0"
                / f"{range_m}m"
                / ("types-on" if labels else "types-off")
                / "nominal"
            )
            trial_dir.mkdir(parents=True)
            (trial_dir / "support.json").write_text(
                json.dumps({"supported": True, "detected_physical_catalog_matched_groups": 3})
            )
            preparation.append({"trial_id": trial_id, "status": "completed", "failure": ""})

    unsupported = _row(
        "development/port-001/placement-unsupported/r0/range1000/typeson/nominal",
        1000,
        True,
    )
    rows.append(unsupported)
    unsupported_dir = (
        input_root / "development/port-001/placement-unsupported/r0/1000m/types-on/nominal"
    )
    unsupported_dir.mkdir(parents=True)
    (unsupported_dir / "support.json").write_text(
        json.dumps({"supported": False, "detected_physical_catalog_matched_groups": 0})
    )
    preparation.append({"trial_id": unsupported["trial_id"], "status": "completed", "failure": ""})

    failed = _row(
        "development/port-001/placement-failed/r0/range1000/map_buffer",
        1000,
        True,
        condition="map_buffer",
    )
    rows.append(failed)
    preparation.append(
        {"trial_id": failed["trial_id"], "status": "failed", "failure": "observer_not_water"}
    )

    selected, manifest = select_pilot_rows(
        rows, preparation, input_root=input_root, supported_per_gate=1
    )
    assert len(selected) == 8
    roles = {entry["selection_role"] for entry in manifest}
    assert roles == {
        "supported_nominal",
        "unsupported_nominal",
        "retained_preparation_failure",
    }
    assert sum(entry["selection_role"] == "supported_nominal" for entry in manifest) == 6
    failure = next(
        entry for entry in manifest if entry["selection_role"] == "retained_preparation_failure"
    )
    assert failure["preparation_failure"] == "observer_not_water"
    assert failure["all_required_inputs_present"] is False
    assert "outcome" not in json.dumps(manifest).lower()


def test_full_domain_bounds_include_known_and_unknown_map_geometry(tmp_path: Path) -> None:
    map_path = tmp_path / "map.json"
    known = {
        "type": "Polygon",
        "coordinates": [[[-76, 36], [-75, 36], [-75, 37], [-76, 37], [-76, 36]]],
    }
    unknown = {
        "type": "Polygon",
        "coordinates": [[[-74, 36], [-73, 36], [-73, 37], [-74, 37], [-74, 36]]],
    }
    write_contract(
        map_path,
        MapV2(
            crs="EPSG:4326",
            source="pilot-test",
            map_version="1",
            coverage_status="partial",
            coverage_geometry_geojson=json.dumps(known),
            unknown_geometry_geojson=json.dumps(unknown),
        ),
    )
    bounds = full_domain_bounds(map_path, "EPSG:32618", margin_m=10_000.0)
    west_known, south_known = Transformer.from_crs(
        "EPSG:4326", "EPSG:32618", always_xy=True
    ).transform(-76, 36)
    east_unknown, north_unknown = Transformer.from_crs(
        "EPSG:4326", "EPSG:32618", always_xy=True
    ).transform(-73, 37)
    assert bounds[0] < west_known - 9_000
    assert bounds[1] > east_unknown + 9_000
    assert bounds[2] < south_known - 9_000
    assert bounds[3] > north_unknown + 9_000


def test_pilot_executes_truth_free_search_and_separates_counters(
    tmp_path: Path, monkeypatch
) -> None:
    import csv
    from types import SimpleNamespace

    import tools.run_accuracy_pilot as pilot

    input_root = tmp_path / "inputs"
    rows = []
    preparation = []
    for range_m in (1000, 5000, 10000):
        for labels in (True, False):
            trial_id = f"development/port-001/placement-{range_m}-{labels}/r0/nominal"
            row = _row(trial_id, range_m, labels)
            rows.append(row)
            trial_dir = (
                input_root
                / "development/port-001"
                / row["placement_id"]
                / "r0"
                / f"{range_m}m"
                / ("types-on" if labels else "types-off")
                / "nominal"
            )
            trial_dir.mkdir(parents=True)
            (trial_dir / "measurements.json").write_text("{}")
            (trial_dir / "support.json").write_text(
                json.dumps({"supported": True, "detected_physical_catalog_matched_groups": 3})
            )
            preparation.append({"trial_id": trial_id, "status": "completed", "failure": ""})

    unsupported = _row("development/port-001/placement-unsupported/r0/nominal", 1000, True)
    rows.append(unsupported)
    unsupported_dir = (
        input_root / "development/port-001/placement-unsupported/r0/1000m/types-on/nominal"
    )
    unsupported_dir.mkdir(parents=True)
    (unsupported_dir / "measurements.json").write_text("{}")
    (unsupported_dir / "support.json").write_text(
        json.dumps({"supported": False, "detected_physical_catalog_matched_groups": 0})
    )
    preparation.append({"trial_id": unsupported["trial_id"], "status": "completed", "failure": ""})
    failed = _row(
        "development/port-010/placement-failed/r0/failure",
        1000,
        True,
        condition="map_buffer",
    )
    rows.append(failed)
    preparation.append({"trial_id": failed["trial_id"], "status": "failed", "failure": "map"})

    plan_path = tmp_path / "trials.csv"
    with plan_path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    prep_path = tmp_path / "preparation.csv"
    with prep_path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(preparation[0]))
        writer.writeheader()
        writer.writerows(preparation)
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text("catalog fixture")
    map_path = tmp_path / "map.json"
    known = {
        "type": "Polygon",
        "coordinates": [[[-76, 36], [-75, 36], [-75, 37], [-76, 37], [-76, 36]]],
    }
    unknown = {
        "type": "Polygon",
        "coordinates": [[[-74, 36], [-73, 36], [-73, 37], [-74, 37], [-74, 36]]],
    }
    write_contract(
        map_path,
        MapV2(
            crs="EPSG:4326",
            source="pilot-test",
            map_version="1",
            coverage_status="partial",
            coverage_geometry_geojson=json.dumps(known),
            unknown_geometry_geojson=json.dumps(unknown),
        ),
    )
    config_path = tmp_path / "pilot.json"
    config = {
        "schema": "accuracy-feasibility-pilot-v1",
        "split": "development",
        "search_domain_scope": "full_coast",
        "domain_margin_m": 10000,
        "supported_cases_per_gate": 1,
        "algorithms": ["grid"],
        "candidate_budgets": [64],
        "grid_spacing_m": 50000,
        "max_grid_bearing_error_deg": 0.5,
        "wedge_cell_size_m": 10000,
        "timeout_s": 10,
        "objective_version": "bearing_set_map_v1",
        "generated_sigma_deg": 0.5,
        "detection_probability": 0.9,
        "bearing_outlier_probability": 0.05,
        "expected_unmatched_reports": 1,
    }
    config_path.write_text(json.dumps(config))
    calls = []

    def fake_subprocess(argv, **kwargs):
        if argv[0] == "git":
            return SimpleNamespace(stdout="test-source\n")
        calls.append(list(argv))
        assert not any(arg == "--truth" or arg.endswith("truth.json") for arg in argv)
        estimate_path = Path(argv[argv.index("--output") + 1])
        telemetry_path = Path(argv[argv.index("--telemetry-output") + 1])
        estimate_path.write_text(
            json.dumps(
                {
                    "search_status": "budget_exhausted",
                    "budget_limit": 64,
                    "budget_used": 64,
                    "certificate_scope": "finite_candidate_set",
                }
            )
        )
        telemetry_path.write_text(
            json.dumps(
                {
                    "diagnostics": {
                        "candidate_count": 64,
                        "candidate_counts": {"total": 1000, "evaluated": 64},
                    },
                    "telemetry": {
                        "counts": {"generated": 64, "evaluated": 64, "retained": 3},
                        "stages": [{"name": "candidate_search", "wall_s": 0.1}],
                    },
                }
            )
        )
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(pilot.subprocess, "run", fake_subprocess)
    result_path = pilot.run_pilot(
        trial_table_path=plan_path,
        preparation_manifest_path=prep_path,
        input_root=input_root,
        catalog_path=catalog_path,
        map_path=map_path,
        output_root=tmp_path / "pilot-output",
        config_path=config_path,
    )
    results = json.loads(result_path.read_text())
    assert len(calls) == 7
    assert results["truth_passed_to_search"] is False
    assert results["evaluator_invoked"] is False
    run_doc = json.loads(Path(results["runs"][0]["run_manifest_path"]).read_text())
    assert run_doc["truth_passed_to_search"] is False
    assert run_doc["evaluator_invoked"] is False
    assert sum(row["status"] == "completed" for row in run_doc["trials"]) == 7
    assert all(
        row["search_status"] == "budget_exhausted"
        for row in run_doc["trials"]
        if row["status"] == "completed"
    )
    assert any(row["status"] == "missing_inputs" for row in run_doc["trials"])
    record = next(row for row in run_doc["trials"] if row["selection_role"] == "supported_nominal")
    assert record["proposal_generation_counters"]["candidate_counts"]["total"] == 1000
    assert record["scoring_and_refinement_telemetry"]["counts"]["evaluated"] == 64
