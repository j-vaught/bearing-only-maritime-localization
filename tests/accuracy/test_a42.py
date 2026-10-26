"""Pre-search denominator freeze and verification regressions."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.accuracy_eligibility import (
    EligibilityError,
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
    with (PLAN_DIR / "trials.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    for index, row in enumerate(
        row
        for row in rows
        if row["split"] == "development" and row["sensor_condition"] == "nominal"
    ):
        count = 3 + index % 8
        payload = {
            "detected_physical_catalog_matched_groups": count,
            "placement_id": row["placement_id"],
            "port_id": row["port_id"],
            "supported": index % 4 != 0,
            "valid_reference_water": index % 4 != 0,
        }
        path = root / _support_rel(row)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return root


@pytest.fixture
def copied_support(tmp_path: Path, complete_support: Path) -> Path:
    target = tmp_path / "support"
    shutil.copytree(complete_support, target, copy_function=os.link)
    return target


def _nominal_rows() -> list[dict[str, str]]:
    with (PLAN_DIR / "trials.csv").open(newline="", encoding="utf-8") as stream:
        return [
            row
            for row in csv.DictReader(stream)
            if row["split"] == "development" and row["sensor_condition"] == "nominal"
        ]


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
    row = _nominal_rows()[0]
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
    row = _nominal_rows()[0]
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


def test_explicit_failed_prep_is_unresolved_not_unsupported(
    copied_support: Path, tmp_path: Path
) -> None:
    row = _nominal_rows()[1]
    (copied_support / _support_rel(row)).unlink()
    ledger = copied_support / "development" / "preparation.csv"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "trial_id",
        "split",
        "port_id",
        "placement_id",
        "realization",
        "range_m",
        "report_types",
        "physical_seed",
        "sensor_condition",
        "status",
    ]
    with ledger.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerow({**{key: row[key] for key in fields[:-1]}, "status": "failed"})

    snapshot = freeze_eligibility_manifest(
        PLAN_DIR, copied_support, tmp_path / "eligibility.json", "development"
    )
    unresolved = next(item for item in snapshot.nominal_trials if item.trial_id == row["trial_id"])
    assert unresolved.status == "unresolved"
    assert unresolved.supported is None
    assert snapshot.supported_denominator == 2699
