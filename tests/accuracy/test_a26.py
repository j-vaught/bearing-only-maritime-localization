from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from types import SimpleNamespace

import pytest

from tools.run_accuracy_suite import (  # noqa: E402
    _extend_nominal_plan_if_needed,
    _load_command_config,
    _run_process,
    build_split_manifest,
    create_plan,
    run_trials,
)


def test_split_manifest_moves_whole_overlap_component_to_development() -> None:
    manifest = build_split_manifest()
    assignments = {port["source_site_id"]: port["effective_split"] for port in manifest["ports"]}

    assert len(manifest["ports"]) == 24
    assert assignments["B8"] == "development"
    assert manifest["split_port_ids"]["development"] == [
        f"port-{index:03d}" for index in (1, 3, 6, 7, 8, 9, 10, 11, 13, 14, 17, 20, 23)
    ]
    assert len(manifest["split_port_ids"]["validation"]) == 3
    assert len(manifest["split_port_ids"]["heldout"]) == 8
    assert manifest["overlap_decisions"][0]["port_ids"] == [
        "port-006",
        "port-007",
        "port-008",
    ]
    assert manifest["legacy_b1_b5_separate"] is True


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
            "EPSG:32618",
            "--scope-manifest",
            "{search_scope_manifest_path}",
            "{estimate_path}",
        ],
        "evaluator_argv": [
            "eval",
            "{estimate_path}",
            "{truth_path}",
            "{support_path}",
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


def test_runner_retains_failure_attempt_then_resumes(tmp_path: Path, monkeypatch) -> None:
    plan_dir = tmp_path / "plan"
    run_root = tmp_path / "runs"
    input_root = tmp_path / "inputs"
    plan_dir.mkdir()
    input_dir = input_root / "development/port-001/placement-001/r0/1000m/types-on/nominal"
    input_dir.mkdir(parents=True)
    for name in ("measurements.json", "truth.json", "support.json"):
        (input_dir / name).write_text("{}")
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
                    "EPSG:32618",
                    "--scope-manifest",
                    "{search_scope_manifest_path}",
                    "{estimate_path}",
                ],
                "evaluator_argv": [
                    "stub",
                    "evaluate",
                    "{estimate_path}",
                    "{truth_path}",
                    "{support_path}",
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
        if argv[0] == "git":
            return SimpleNamespace(returncode=0, stdout="abc123\n", stderr="")
        if argv[1] == "search" and fail_search:
            return SimpleNamespace(returncode=17, stdout="search stdout", stderr="search failed")
        if argv[1] == "search":
            Path(argv[-1]).write_text("{}")
        elif argv[1] == "evaluate":
            Path(argv[-1]).write_text("{}")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("tools.run_accuracy_suite.subprocess.run", fake_run)
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
    scope_manifest = json.loads((run_root / "scope-manifest.json").read_text())
    assert scope_manifest == {
        "schema_version": 1,
        "geographic_scope": "full_coast",
        "catalog_sha256": catalog_hash,
        "map_sha256": hashlib.sha256((tmp_path / "coast-map.json").read_bytes()).hexdigest(),
        "projected_crs": "EPSG:32618",
        "bounds_xy_m": [-10.0, 10.0, -20.0, 20.0],
    }
    attempts_path = run_root / "attempts.csv"
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


def test_subprocess_timeout_is_an_explicit_outcome(monkeypatch) -> None:
    def timeout(*_args, **_kwargs):
        raise __import__("subprocess").TimeoutExpired(["search"], timeout=0.01, output="partial")

    monkeypatch.setattr("tools.run_accuracy_suite.subprocess.run", timeout)
    outcome = _run_process(["search"], cwd=Path("."), timeout_s=0.01)
    assert outcome["status"] == "timeout"
    assert outcome["stdout"] == "partial"
