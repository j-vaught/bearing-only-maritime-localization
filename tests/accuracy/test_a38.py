"""A38 execution-helper archive, parity, merge and storage-guard tests."""

from __future__ import annotations

import csv
import gzip
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import a38_archive
from tools.a38_calibration_v2 import build_condition_document
from tools.build_a38_calibration_metrics import (
    build_document,
    build_trial_metrics,
    evaluator_error_metrics,
)
from tools.build_a38_calibration_metrics import (
    main as calibration_metrics_main,
)
from tools.compact_a38_audit import compact_audits
from tools.run_a38_execution import compact_truth, evaluator_parity
from tools.run_a38_parallel_prep import (
    _merge_worker,
    _trial_output_relative_path,
    _worker,
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


def test_compact_resume_checks_pinned_hashes_without_decompressing(
    tmp_path: Path, monkeypatch
) -> None:
    trial = tmp_path / "development/port-001/placement-001/r0/1000m/types-on/nominal"
    trial.mkdir(parents=True)
    truth = trial / "truth.json"
    truth.write_bytes(_truth_doc())
    audit = trial / "physical-identity-audit.json"
    audit.write_text(
        json.dumps(
            {
                "catalog_sha256": "catalog-hash",
                "physical_entity_by_catalog_key": {"chart:1": "physical:1"},
                "unresolved_catalog_keys": [],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (trial / "support.json").write_text(json.dumps({"supported": True}))
    truth_manifest = tmp_path / "truth-compaction.csv"
    audit_manifest = tmp_path / "identity-audit-compaction.csv"
    assert compact_truth(truth, truth_manifest, "development") is not None
    assert compact_audits(tmp_path, audit_manifest, "development") == 1

    def unexpected_decompression(*_args, **_kwargs):
        raise AssertionError("verified complete archives should use pinned-hash fast path")

    monkeypatch.setattr("tools.run_a38_execution.verified_gzip_bytes", unexpected_decompression)
    monkeypatch.setattr("tools.compact_a38_audit.verified_gzip_bytes", unexpected_decompression)
    from tools.run_a38_execution import compact_split

    assert compact_split(tmp_path, truth_manifest, "development") == 0
    assert compact_audits(tmp_path, audit_manifest, "development") == 0


def test_orphan_compact_truth_recovers_manifest_from_verified_archive(tmp_path: Path) -> None:
    path = tmp_path / "development/port-001/placement-001/r0/1000m/types-on/nominal/truth.json"
    path.parent.mkdir(parents=True)
    original = _truth_doc()
    path.write_bytes(original)
    manifest = tmp_path / "truth-compaction.csv"
    first = compact_truth(path, manifest, "development")
    assert first is not None
    compact_bytes = path.read_bytes()
    manifest.unlink()

    recovered = compact_truth(path, manifest, "development")

    assert recovered is not None
    assert path.read_bytes() == compact_bytes
    assert gzip.decompress(Path(recovered["full_truth_archive_path"]).read_bytes()) == original
    assert recovered["full_truth_sha256"] == a38_archive.sha256_bytes(original)


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
    with pytest.raises(RuntimeError, match="Pinned compact truth or archive hash mismatch"):
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


def test_audit_compaction_can_reconcile_only_missing_trial_ids(tmp_path: Path) -> None:
    selected = tmp_path / "development/port-001/placement-001/r0/1000m/types-on/nominal"
    ignored = tmp_path / "development/port-001/placement-002/r0/1000m/types-on/nominal"
    selected.mkdir(parents=True)
    ignored.mkdir(parents=True)
    full = {
        "catalog_sha256": "catalog-hash",
        "physical_entity_by_catalog_key": {"chart:1": "physical:1"},
        "unresolved_catalog_keys": [],
    }
    audit_path = selected / "physical-identity-audit.json"
    audit_path.write_text(json.dumps(full, indent=2, sort_keys=True) + "\n")
    (ignored / "physical-identity-audit.json").write_text("{incomplete")

    updated = compact_audits(
        tmp_path,
        tmp_path / "identity-audit-compaction.csv",
        "development",
        trial_ids={"development/port-001/placement-001/r0/range1000m/typeson/nominal"},
    )

    assert updated == 1
    rows = list(csv.DictReader((tmp_path / "identity-audit-compaction.csv").open(newline="")))
    assert [row["trial_id"] for row in rows] == [
        "development/port-001/placement-001/r0/range1000m/typeson/nominal"
    ]


def test_orphan_compact_audit_recovers_manifest_from_verified_archive(tmp_path: Path) -> None:
    trial = tmp_path / "development/port-001/placement-001/r0/1000m/types-on/nominal"
    trial.mkdir(parents=True)
    audit = trial / "physical-identity-audit.json"
    full = {
        "catalog_sha256": "catalog-hash",
        "physical_entity_by_catalog_key": {"chart:1": "physical:1"},
        "unresolved_catalog_keys": [],
    }
    original = (json.dumps(full, indent=2, sort_keys=True) + "\n").encode()
    audit.write_bytes(original)
    manifest = tmp_path / "identity-audit-compaction.csv"
    assert compact_audits(tmp_path, manifest, "development") == 1
    compact_bytes = audit.read_bytes()
    manifest.unlink()

    assert compact_audits(tmp_path, manifest, "development") == 1

    assert audit.read_bytes() == compact_bytes
    archive = audit.with_name("physical-identity-audit.full.json.gz")
    assert gzip.decompress(archive.read_bytes()) == original
    recovered = list(csv.DictReader(manifest.open(newline="")))
    assert len(recovered) == 1
    assert recovered[0]["gzip_roundtrip_sha256_verified"] == "True"


def test_parallel_row_validation_rejects_missing_and_duplicate_ids() -> None:
    validate_worker_ids(["a", "b"], ["b", "a"])
    with pytest.raises(RuntimeError, match="duplicate"):
        validate_worker_ids(["a", "b"], ["a", "a"])
    with pytest.raises(RuntimeError, match="missing"):
        validate_worker_ids(["a", "b"], ["a"])


@pytest.mark.parametrize("split", ["development", "validation"])
def test_parallel_merge_accounts_for_explicit_failures_without_outputs(
    tmp_path: Path, split: str
) -> None:
    worker = tmp_path / "worker"
    canonical = tmp_path / "canonical"
    prep = worker / split / "preparation.csv"
    prep.parent.mkdir(parents=True)
    trial_id = f"{split}/port-001/placement-001/r0/range1000m/typeson/map_buffer"
    with prep.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["trial_id", "status", "failure"])
        writer.writeheader()
        writer.writerow(
            {
                "trial_id": trial_id,
                "status": "failed",
                "failure": "Observer must lie in known water on the observation map.",
            }
        )

    merged = _merge_worker(
        worker,
        canonical,
        tmp_path / "truth.csv",
        tmp_path / "audit.csv",
        split,
    )

    assert merged == 1
    rows = list(csv.DictReader((canonical / split / "preparation.csv").open()))
    assert rows == [
        {
            "trial_id": trial_id,
            "status": "failed",
            "failure": "Observer must lie in known water on the observation map.",
        }
    ]
    assert not (canonical / split / "port-001/placement-001").exists()


def test_parallel_worker_uses_requested_validation_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import run_a38_parallel_prep

    observed: dict[str, object] = {}

    def fake_prepare_inputs(
        plan: Path,
        *,
        catalog_path: Path,
        map_cache: Path,
        input_root: Path,
        split: str,
        placements_per_port: int,
        only_trial_ids: set[str],
        _allow_extension: bool,
    ) -> None:
        observed["split"] = split
        assert split == "validation"
        assert only_trial_ids == {
            "validation/port-002/placement-015/r0/range5000m/typesoff/nominal"
        }
        prep = input_root / split / "preparation.csv"
        prep.parent.mkdir(parents=True)
        with prep.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["trial_id", "status"])
            writer.writeheader()
            writer.writerow({"trial_id": next(iter(only_trial_ids)), "status": "failed"})

    monkeypatch.setattr(run_a38_parallel_prep, "prepare_inputs", fake_prepare_inputs)
    monkeypatch.setattr(run_a38_parallel_prep, "compact_split", lambda *_args: 0)

    trial_id = "validation/port-002/placement-015/r0/range5000m/typesoff/nominal"
    result = _worker(
        (
            "plan.json",
            "catalog.json",
            "map.json",
            str(tmp_path / "worker"),
            [trial_id],
            0,
            [],
            "validation",
        )
    )

    assert observed["split"] == "validation"
    assert result["trial_ids"] == [trial_id]
    assert _trial_output_relative_path(trial_id, "validation") == (
        "validation/port-002/placement-015/r0/5000m/types-off/nominal"
    )


def test_trial_id_maps_to_canonical_preparation_directory() -> None:
    assert (
        _trial_output_relative_path(
            "development/port-010/placement-004/r0/range10000m/typesoff/miss"
        )
        == "development/port-010/placement-004/r0/10000m/types-off/miss"
    )
    assert (
        _trial_output_relative_path(
            "validation/port-002/placement-015/r0/range5000m/typesoff/nominal"
        )
        == "validation/port-002/placement-015/r0/5000m/types-off/nominal"
    )


def test_parallel_merge_rejects_completed_row_without_output(tmp_path: Path) -> None:
    worker = tmp_path / "worker"
    prep = worker / "development/preparation.csv"
    prep.parent.mkdir(parents=True)
    with prep.open("w", newline="") as stream:
        fields = [
            "trial_id",
            "status",
            "port_id",
            "placement_id",
            "realization",
            "range_m",
            "report_types",
            "sensor_condition",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerow(
            {
                "trial_id": "development/port-001/placement-001/r0/range1000m/typeson/nominal",
                "status": "completed",
                "port_id": "port-001",
                "placement_id": "placement-001",
                "realization": "0",
                "range_m": "1000",
                "report_types": "True",
                "sensor_condition": "nominal",
            }
        )
    with pytest.raises(RuntimeError, match="missing completed"):
        _merge_worker(
            worker, tmp_path / "canonical", tmp_path / "truth.csv", tmp_path / "audit.csv"
        )


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
        "certificate_scope": "finite_candidate_set",
        "bound_gap": None,
        "hypotheses": [
            {
                "east_m": 0.0,
                "north_m": 0.0,
                "score": 1.0,
                "assignments": [{"catalog_key": f"key-{i}"} for i in range(3)],
                "uncertainty": {"major_radius_m": 50.0},
            },
            {"east_m": 500.0, "north_m": 0.0, "score": 2.0, "assignments": []},
        ],
    }
    evidence = {
        "diagnostics": {
            "public_evidence": {
                "schema": "search-mode-evidence-v1",
                "scope_manifest_verified": True,
                "search_status": "completed",
                "mode_source": "refined_local_modes",
                "coverage_complete": False,
                "global_bound_excludes_outside": False,
                "unresolved_region_count": 0,
                "evaluated_candidate_count": 10,
                "certificate_scope": "finite_candidate_set",
                "bound_gap": None,
                "distant_region_score_lower_bound": None,
                "distant_region_bound_scope": None,
                "distant_region_bound_top_east_m": None,
                "distant_region_bound_top_north_m": None,
                "top_mode_converged": True,
                "candidate_outcome_counts_available": True,
                "unresolved_candidate_count": 0,
                "candidate_outcome_counts": {
                    "scored": 10,
                    "invalid_water": 0,
                    "unresolved_coverage": 0,
                    "unresolved_model": 0,
                },
                "top_mode_index": 0,
                "raw_distant_hypothesis_veto_evidence": {
                    "top_score": 1.0,
                    "score_tolerance": 1e-10,
                    "alternatives": [
                        {
                            "east_m": 500.0,
                            "north_m": 0.0,
                            "score": 2.0,
                            "score_gap": 1.0,
                            "distance_from_top_m": 500.0,
                        }
                    ],
                },
                "mode_evidence": [
                    {
                        "east_m": 0.0,
                        "north_m": 0.0,
                        "score": 1.0,
                        "converged": True,
                        "ellipse_rank": 2,
                        "radius_95_m": 50.0,
                        "matched_catalog_keys": ["key-0", "key-1", "key-2"],
                        "stop_reason": "relative_improvement_below_1e-8",
                    },
                    {
                        "east_m": 150.0,
                        "north_m": 0.0,
                        "score": 5.0,
                        "converged": True,
                        "ellipse_rank": 2,
                        "radius_95_m": 60.0,
                        "matched_catalog_keys": ["key-0", "key-1", "key-2"],
                        "stop_reason": "relative_improvement_below_1e-8",
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
    assert row["error_m"] is None
    assert row["error_source"] is None
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
    run_dir = run_root / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "run-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "run-1",
                "split": "development",
                "projected_crs": "EPSG:32618",
                "trials": [],
            }
        ),
        encoding="utf-8",
    )
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
            "--run-id",
            "run-1",
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


@pytest.mark.parametrize(
    ("available", "unresolved", "expected_status"),
    [
        (False, 0, "candidate_outcome_counts_unavailable"),
        (True, 1, "unresolved_candidate_geometry"),
        (True, None, "unresolved_candidate_count_malformed"),
    ],
)
def test_calibration_metrics_fail_closed_on_candidate_coverage(
    available: bool, unresolved: int | None, expected_status: str
) -> None:
    trial, estimate, telemetry = _metric_inputs()
    evidence = telemetry["diagnostics"]["public_evidence"]
    evidence["candidate_outcome_counts_available"] = available
    evidence["unresolved_candidate_count"] = unresolved if available else None
    if unresolved == 1:
        evidence["candidate_outcome_counts"]["unresolved_model"] = 1
    elif unresolved is None or not available:
        evidence["candidate_outcome_counts"] = None
    row = build_trial_metrics(
        trial,
        run_state={"status": "completed"},
        estimate=estimate,
        telemetry=telemetry,
        evaluation=None,
        truth=None,
        support={"supported": True},
    )
    assert row["evidence_status"] == expected_status
    assert row["meaningful_distant_coverage"] is False
    assert row["score_gap"] is None


def test_continuous_certificate_without_distant_margin_is_not_proof() -> None:
    trial, estimate, telemetry = _metric_inputs()
    estimate["certificate_scope"] = "continuous_objective"
    estimate["bound_gap"] = 0.0
    evidence = telemetry["diagnostics"]["public_evidence"]
    evidence["candidate_outcome_counts_available"] = False
    evidence["unresolved_candidate_count"] = None
    evidence["candidate_outcome_counts"] = None
    evidence["coverage_complete"] = True
    evidence["global_bound_excludes_outside"] = True
    evidence["certificate_scope"] = "continuous_objective"
    evidence["bound_gap"] = 0.0
    evidence["mode_evidence"] = []
    evidence["mode_source"] = "none"
    evidence["top_mode_converged"] = False
    evidence["top_mode_index"] = None
    row = build_trial_metrics(
        trial,
        run_state={"status": "completed"},
        estimate=estimate,
        telemetry=telemetry,
        evaluation=None,
        truth=None,
        support={"supported": True},
    )
    assert row["global_exclusion_verified"] is False
    assert row["distant_region_score_lower_bound"] is None
    assert row["evidence_status"] == "candidate_outcome_counts_unavailable"


def test_margin_evidence_is_bound_to_final_top_and_preserved_for_grid() -> None:
    trial, estimate, telemetry = _metric_inputs()
    estimate["certificate_scope"] = "continuous_objective"
    estimate["bound_gap"] = 0.0
    evidence = telemetry["diagnostics"]["public_evidence"]
    evidence["candidate_outcome_counts_available"] = False
    evidence["unresolved_candidate_count"] = None
    evidence["candidate_outcome_counts"] = None
    evidence["coverage_complete"] = True
    evidence["global_bound_excludes_outside"] = True
    evidence["certificate_scope"] = "continuous_objective"
    evidence["bound_gap"] = 0.0
    evidence["distant_region_score_lower_bound"] = 1.25
    evidence["distant_region_bound_scope"] = "all_objective_positions_at_least_100m_from_top"
    evidence["distant_region_bound_top_east_m"] = 0.0
    evidence["distant_region_bound_top_north_m"] = 0.0
    evidence["mode_evidence"] = []
    evidence["mode_source"] = "none"
    evidence["top_mode_converged"] = False
    evidence["top_mode_index"] = None
    row = build_trial_metrics(
        trial,
        run_state={"status": "completed"},
        estimate=estimate,
        telemetry=telemetry,
        evaluation=None,
        truth=None,
        support={"supported": True},
    )
    assert row["evidence_status"] == "distant_region_margin_evidence_available"
    assert row["global_exclusion_verified"] is False
    assert row["distant_region_score_lower_bound"] == 1.25
    assert row["distant_region_bound_top_east_m"] == 0.0
    assert row["distant_region_bound_top_north_m"] == 0.0


def test_margin_evidence_with_wrong_final_top_is_rejected() -> None:
    trial, estimate, telemetry = _metric_inputs()
    estimate["certificate_scope"] = "continuous_objective"
    estimate["bound_gap"] = 0.0
    evidence = telemetry["diagnostics"]["public_evidence"]
    evidence["candidate_outcome_counts_available"] = False
    evidence["unresolved_candidate_count"] = None
    evidence["candidate_outcome_counts"] = None
    evidence["coverage_complete"] = True
    evidence["global_bound_excludes_outside"] = True
    evidence["certificate_scope"] = "continuous_objective"
    evidence["bound_gap"] = 0.0
    evidence["distant_region_score_lower_bound"] = 1.25
    evidence["distant_region_bound_scope"] = "all_objective_positions_at_least_100m_from_top"
    evidence["distant_region_bound_top_east_m"] = 1.0
    evidence["distant_region_bound_top_north_m"] = 0.0
    evidence["mode_evidence"] = []
    evidence["mode_source"] = "none"
    evidence["top_mode_converged"] = False
    evidence["top_mode_index"] = None
    row = build_trial_metrics(
        trial,
        run_state={"status": "completed"},
        estimate=estimate,
        telemetry=telemetry,
        evaluation=None,
        truth=None,
        support={"supported": True},
    )
    assert row["evidence_status"] == "candidate_outcome_counts_unavailable"
    assert row["global_exclusion_verified"] is False


def test_evaluator_error_uses_hash_verified_same_crs_geodesic() -> None:
    truth = {"observer_east_m": 500000.0, "observer_north_m": 4500000.0}
    estimate = {
        "hypotheses": [{"east_m": 500100.0, "north_m": 4500000.0}],
    }
    provenance = {
        "schema_version": 1,
        "trial_id": "trial-1",
        "source_estimate_sha256": "source-estimate-hash",
        "source_truth_sha256": "source-truth-hash",
        "evaluator_estimate_sha256": "evaluator-estimate-hash",
        "estimate_source_crs": "EPSG:32618",
        "truth_coordinate_crs": "EPSG:32618",
        "estimate_target_crs": "EPSG:32618",
        "transform_method": "pyproj.Transformer.from_crs(always_xy=True)",
        "axis_order": "xy",
    }

    geodesic, euclidean, status = evaluator_error_metrics(
        "trial-1",
        estimate,
        truth,
        provenance,
        source_estimate_sha256="source-estimate-hash",
        source_truth_sha256="source-truth-hash",
        evaluator_estimate_sha256="evaluator-estimate-hash",
        projected_crs="EPSG:32618",
    )

    assert status == "verified_evaluator_coordinates_and_truth"
    assert geodesic == pytest.approx(99.96, abs=0.1)
    assert euclidean == pytest.approx(100.0)


def test_evaluator_error_rejects_unverified_crs_or_hash() -> None:
    provenance = {
        "schema_version": 1,
        "trial_id": "trial-1",
        "source_estimate_sha256": "wrong-hash",
        "source_truth_sha256": "source-truth-hash",
        "evaluator_estimate_sha256": "evaluator-estimate-hash",
        "estimate_source_crs": "EPSG:32618",
        "truth_coordinate_crs": "EPSG:32618",
        "estimate_target_crs": "EPSG:32618",
        "transform_method": "pyproj.Transformer.from_crs(always_xy=True)",
        "axis_order": "xy",
    }
    error, euclidean, status = evaluator_error_metrics(
        "trial-1",
        {"hypotheses": [{"east_m": 500100.0, "north_m": 4500000.0}]},
        {"observer_east_m": 500000.0, "observer_north_m": 4500000.0},
        provenance,
        source_estimate_sha256="source-estimate-hash",
        source_truth_sha256="source-truth-hash",
        evaluator_estimate_sha256="evaluator-estimate-hash",
        projected_crs="EPSG:32618",
    )
    assert error is None
    assert euclidean is None
    assert status == "coordinate_provenance_mismatch"


def test_raw_distant_tie_within_runtime_tolerance_fails_calibration_closed() -> None:
    trial, estimate, telemetry = _metric_inputs()
    estimate["hypotheses"][1]["score"] = 1.0
    veto = telemetry["diagnostics"]["public_evidence"]["raw_distant_hypothesis_veto_evidence"]
    veto["alternatives"][0]["score"] = 1.0
    veto["alternatives"][0]["score_gap"] = 0.0
    row = build_trial_metrics(
        trial,
        run_state={"status": "completed"},
        estimate=estimate,
        telemetry=telemetry,
        evaluation=None,
        truth=None,
        support={"supported": True},
    )
    assert row["meaningful_distant_coverage"] is True
    assert row["score_gap"] == 4.0
    assert row["raw_distant_hypothesis_veto_evidence"] == [
        {"score_gap": 0.0, "score_tolerance": 1e-10}
    ]
    assert row["evidence_status"] == "complete"


def test_manifest_builder_validates_hashes_and_uses_transformed_coordinates(
    tmp_path: Path,
) -> None:
    import hashlib

    from pyproj import CRS, Transformer

    input_root = tmp_path / "inputs"
    trial = {
        "split": "development",
        "trial_id": "development/port-001/placement-001/r0/1000m/types-on/nominal",
        "port_id": "port-001",
        "placement_id": "placement-001",
        "realization": "0",
        "range_m": "1000",
        "report_types": "True",
        "sensor_condition": "nominal",
    }
    trial_dir = input_root / "development/port-001/placement-001/r0/1000m/types-on/nominal"
    trial_dir.mkdir(parents=True)
    (trial_dir / "support.json").write_text('{"supported": true}\n', encoding="utf-8")

    source_crs = CRS.from_epsg(32618)
    truth_crs = CRS.from_epsg(32619)
    to_geo = Transformer.from_crs(truth_crs, truth_crs.geodetic_crs, always_xy=True)
    to_truth = Transformer.from_crs(truth_crs.geodetic_crs, truth_crs, always_xy=True)
    to_source = Transformer.from_crs(truth_crs, source_crs, always_xy=True)
    lon, lat = -73.0, 40.0
    truth_east, truth_north = to_truth.transform(lon, lat)
    truth = {"observer_east_m": truth_east, "observer_north_m": truth_north}
    (trial_dir / "truth.json").write_text(json.dumps(truth), encoding="utf-8")

    estimate_east_truth = truth_east + 100.0
    estimate_north_truth = truth_north
    estimate_lon, estimate_lat = to_geo.transform(estimate_east_truth, estimate_north_truth)
    estimate_east_source, estimate_north_source = to_source.transform(estimate_lon, estimate_lat)
    source_estimate = {
        "search_status": "completed",
        "certificate_scope": "finite_candidate_set",
        "bound_gap": None,
        "hypotheses": [
            {
                "east_m": estimate_east_source,
                "north_m": estimate_north_source,
                "score": 1.0,
                "assignments": [{"catalog_key": f"key-{i}"} for i in range(3)],
                "uncertainty": {"major_radius_m": 50.0},
            },
            {
                "east_m": estimate_east_source + 500.0,
                "north_m": estimate_north_source,
                "score": 2.0,
                "assignments": [],
            },
        ],
    }
    evaluator_transform = Transformer.from_crs(source_crs, truth_crs, always_xy=True)
    evaluator_estimate = {
        "search_status": "completed",
        "hypotheses": [
            {
                **source_estimate["hypotheses"][0],
                "east_m": estimate_east_truth,
                "north_m": estimate_north_truth,
            },
            {
                **source_estimate["hypotheses"][1],
                "east_m": evaluator_transform.transform(
                    source_estimate["hypotheses"][1]["east_m"],
                    source_estimate["hypotheses"][1]["north_m"],
                )[0],
                "north_m": evaluator_transform.transform(
                    source_estimate["hypotheses"][1]["east_m"],
                    source_estimate["hypotheses"][1]["north_m"],
                )[1],
            },
        ],
    }
    evidence = _metric_inputs()[2]["diagnostics"]["public_evidence"]
    evidence["mode_evidence"][0].update(
        {
            "east_m": estimate_east_source,
            "north_m": estimate_north_source,
        }
    )
    evidence["mode_evidence"][1].update(
        {
            "east_m": estimate_east_source + 150.0,
            "north_m": estimate_north_source,
        }
    )
    evidence["raw_distant_hypothesis_veto_evidence"].update(
        {
            "alternatives": [
                {
                    "east_m": estimate_east_source + 500.0,
                    "north_m": estimate_north_source,
                    "score": 2.0,
                    "score_gap": 1.0,
                    "distance_from_top_m": 500.0,
                }
            ]
        }
    )
    raw_root = tmp_path / "run/run-1"
    artifact_dir = raw_root / "artifacts/trial-1"
    artifact_dir.mkdir(parents=True)
    artifacts = {
        "estimate.json": source_estimate,
        "estimate.json.telemetry.json": {"diagnostics": {"public_evidence": evidence}},
        "evaluator-estimate.json": evaluator_estimate,
        "evaluation.json": {"acceptance_status": "ambiguous"},
    }
    hashes = {}
    for name, value in artifacts.items():
        path = artifact_dir / name
        path.write_text(json.dumps(value), encoding="utf-8")
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()

    provenance = {
        "schema_version": 1,
        "trial_id": trial["trial_id"],
        "source_estimate_sha256": hashes["estimate.json"],
        "source_truth_sha256": hashlib.sha256((trial_dir / "truth.json").read_bytes()).hexdigest(),
        "evaluator_estimate_sha256": hashes["evaluator-estimate.json"],
        "estimate_source_crs": source_crs.to_string(),
        "truth_coordinate_crs": truth_crs.to_string(),
        "estimate_target_crs": truth_crs.to_string(),
        "transform_method": "pyproj.Transformer.from_crs(always_xy=True)",
        "transformer_description": "fixture",
        "transformer_definition": "fixture",
        "transformer_accuracy_m": 0.0,
        "axis_order": "xy",
    }
    provenance_path = artifact_dir / "evaluation-coordinate-provenance.json"
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
    hashes["evaluation-coordinate-provenance.json"] = hashlib.sha256(
        provenance_path.read_bytes()
    ).hexdigest()

    manifest = {
        "schema_version": 1,
        "run_id": "run-1",
        "split": "development",
        "projected_crs": source_crs.to_string(),
        "trials": [
            {
                "trial_id": trial["trial_id"],
                "status": "completed",
                "estimate_path": "artifacts/trial-1/estimate.json",
                "estimate_sha256": hashes["estimate.json"],
                "evaluator_estimate_path": "artifacts/trial-1/evaluator-estimate.json",
                "evaluator_estimate_sha256": hashes["evaluator-estimate.json"],
                "evaluation_path": "artifacts/trial-1/evaluation.json",
                "evaluation_sha256": hashes["evaluation.json"],
                "coordinate_provenance_path": (
                    "artifacts/trial-1/evaluation-coordinate-provenance.json"
                ),
                "coordinate_provenance_sha256": hashes["evaluation-coordinate-provenance.json"],
                "truth_path": str(trial_dir / "truth.json"),
                "truth_sha256": provenance["source_truth_sha256"],
            }
        ],
    }
    manifest_dir = raw_root
    manifest_path = manifest_dir / "run-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    document = build_document([trial], manifest, manifest_path, input_root, "development")
    row = document["all_trials"][0]
    assert row["evidence_status"] == "complete"
    assert row["error_source"] == "verified_evaluator_coordinates_and_truth"
    assert row["error_m"] == pytest.approx(99.96, abs=0.1)
    assert row["euclidean_error_m"] == pytest.approx(100.0)
    assert document["distance_comparison"]["pairs_near_100m"]["pair_count"] == 1
    assert row["error_m"] != pytest.approx(
        math.hypot(
            source_estimate["hypotheses"][0]["east_m"] - truth_east,
            source_estimate["hypotheses"][0]["north_m"] - truth_north,
        ),
        abs=1.0,
    )


def _v2_fixture(tmp_path: Path):
    import hashlib

    from bearing_localization.search.calibration import (
        CalibrationContext,
        CalibrationOperatingCondition,
    )

    context = CalibrationContext(
        catalog_sha256="a" * 64,
        map_sha256="b" * 64,
        scope_manifest_sha256="c" * 64,
        search_execution_sha256="d" * 64,
        implementation_sha256="e" * 64,
    )
    condition = CalibrationOperatingCondition(
        range_m=1000.0,
        use_types=True,
        objective_version="bearing_set_map_v1",
        algorithm="hierarchy",
    )
    plans = []
    eligibility = []
    specs = [
        ("nominal", True, "supported", None),
        ("nominal", False, "unsupported", None),
        ("clutter", True, "stress_supported", None),
        ("map_buffer", None, "unresolved", "failed map placement"),
    ]
    for index, (profile, supported, status, reason) in enumerate(specs):
        trial_id = f"development/port-001/placement-{index + 1:03d}/r0/range1000m/typeson/{profile}"
        plans.append(
            {
                "trial_id": trial_id,
                "split": "development",
                "port_id": "port-001",
                "placement_id": f"placement-{index + 1:03d}",
                "realization": "0",
                "range_m": "1000",
                "report_types": "True",
                "physical_seed": str(100 + index),
                "sensor_condition": profile,
            }
        )
        eligibility.append(
            SimpleNamespace(
                trial_id=trial_id,
                split="development",
                port_id="port-001",
                placement_id=f"placement-{index + 1:03d}",
                realization="0",
                range_m=1000,
                report_types=True,
                physical_seed=100 + index,
                sensor_condition=profile,
                supported=supported,
                status=status,
                eligibility_status="unresolved"
                if supported is None
                else status.removeprefix("stress_")
                if status.startswith("stress_")
                else status,
                eligibility_reason=reason,
                preparation_status="failed" if supported is None else "completed",
                preparation_reason=reason,
                support_sha256=None,
            )
        )
    snapshot = SimpleNamespace(
        split="development",
        plan_sha256="f" * 64,
        config_sha256="1" * 64,
        splits_sha256="3" * 64,
        trial_table_sha256="2" * 64,
        all_trials=tuple(eligibility),
    )
    eligibility_manifest = tmp_path / "eligibility.json"
    eligibility_manifest.write_text('{"fixture": true}\n', encoding="utf-8")
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    run_manifest_path = run_dir / "run-manifest.json"
    condition_contexts = {}
    for range_m in (1000, 5000, 10000):
        for use_types in (True, False):
            gate_condition = CalibrationOperatingCondition(
                range_m=float(range_m),
                use_types=use_types,
                objective_version=condition.objective_version,
                algorithm=condition.algorithm,
            )
            key = f"{range_m}m_types{'on' if use_types else 'off'}"
            gate_context = (
                context
                if key == "1000m_typeson"
                else CalibrationContext(
                    catalog_sha256=context.catalog_sha256,
                    map_sha256=context.map_sha256,
                    scope_manifest_sha256=context.scope_manifest_sha256,
                    search_execution_sha256=hashlib.sha256(key.encode()).hexdigest(),
                    implementation_sha256=context.implementation_sha256,
                )
            )
            condition_contexts[key] = {
                "calibration_condition": gate_condition.as_dict(),
                "calibration_context": gate_context.as_dict(),
            }
    manifest = {
        "schema_version": 1,
        "run_id": "run-1",
        "split": "development",
        "plan_sha256": snapshot.config_sha256,
        "config_sha256": snapshot.config_sha256,
        "splits_sha256": snapshot.splits_sha256,
        "trial_table_sha256": snapshot.trial_table_sha256,
        "eligibility_plan_sha256": snapshot.plan_sha256,
        "eligibility_manifest_sha256": hashlib.sha256(
            eligibility_manifest.read_bytes()
        ).hexdigest(),
        "condition_contexts": condition_contexts,
        "execution_protocol": {
            "schema": "accuracy-execution-protocol-v1",
            "timeout_s": 1800.0,
            "timeout_scope": "per_command_subprocess",
            "deadline_clock": "monotonic_wall_clock_from_spawn",
            "timeout_status": "search_timeout",
            "termination_policy": {
                "start_new_session": True,
                "timeout_signal": "SIGTERM",
                "grace_s": 1.0,
                "escalation_signal": "SIGKILL",
                "reap_process_group": True,
            },
        },
        "trials": [],
    }
    identity_fields = (
        "plan_sha256",
        "eligibility_plan_sha256",
        "eligibility_manifest_sha256",
        "config_sha256",
        "splits_sha256",
        "trial_table_sha256",
        "condition_contexts",
        "execution_protocol",
    )
    identity = {key: manifest[key] for key in identity_fields}
    manifest["run_identity_sha256"] = hashlib.sha256(
        json.dumps(
            {"schema": "accuracy-formal-run-identity-v1", **identity},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    run_manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return plans, snapshot, eligibility_manifest, run_manifest_path, manifest, condition, context


def _v2_run_row(planned, eligibility, manifest):
    import hashlib

    range_m = int(planned["range_m"])
    report_types = planned["report_types"] == "True"
    key = f"{range_m}m_types{'on' if report_types else 'off'}"
    binding = manifest["condition_contexts"][key]

    def canonical(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

    return {
        "trial_id": planned["trial_id"],
        "split": planned["split"],
        "port_id": planned["port_id"],
        "placement_id": planned["placement_id"],
        "realization": planned["realization"],
        "range_m": range_m,
        "report_types": report_types,
        "physical_seed": int(planned["physical_seed"]),
        "sensor_condition": planned["sensor_condition"],
        "condition_context_key": key,
        "condition_context_sha256": hashlib.sha256(canonical(binding)).hexdigest(),
        "calibration_condition_sha256": hashlib.sha256(
            canonical(binding["calibration_condition"])
        ).hexdigest(),
        "calibration_context_sha256": hashlib.sha256(
            canonical(binding["calibration_context"])
        ).hexdigest(),
        "eligibility_record_sha256": hashlib.sha256(canonical(vars(eligibility))).hexdigest(),
        "execution_protocol_sha256": hashlib.sha256(
            canonical(manifest["execution_protocol"])
        ).hexdigest(),
        "supported": eligibility.supported,
        "support_sha256": eligibility.support_sha256 or "",
        "status": "failed",
        "failure": "controlled test row",
    }


def test_v2_condition_document_keeps_full_population_and_failure_rows(tmp_path: Path) -> None:
    import hashlib

    from tools.calibrate_accuracy_decision import _v2_split

    plans, snapshot, eligibility_path, run_path, manifest, condition, context = _v2_fixture(
        tmp_path
    )
    document = build_condition_document(
        trial_rows=plans,
        run_manifest=manifest,
        manifest_path=run_path,
        input_root=tmp_path / "inputs",
        split="development",
        eligibility_snapshot=snapshot,
        eligibility_manifest_path=eligibility_path,
        condition=condition,
        context=context,
    )
    assert document["schema_version"] == 2
    assert len(document["all_trials"]) == 4
    assert len(document["trials"]) == 2
    assert document["population"]["condition_wide_planned"]["count"] == 4
    assert document["population"]["nominal_planned"]["count"] == 2
    assert document["population"]["supported_nominal"]["count"] == 1
    assert document["population"]["unsupported_nominal"]["count"] == 1
    assert document["execution_protocol"] == manifest["execution_protocol"]
    assert (
        document["provenance"]["execution_protocol_sha256"]
        == hashlib.sha256(
            json.dumps(
                manifest["execution_protocol"], sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    )
    protocol_sha = document["provenance"]["execution_protocol_sha256"]
    assert all(row["execution_protocol_sha256"] == protocol_sha for row in document["all_trials"])
    missing_stress = document["all_trials"][-1]
    assert missing_stress["sensor_condition"] == "stress"
    assert missing_stress["sensor_profile"] == "map_buffer"
    assert missing_stress["supported"] is None
    assert missing_stress["preparation_status"] == "failed"
    assert missing_stress["evidence_status"] == "estimate_missing"
    included, _, _, _, population = _v2_split(document, "development", metrics_sha256="3" * 64)
    assert len(included) == 1
    assert population["excluded_stress"]["count"] == 2


def test_v2_condition_projection_accepts_other_conditions_in_split_manifest(
    tmp_path: Path,
) -> None:
    import hashlib

    plans, snapshot, eligibility_path, run_path, manifest, condition, context = _v2_fixture(
        tmp_path
    )
    other = dict(plans[0])
    other.update(
        {
            "trial_id": "development/port-001/placement-099/r0/range5000m/typeson/nominal",
            "placement_id": "placement-099",
            "range_m": "5000",
            "physical_seed": "909",
        }
    )
    plans.append(other)
    snapshot.all_trials = (
        *snapshot.all_trials,
        SimpleNamespace(
            trial_id=other["trial_id"],
            split="development",
            port_id="port-001",
            placement_id="placement-099",
            realization="0",
            range_m=5000,
            report_types=True,
            physical_seed=909,
            sensor_condition="nominal",
            supported=False,
            status="unsupported",
            eligibility_status="unsupported",
            eligibility_reason=None,
            preparation_status="completed",
            preparation_reason=None,
            support_sha256="9" * 64,
            support_count=2,
        ),
    )
    other_eligibility = snapshot.all_trials[-1]
    other_key = "5000m_typeson"
    other_binding = manifest["condition_contexts"][other_key]
    manifest["trials"] = [
        {
            "trial_id": other["trial_id"],
            "split": "development",
            "port_id": other["port_id"],
            "placement_id": other["placement_id"],
            "realization": "0",
            "range_m": 5000,
            "report_types": True,
            "physical_seed": 909,
            "sensor_condition": "nominal",
            "status": "failed",
            "failure": "controlled fixture",
            "condition_context_key": other_key,
            "condition_context_sha256": hashlib.sha256(
                json.dumps(other_binding, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "calibration_condition_sha256": hashlib.sha256(
                json.dumps(
                    other_binding["calibration_condition"],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "calibration_context_sha256": hashlib.sha256(
                json.dumps(
                    other_binding["calibration_context"],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "eligibility_record_sha256": hashlib.sha256(
                json.dumps(
                    vars(other_eligibility),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "execution_protocol_sha256": hashlib.sha256(
                json.dumps(
                    manifest["execution_protocol"],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "supported": other_eligibility.supported,
            "support_sha256": other_eligibility.support_sha256 or "",
        }
    ]
    document = build_condition_document(
        trial_rows=plans,
        run_manifest=manifest,
        manifest_path=run_path,
        input_root=tmp_path / "inputs",
        split="development",
        eligibility_snapshot=snapshot,
        eligibility_manifest_path=eligibility_path,
        condition=condition,
        context=context,
    )
    assert document["trial_count"] == 4
    assert other["trial_id"] not in {row["trial_id"] for row in document["all_trials"]}


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("supported", False, "supported flag mismatch"),
        ("supported", 1, "supported flag mismatch"),
        ("supported", None, "supported flag mismatch"),
        ("support_sha256", "0" * 64, "support hash mismatch"),
        ("execution_protocol_sha256", "0" * 64, "execution-protocol hash mismatch"),
    ],
)
def test_v2_run_rows_reject_support_provenance_mismatches(
    tmp_path: Path, field: str, value, message: str
) -> None:
    plans, snapshot, eligibility_path, run_path, manifest, condition, context = _v2_fixture(
        tmp_path
    )
    row = _v2_run_row(plans[0], snapshot.all_trials[0], manifest)
    row[field] = value
    manifest["trials"] = [row]
    with pytest.raises(ValueError, match=message):
        build_condition_document(
            trial_rows=plans,
            run_manifest=manifest,
            manifest_path=run_path,
            input_root=tmp_path / "inputs",
            split="development",
            eligibility_snapshot=snapshot,
            eligibility_manifest_path=eligibility_path,
            condition=condition,
            context=context,
        )


def test_v2_run_rows_require_explicit_supported_and_support_hash_fields(
    tmp_path: Path,
) -> None:
    plans, snapshot, eligibility_path, run_path, manifest, condition, context = _v2_fixture(
        tmp_path
    )
    row = _v2_run_row(plans[0], snapshot.all_trials[0], manifest)
    del row["supported"]
    manifest["trials"] = [row]
    with pytest.raises(ValueError, match="supported flag mismatch"):
        build_condition_document(
            trial_rows=plans,
            run_manifest=manifest,
            manifest_path=run_path,
            input_root=tmp_path / "inputs",
            split="development",
            eligibility_snapshot=snapshot,
            eligibility_manifest_path=eligibility_path,
            condition=condition,
            context=context,
        )

    row = _v2_run_row(plans[0], snapshot.all_trials[0], manifest)
    del row["support_sha256"]
    manifest["trials"] = [row]
    with pytest.raises(ValueError, match="support hash mismatch"):
        build_condition_document(
            trial_rows=plans,
            run_manifest=manifest,
            manifest_path=run_path,
            input_root=tmp_path / "inputs",
            split="development",
            eligibility_snapshot=snapshot,
            eligibility_manifest_path=eligibility_path,
            condition=condition,
            context=context,
        )


def test_v2_condition_document_rejects_truncated_eligibility_plan(
    tmp_path: Path,
) -> None:
    plans, snapshot, eligibility_path, run_path, manifest, condition, context = _v2_fixture(
        tmp_path
    )
    with pytest.raises(ValueError, match="exactly match"):
        build_condition_document(
            trial_rows=plans[:-1],
            run_manifest=manifest,
            manifest_path=run_path,
            input_root=tmp_path / "inputs",
            split="development",
            eligibility_snapshot=snapshot,
            eligibility_manifest_path=eligibility_path,
            condition=condition,
            context=context,
        )


def test_v2_condition_document_rejects_wrong_gate_context_binding(
    tmp_path: Path,
) -> None:
    import hashlib

    plans, snapshot, eligibility_path, run_path, manifest, condition, context = _v2_fixture(
        tmp_path
    )
    manifest["condition_contexts"]["1000m_typeson"]["calibration_context"][
        "search_execution_sha256"
    ] = "f" * 64
    identity_fields = (
        "plan_sha256",
        "eligibility_plan_sha256",
        "eligibility_manifest_sha256",
        "config_sha256",
        "splits_sha256",
        "trial_table_sha256",
        "condition_contexts",
    )
    identity = {key: manifest[key] for key in identity_fields}
    manifest["run_identity_sha256"] = hashlib.sha256(
        json.dumps(
            {"schema": "accuracy-formal-run-identity-v1", **identity},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    with pytest.raises(ValueError, match="pre-search binding"):
        build_condition_document(
            trial_rows=plans,
            run_manifest=manifest,
            manifest_path=run_path,
            input_root=tmp_path / "inputs",
            split="development",
            eligibility_snapshot=snapshot,
            eligibility_manifest_path=eligibility_path,
            condition=condition,
            context=context,
        )


def test_v2_condition_document_rejects_run_metadata_mismatch(tmp_path: Path) -> None:
    plans, snapshot, eligibility_path, run_path, manifest, condition, context = _v2_fixture(
        tmp_path
    )
    manifest["trials"] = [
        {
            "trial_id": plans[0]["trial_id"],
            "split": "development",
            "port_id": "port-999",
            "placement_id": plans[0]["placement_id"],
            "realization": "0",
            "range_m": 1000,
            "report_types": True,
            "physical_seed": 100,
            "sensor_condition": "nominal",
            "status": "failed",
        }
    ]
    with pytest.raises(ValueError, match="metadata differs from plan"):
        build_condition_document(
            trial_rows=plans,
            run_manifest=manifest,
            manifest_path=run_path,
            input_root=tmp_path / "inputs",
            split="development",
            eligibility_snapshot=snapshot,
            eligibility_manifest_path=eligibility_path,
            condition=condition,
            context=context,
        )


def test_v2_metrics_cli_writes_parseable_newline_terminated_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import accuracy_eligibility

    plans, snapshot, eligibility_path, run_path, manifest, condition, _context = _v2_fixture(
        tmp_path
    )
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    table_path = plan_dir / "trials.csv"
    with table_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(plans[0]))
        writer.writeheader()
        writer.writerows(plans)
    (plan_dir / "config.json").write_text(
        json.dumps({"trial_table": "trials.csv"}), encoding="utf-8"
    )
    run_root = tmp_path
    monkeypatch.setattr(
        accuracy_eligibility,
        "verify_eligibility_manifest",
        lambda *_args: snapshot,
    )
    output_path = tmp_path / "metrics.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_a38_calibration_metrics.py",
            "--plan",
            str(plan_dir / "config.json"),
            "--input-root",
            str(tmp_path / "inputs"),
            "--run-root",
            str(run_root),
            "--run-id",
            "run-1",
            "--split",
            "development",
            "--output",
            str(output_path),
            "--schema-version",
            "2",
            "--eligibility-manifest",
            str(eligibility_path),
            "--range-m",
            "1000",
            "--use-types",
            "--objective-version",
            condition.objective_version,
            "--algorithm",
            condition.algorithm,
        ],
    )
    calibration_metrics_main()
    raw = output_path.read_bytes()
    parsed = json.loads(raw)
    assert raw.endswith(b"\n")
    assert parsed["schema_version"] == 2
    assert parsed["operating_condition"] == condition.as_dict()
    assert parsed["trial_count"] == 4
    assert parsed["supported_trial_count"] == 2
    assert run_path.is_file()
