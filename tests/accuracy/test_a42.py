"""Pre-search denominator freeze and verification regressions."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.accuracy_eligibility import (
    EligibilityError,
    _support_manifest,
    freeze_eligibility_manifest,
    verify_eligibility_manifest,
)

PLAN_DIR = (
    Path(__file__).resolve().parents[2]
    / "benchmarks"
    / "accuracy"
    / "plans"
    / "anchor-corrected-2026-10-26"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _support_rel(row: dict[str, str]) -> Path:
    mode = "types-on" if row["report_types"] == "True" else "types-off"
    return (
        Path(row["split"])
        / row["port_id"]
        / row["placement_id"]
        / f"r{row['realization']}"
        / f"{row['range_m']}m"
        / mode
        / row["sensor_condition"]
        / "support.json"
    )


@pytest.fixture(scope="module")
def complete_support(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("complete-support")
    rows = [row for row in _all_rows() if row["split"] == "development"]
    fields = ["trial_id", "status", "failure", "physical_seed", "sensor_condition"]
    ledger = root / "development" / "preparation.csv"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        nominal_index = 0
        for index, row in enumerate(rows):
            is_nominal = row["sensor_condition"] == "nominal"
            supported = nominal_index % 4 != 0 if is_nominal else True
            if is_nominal:
                nominal_index += 1
            count = 3 + index % 8
            payload = {
                "detected_physical_catalog_matched_groups": count,
                "placement_id": row["placement_id"],
                "port_id": row["port_id"],
                "supported": supported,
                "valid_reference_water": supported,
            }
            path = root / _support_rel(row)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            writer.writerow(
                {
                    "trial_id": row["trial_id"],
                    "status": "completed",
                    "failure": "",
                    "physical_seed": row["physical_seed"],
                    "sensor_condition": row["sensor_condition"],
                }
            )
    return root


@pytest.fixture
def copied_support(tmp_path: Path, complete_support: Path) -> Path:
    target = tmp_path / "support"
    shutil.copytree(complete_support, target, copy_function=os.link)
    return target


def _all_rows() -> list[dict[str, str]]:
    with (PLAN_DIR / "trials.csv").open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _split_rows(condition: str | None = None) -> list[dict[str, str]]:
    return [
        row
        for row in _all_rows()
        if row["split"] == "development"
        and (condition is None or row["sensor_condition"] == condition)
    ]


def _set_preparation(support_root: Path, trial_id: str, *, status: str, failure: str = "") -> None:
    ledger = support_root / "development" / "preparation.csv"
    with ledger.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    for row in rows:
        if row["trial_id"] == trial_id:
            row["status"] = status
            row["failure"] = failure
            break
    else:
        raise AssertionError(f"Missing test ledger row for {trial_id}")
    ledger.unlink()
    with ledger.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_complete_population_freezes_exact_denominator_and_condition_projections(
    complete_support: Path, tmp_path: Path
) -> None:
    manifest = tmp_path / "eligibility.json"
    snapshot = freeze_eligibility_manifest(PLAN_DIR, complete_support, manifest, "development")

    assert snapshot.expected_nominal_count == 3600
    assert len(snapshot.nominal_trials) == 3600
    assert snapshot.supported_denominator == 2700
    assert len(snapshot.nominal_projections) == 6
    assert {len(rows) for rows in snapshot.nominal_projections.values()} == {600}
    assert (
        sum(row.supported is True for rows in snapshot.nominal_projections.values() for row in rows)
        == snapshot.supported_denominator
    )
    assert (
        verify_eligibility_manifest(manifest, PLAN_DIR, complete_support, "development") == snapshot
    )
    assert (
        freeze_eligibility_manifest(PLAN_DIR, complete_support, manifest, "development") == snapshot
    )


def test_partial_pilot_missing_support_cannot_freeze(copied_support: Path, tmp_path: Path) -> None:
    row = _split_rows("nominal")[0]
    (_support := copied_support / _support_rel(row)).unlink()

    with pytest.raises(EligibilityError, match="Missing support"):
        freeze_eligibility_manifest(
            PLAN_DIR, copied_support, tmp_path / "eligibility.json", "development"
        )


def test_edited_support_evidence_invalidates_frozen_manifest(
    copied_support: Path, tmp_path: Path
) -> None:
    manifest = tmp_path / "eligibility.json"
    freeze_eligibility_manifest(PLAN_DIR, copied_support, manifest, "development")
    row = _split_rows("nominal")[0]
    support_file = copied_support / _support_rel(row)
    support_file.unlink()
    support_file.write_text(
        json.dumps(
            {
                "detected_physical_catalog_matched_groups": 3,
                "placement_id": row["placement_id"],
                "port_id": row["port_id"],
                "supported": True,
                "valid_reference_water": True,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(EligibilityError, match="changed|differs"):
        verify_eligibility_manifest(manifest, PLAN_DIR, copied_support, "development")


def test_absent_search_outputs_do_not_change_frozen_denominator(
    complete_support: Path, tmp_path: Path
) -> None:
    manifest = tmp_path / "eligibility.json"
    before = freeze_eligibility_manifest(PLAN_DIR, complete_support, manifest, "development")
    assert not list(tmp_path.rglob("estimate.json"))
    assert not list(tmp_path.rglob("evaluation.json"))

    after = verify_eligibility_manifest(manifest, PLAN_DIR, complete_support, "development")
    assert after.supported_denominator == before.supported_denominator == 2700


@pytest.mark.parametrize("mutation", ["duplicate", "metadata"])
def test_duplicate_ids_and_changed_condition_metadata_fail(tmp_path: Path, mutation: str) -> None:
    plan = tmp_path / "plan"
    shutil.copytree(PLAN_DIR, plan)
    table_path = plan / "trials.csv"
    with table_path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
        fields = rows[0].keys()
    if mutation == "duplicate":
        rows[1]["trial_id"] = rows[0]["trial_id"]
    else:
        rows[0]["placement_id"] = "placement-002"
    with table_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    config_path = plan / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["trial_table_sha256"] = _sha256(table_path)
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(EligibilityError):
        freeze_eligibility_manifest(
            plan, tmp_path / "empty-support", tmp_path / "eligibility.json", "development"
        )


def test_explicit_failed_stress_is_retained_as_unresolved(
    copied_support: Path, tmp_path: Path
) -> None:
    row = next(row for row in _split_rows() if row["sensor_condition"] != "nominal")
    (copied_support / _support_rel(row)).unlink()
    failure = "ValueError: Observer must lie in known water."
    _set_preparation(copied_support, row["trial_id"], status="failed", failure=failure)

    snapshot = freeze_eligibility_manifest(
        PLAN_DIR, copied_support, tmp_path / "eligibility.json", "development"
    )
    unresolved = next(item for item in snapshot.trials if item.trial_id == row["trial_id"])
    assert len(snapshot.trials) == snapshot.expected_trial_count == 6120
    assert unresolved.status == "unresolved"
    assert unresolved.supported is None
    assert unresolved.support_count is None
    assert unresolved.support_path is None
    assert unresolved.support_sha256 is None
    assert unresolved.preparation_status == "failed"
    assert unresolved.preparation_reason == failure
    assert unresolved.eligibility_status == "unresolved"
    assert unresolved.eligibility_reason == failure
    assert snapshot.supported_denominator == 2700


def test_unresolved_nominal_support_blocks_freeze(copied_support: Path, tmp_path: Path) -> None:
    row = _split_rows("nominal")[1]
    (copied_support / _support_rel(row)).unlink()
    _set_preparation(copied_support, row["trial_id"], status="failed", failure="prep failed")
    with pytest.raises(EligibilityError, match="Unresolved nominal"):
        freeze_eligibility_manifest(
            PLAN_DIR, copied_support, tmp_path / "eligibility.json", "development"
        )


def test_missing_preparation_ledger_trial_blocks_freeze(
    copied_support: Path, tmp_path: Path
) -> None:
    ledger = copied_support / "development" / "preparation.csv"
    with ledger.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    ledger.unlink()
    with ledger.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows[:-1])
    with pytest.raises(EligibilityError, match="ledger population mismatch"):
        freeze_eligibility_manifest(
            PLAN_DIR, copied_support, tmp_path / "eligibility.json", "development"
        )


def _run_extension_batches(plan: Path, support_root: Path, batch_count: int) -> list[str]:
    from tools.run_accuracy_suite import _extend_nominal_plan_if_needed

    added_ids: list[str] = []
    ledger = support_root / "development" / "preparation.csv"
    with ledger.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        fields = list(reader.fieldnames or [])
        preparation = list(reader)
    plan_config = json.loads((plan / "config.json").read_text(encoding="utf-8"))
    for _ in range(batch_count):
        new_ids = _extend_nominal_plan_if_needed(
            plan / "config.json", input_root=support_root, split="development"
        )
        if not new_ids:
            break
        with (plan / plan_config["trial_table"]).open(newline="", encoding="utf-8") as stream:
            planned = {row["trial_id"]: row for row in csv.DictReader(stream)}
        for trial_id in new_ids:
            row = planned[trial_id]
            support_path = support_root / _support_rel(row)
            support_path.parent.mkdir(parents=True, exist_ok=True)
            support_path.write_text(
                json.dumps(
                    {
                        "detected_physical_catalog_matched_groups": 0,
                        "placement_id": row["placement_id"],
                        "port_id": row["port_id"],
                        "supported": False,
                        "valid_reference_water": False,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            preparation.append(
                {
                    "trial_id": trial_id,
                    "status": "completed",
                    "failure": "",
                    "physical_seed": row["physical_seed"],
                    "sensor_condition": row["sensor_condition"],
                }
            )
        added_ids.extend(new_ids)
        plan_config = json.loads((plan / "config.json").read_text(encoding="utf-8"))
    ledger.unlink()
    with ledger.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(preparation)
    return added_ids


@pytest.mark.parametrize("batch_count", [0, 1, 2, 3])
def test_producer_extensions_25_50_75_are_plan_complete(
    tmp_path: Path, complete_support: Path, batch_count: int
) -> None:
    plan = tmp_path / "plan"
    shutil.copytree(PLAN_DIR, plan)
    support = tmp_path / "support"
    shutil.copytree(complete_support, support)
    # Force the producer's initial support gate to remain deficient.
    for row in _split_rows("nominal"):
        path = support / _support_rel(row)
        document = json.loads(path.read_text(encoding="utf-8"))
        document["supported"] = False
        document["valid_reference_water"] = False
        path.write_text(json.dumps(document) + "\n", encoding="utf-8")

    added = _run_extension_batches(plan, support, batch_count)
    assert len(added) == 300 * batch_count
    config = json.loads((plan / "config.json").read_text(encoding="utf-8"))
    history = config.get("support_extension_batches", [])
    assert len(history) == batch_count
    assert [(batch["first_placement"], batch["last_placement"]) for batch in history] == [
        (26 + 25 * index, 50 + 25 * index) for index in range(batch_count)
    ]
    manifest = tmp_path / "eligibility.json"
    snapshot = freeze_eligibility_manifest(plan, support, manifest, "development")
    assert snapshot.expected_nominal_count == 3600 + 300 * batch_count
    assert len(snapshot.trials) == 6120 + 300 * batch_count


@pytest.mark.parametrize("mutation", ["boundary", "count", "gate"])
def test_invalid_extension_history_is_rejected(
    tmp_path: Path, complete_support: Path, mutation: str
) -> None:
    plan = tmp_path / "plan"
    shutil.copytree(PLAN_DIR, plan)
    support = tmp_path / "support"
    shutil.copytree(complete_support, support)
    for row in _split_rows("nominal"):
        path = support / _support_rel(row)
        document = json.loads(path.read_text(encoding="utf-8"))
        document["supported"] = False
        document["valid_reference_water"] = False
        path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    _run_extension_batches(plan, support, 1)
    config_path = plan / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    batch = config["support_extension_batches"][0]
    if mutation == "boundary":
        batch["last_placement"] = 49
    elif mutation == "count":
        key = next(iter(batch["supported_units_before_batch"]))
        batch["supported_units_before_batch"][key] = 99
    else:
        batch["deficient_gates_before_batch"].reverse()
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(EligibilityError):
        freeze_eligibility_manifest(plan, support, tmp_path / "eligibility.json", "development")


def test_optional_supported_flag_preserves_eligibility_record(
    complete_support: Path, tmp_path: Path
) -> None:
    with_flag = tmp_path / "with-flag"
    without_flag = tmp_path / "without-flag"
    shutil.copytree(complete_support, with_flag)
    shutil.copytree(complete_support, without_flag)
    row = _split_rows("nominal")[0]
    unflagged_path = without_flag / _support_rel(row)
    document = json.loads(unflagged_path.read_text(encoding="utf-8"))
    document.pop("supported")
    unflagged_path.write_text(json.dumps(document) + "\n", encoding="utf-8")

    flagged = freeze_eligibility_manifest(
        PLAN_DIR, with_flag, tmp_path / "flagged.json", "development"
    )
    unflagged = freeze_eligibility_manifest(
        PLAN_DIR, without_flag, tmp_path / "unflagged.json", "development"
    )
    flagged_trial = next(trial for trial in flagged.trials if trial.trial_id == row["trial_id"])
    unflagged_trial = next(trial for trial in unflagged.trials if trial.trial_id == row["trial_id"])
    assert replace(unflagged_trial, support_sha256=flagged_trial.support_sha256) == flagged_trial


@pytest.mark.parametrize(
    "mutation",
    [
        "correct_true",
        "correct_false",
        "correct_false_threshold",
        "mismatch_true",
        "mismatch_false",
        "nonboolean_flag",
        "boolean_count",
        "negative_count",
        "string_count",
        "nonboolean_water",
        "unknown_field",
    ],
)
def test_support_manifest_optional_flag_and_strict_fields(mutation: str) -> None:
    payload = {
        "valid_reference_water": True,
        "detected_physical_catalog_matched_groups": 3,
        "port_id": "port-001",
        "placement_id": "placement-001",
    }
    expected = True
    if mutation == "correct_true":
        payload["supported"] = True
    elif mutation == "correct_false":
        payload["valid_reference_water"] = False
        payload["supported"] = False
        expected = False
    elif mutation == "correct_false_threshold":
        payload["detected_physical_catalog_matched_groups"] = 2
        payload["supported"] = False
        expected = False
    elif mutation == "mismatch_true":
        payload["supported"] = False
    elif mutation == "mismatch_false":
        payload["detected_physical_catalog_matched_groups"] = 2
        payload["supported"] = True
    elif mutation == "nonboolean_flag":
        payload["supported"] = 1
    elif mutation == "boolean_count":
        payload["detected_physical_catalog_matched_groups"] = True
    elif mutation == "negative_count":
        payload["detected_physical_catalog_matched_groups"] = -1
    elif mutation == "string_count":
        payload["detected_physical_catalog_matched_groups"] = "3"
    elif mutation == "nonboolean_water":
        payload["valid_reference_water"] = 1
    elif mutation == "unknown_field":
        payload["unexpected"] = "rejected"

    if mutation.startswith("correct_"):
        assert _support_manifest(payload) == (
            expected,
            payload["detected_physical_catalog_matched_groups"],
        )
    else:
        with pytest.raises(EligibilityError):
            _support_manifest(payload)
