"""A38 execution-helper archive, parity, merge and storage-guard tests."""

from __future__ import annotations

import csv
import gzip
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import a38_archive
from tools.build_a38_calibration_metrics import (
    build_trial_metrics,
)
from tools.build_a38_calibration_metrics import (
    main as calibration_metrics_main,
)
from tools.compact_a38_audit import compact_audits
from tools.run_a38_execution import compact_truth, evaluator_parity
from tools.run_a38_parallel_prep import (
    _merge_worker,
    enforce_disk_guard,
    estimated_wave_gib,
    validate_worker_ids,
)


def _truth_doc(
    trial_id: str = "development/port-001/placement-001/r0/range1000m/typeson/nominal",
) -> bytes:
    return (
        json.dumps(
            {
                "contract_type": "EvaluationTruth",
                "observer_east_m": 1.0,
                "observer_north_m": 2.0,
                "observer_heading_deg": 3.0,
                "trial_id": trial_id,
                "objects": [
                    {"object_id": "observed", "object_type": "aid", "observation_index": 0},
                    {"object_id": "unobserved", "object_type": "aid", "observation_index": None},
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def test_truth_compaction_is_lossless_and_idempotent(tmp_path: Path) -> None:
    split_root = tmp_path / "development/port-001/placement-001/r0/1000m/types-on/nominal"
    split_root.mkdir(parents=True)
    truth_path = split_root / "truth.json"
    original = _truth_doc()
    truth_path.write_bytes(original)
    manifest = tmp_path / "truth-compaction.csv"

    first = compact_truth(truth_path, manifest, "development")
    assert first is not None
    assert gzip.decompress(Path(first["full_truth_archive_path"]).read_bytes()) == original
    assert Path(first["truth_path"]).read_bytes() != original
    assert compact_truth(truth_path, manifest, "development") is None
    rows = list(csv.DictReader(manifest.open(newline="")))
    assert len(rows) == 1
    assert rows[0]["gzip_roundtrip_sha256_verified"] == "True"


def test_interrupted_archive_write_keeps_final_archive_atomic(tmp_path: Path, monkeypatch) -> None:
    raw = _truth_doc()
    archive = tmp_path / "truth.json.gz"
    a38_archive.write_verified_gzip(raw, archive)
    good_archive = archive.read_bytes()

    def interrupted(*_args, **_kwargs):
        raise OSError("simulated interrupted gzip write")

    monkeypatch.setattr(a38_archive.gzip, "GzipFile", interrupted)
    with pytest.raises(OSError, match="simulated interrupted"):
        a38_archive.write_verified_gzip(b"new data", archive)
    assert archive.read_bytes() == good_archive
    assert gzip.decompress(archive.read_bytes()) == raw
    assert not list(tmp_path.glob(".*.tmp"))


def test_corrupt_final_archive_is_rebuilt_only_from_full_source(tmp_path: Path) -> None:
    path = tmp_path / "development/port-001/placement-001/r0/1000m/types-on/nominal/truth.json"
    path.parent.mkdir(parents=True)
    original = _truth_doc()
    path.write_bytes(original)
    archive = path.with_name("truth.full.json.gz")
    archive.write_bytes(b"partial gzip stream")
    archive.with_name(f".{archive.name}.interrupted.tmp").write_bytes(b"stale temporary bytes")

    row = compact_truth(path, tmp_path / "truth.csv", "development")
    assert row is not None
    assert gzip.decompress(archive.read_bytes()) == original
    assert row["full_truth_sha256"] == a38_archive.sha256_bytes(original)
    # A damaged archive cannot be accepted after the complete source has been compacted away.
    archive.write_bytes(b"corrupt")
    with pytest.raises(RuntimeError, match="no full source remains"):
        compact_truth(path, tmp_path / "truth.csv", "development")


def test_evaluator_parity_includes_multiple_members_per_observation_group() -> None:
    result = evaluator_parity()
    assert result["all_metrics_equal"] is True
    assert result["support_fields_preserved"] is True
    assert result["full_objects"] == 4
    assert result["compact_objects"] == 2
    assert result["cases"] == [
        "accepted_correct",
        "accepted_wrong",
        "ambiguous",
        "no_fix",
        "failed",
    ]


def test_audit_compaction_is_lossless_and_idempotent(tmp_path: Path) -> None:
    trial = tmp_path / "development/port-001/placement-001/r0/1000m/types-on/nominal"
    trial.mkdir(parents=True)
    audit = trial / "physical-identity-audit.json"
    full = {
        "catalog_sha256": "catalog-hash",
        "physical_entity_by_catalog_key": {"chart:1": "physical:1", "chart:2": "physical:2"},
        "unresolved_catalog_keys": ["chart:ambiguous"],
    }
    raw = (json.dumps(full, indent=2, sort_keys=True) + "\n").encode()
    audit.write_bytes(raw)
    manifest = tmp_path / "identity-audit-compaction.csv"

    assert compact_audits(tmp_path, manifest, "development") == 1
    archive = audit.with_name("physical-identity-audit.full.json.gz")
    assert gzip.decompress(archive.read_bytes()) == raw
    assert compact_audits(tmp_path, manifest, "development") == 0
    rows = list(csv.DictReader(manifest.open(newline="")))
    assert len(rows) == 1
    assert rows[0]["entity_map_entry_count"] == "2"
    assert rows[0]["unresolved_key_count"] == "1"


def test_parallel_row_validation_rejects_missing_and_duplicate_ids() -> None:
    validate_worker_ids(["a", "b"], ["b", "a"])
    with pytest.raises(RuntimeError, match="duplicate"):
        validate_worker_ids(["a", "b"], ["a", "a"])
    with pytest.raises(RuntimeError, match="missing"):
        validate_worker_ids(["a", "b"], ["a"])


def test_parallel_merge_refuses_duplicate_trial_directory(tmp_path: Path) -> None:
    source = tmp_path / "worker/development/port-001/placement-001/r0/1000m/types-on/nominal"
    destination = (
        tmp_path / "canonical/development/port-001/placement-001/r0/1000m/types-on/nominal"
    )
    source.mkdir(parents=True)
    destination.mkdir(parents=True)
    with pytest.raises(FileExistsError, match="Refusing to merge over existing"):
        _merge_worker(
            tmp_path / "worker",
            tmp_path / "canonical",
            tmp_path / "truth.csv",
            tmp_path / "audit.csv",
        )


def test_disk_guard_includes_reserve_plus_wave_growth() -> None:
    estimate = estimated_wave_gib(200)
    assert estimate > 0
    assert enforce_disk_guard(40 + estimate, 200) == pytest.approx(40 + estimate)
    with pytest.raises(RuntimeError, match="Disk guard requires"):
        enforce_disk_guard(40 + estimate - 0.01, 200)


def _metric_inputs():
    trial = {
        "split": "development",
        "trial_id": "development/port-001/placement-001/r0/range1000m/typeson/nominal",
        "port_id": "port-001",
        "placement_id": "placement-001",
        "realization": "0",
        "range_m": "1000",
        "report_types": "True",
        "sensor_condition": "nominal",
    }
    estimate = {
        "search_status": "completed",
        "hypotheses": [
            {
                "east_m": 0.0,
                "north_m": 0.0,
                "score": 1.0,
                "assignments": [{"catalog_key": f"key-{i}"} for i in range(3)],
            },
            {"east_m": 500.0, "north_m": 0.0, "score": 2.0, "assignments": []},
        ],
    }
    evidence = {
        "diagnostics": {
            "public_evidence": {
                "schema": "search-mode-evidence-v1",
                "search_status": "completed",
                "top_mode_converged": True,
                "mode_evidence": [
                    {
                        "east_m": 0.0,
                        "north_m": 0.0,
                        "score": 1.0,
                        "converged": True,
                        "ellipse_rank": 2,
                        "radius_95_m": 50.0,
                        "matched_catalog_keys": ["key-0", "key-1", "key-2"],
                        "stop_reason": "gradient_tolerance",
                    },
                    {
                        "east_m": 150.0,
                        "north_m": 0.0,
                        "score": 5.0,
                        "converged": True,
                        "ellipse_rank": 2,
                        "radius_95_m": 60.0,
                        "matched_catalog_keys": ["key-0", "key-1", "key-2"],
                        "stop_reason": "gradient_tolerance",
                    },
                ],
            }
        }
    }
    return trial, estimate, evidence


def test_calibration_metrics_use_converged_modes_and_separate_truth() -> None:
    trial, estimate, telemetry = _metric_inputs()
    row = build_trial_metrics(
        trial,
        run_state={"status": "completed"},
        estimate=estimate,
        telemetry=telemetry,
        evaluation={"acceptance_status": "ambiguous", "position_error_m": None},
        truth={"observer_east_m": 3.0, "observer_north_m": 4.0},
        support={"supported": True},
    )
    assert row["distinct_matched_groups"] == 3
    assert row["ellipse_rank"] == 2
    assert row["radius_95_m"] == 50.0
    assert row["meaningful_distant_coverage"] is True
    assert row["score_gap"] == 4.0
    assert row["error_m"] == 5.0
    assert row["error_source"] == "evaluator_truth_pose_vs_top_ranked_hypothesis"
    assert row["evidence_status"] == "complete"


def test_raw_distant_hypothesis_is_not_positive_coverage_evidence() -> None:
    trial, estimate, telemetry = _metric_inputs()
    evidence = telemetry["diagnostics"]["public_evidence"]
    evidence["mode_evidence"] = evidence["mode_evidence"][:1]
    row = build_trial_metrics(
        trial,
        run_state={"status": "completed"},
        estimate=estimate,
        telemetry=telemetry,
        evaluation=None,
        truth={"observer_east_m": 0.0, "observer_north_m": 0.0},
        support={"supported": True},
    )
    assert row["meaningful_distant_coverage"] is False
    assert row["score_gap"] is None


def test_missing_or_failed_trial_is_preserved_as_ineligible() -> None:
    trial, _, _ = _metric_inputs()
    row = build_trial_metrics(
        trial,
        run_state={"status": "search_timeout", "failure": "search_command_timeout"},
        estimate=None,
        telemetry=None,
        evaluation=None,
        truth=None,
        support={"supported": True},
    )
    assert row["run_status"] == "search_timeout"
    assert row["search_status"] == "failed"
    assert row["error_m"] is None
    assert row["supported"] is True
    assert row["evidence_status"] == "estimate_missing"


def test_calibration_metrics_cli_writes_parseable_json_with_newline(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps({"trial_table": "trials.csv", "trial_table_sha256": "fixture"}),
        encoding="utf-8",
    )
    (tmp_path / "trials.csv").write_text("split,trial_id\n", encoding="utf-8")
    input_root = tmp_path / "inputs"
    run_root = tmp_path / "run"
    run_root.mkdir()
    output = tmp_path / "metrics.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "build_a38_calibration_metrics.py",
            "--plan",
            str(plan_path),
            "--input-root",
            str(input_root),
            "--run-root",
            str(run_root),
            "--split",
            "development",
            "--output",
            str(output),
        ],
    )

    calibration_metrics_main()

    raw = output.read_bytes()
    decoded = json.loads(raw)
    assert decoded["split"] == "development"
    assert decoded["trial_count"] == 0
    assert raw.endswith(b"\n")
    assert not raw.endswith(b"\\n")
    assert json.loads(capsys.readouterr().out)["trial_count"] == 0
