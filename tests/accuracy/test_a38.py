"""A38 execution-helper archive, parity, merge and storage-guard tests."""

from __future__ import annotations

import csv
import gzip
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import a38_archive
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
