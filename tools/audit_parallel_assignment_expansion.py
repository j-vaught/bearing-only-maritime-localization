"""Composite evaluator-only audit for the interrupted parallel assignment-expansion run."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, cast

from tools import evaluate_parallel_assignment_expansion as reviewer
from tools.evaluate_a20_cached_refinement import load_refinement
from tools.evaluate_assigned_refinement import load_assigned
from tools.evaluate_assignment_expansion import (
    _same_hypothesis,
    _same_score,
    evaluate_exact_position,
    evaluate_rows,
    require,
)
from tools.evaluate_cached_finite_domain import decode_hypothesis, load_run, sha256_file
from tools.evaluate_parallel_assignment_expansion import (
    CHECKPOINT_NAME,
    EVENTS_NAME,
    ROWS_NAME,
    compare_bindings,
    event_index,
    read_json,
    read_jsonl,
    strip_timing,
    validate_manifest,
    verify_checkpoint_prefix,
    verify_event_chain,
    verify_immutable_sources,
)

TRIAL_ID = "development/port-001/placement-001/r0/range10000m/typesoff/nominal"
EXPECTED_PARALLEL_COMPLETED = [*range(48), *range(49, 64)]


def _raw_event_map(events: list[dict[str, Any]]) -> dict[tuple[int, str, int], dict[str, Any]]:
    indexed = event_index(events)
    return {key: value for key, value in indexed.items()}


def _event_records_by_mode(events: list[dict[str, Any]], mode: int) -> list[dict[str, Any]]:
    return [item for item in events if item.get("mode_index") == mode]


def _same_branch_payload(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ignored = {"mode_index", "phase"}
    return {key: value for key, value in a.items() if key not in ignored} == {
        key: value for key, value in b.items() if key not in ignored
    }


def _normalized_parallel_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    coordinator_raw = manifest.get("coordinator_loaded_package")
    require(type(coordinator_raw) is dict, "parallel coordinator package provenance is missing")
    coordinator = cast(dict[str, Any], coordinator_raw)
    normalized = dict(manifest)
    for key in (
        "assignment_expansion_sha256",
        "assignment_expansion_source",
        "package_source",
        "package_commit",
    ):
        normalized[key] = coordinator.get(key)
    normalized["harness_commit"] = manifest.get("harness_commit")
    normalized["runner_sha256"] = manifest.get("runner_sha256")
    normalized["worker_provenance"] = manifest.get("worker_provenance")
    return normalized


def validate_serial_audit(
    audit: dict[str, Any],
    serial_root: Path,
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> set[int]:
    require(
        audit.get("schema") == "a35-assignment-expansion-evaluation-v1",
        "unexpected serial audit schema",
    )
    require(
        audit.get("evaluation_status") == "complete_audit"
        and audit.get("branch_rescore_complete") is True,
        "serial exact audit is incomplete",
    )
    require(
        audit.get("evaluation_only") is True
        and audit.get("truth_used_for_search_or_proposal_selection") is False,
        "serial audit crossed evaluator boundary",
    )
    require(
        audit.get("certificate_scope") == "none" and audit.get("full_domain_complete") is False,
        "serial audit claims a certificate",
    )
    require(
        (
            audit.get("mode_count"),
            audit.get("completed_mode_count"),
            audit.get("pending_mode_count"),
        )
        == (64, 60, 4),
        "serial audit population is not 60+4",
    )
    require(
        audit.get("expansion_status") == "interrupted", "serial audit does not bind interrupted run"
    )
    require(
        audit.get("expansion_manifest_sha256") == sha256_file(serial_root / "manifest.json"),
        "serial manifest hash mismatch",
    )
    require(
        audit.get("expansion_rows_sha256") == manifest.get("rows_sha256"),
        "serial rows hash mismatch",
    )
    require(
        audit.get("expansion_events_sha256") == manifest.get("events_sha256"),
        "serial events hash mismatch",
    )
    require(
        audit.get("expansion_event_count") == len(events) == manifest.get("event_count"),
        "serial event count mismatch",
    )
    require(
        audit.get("expansion_event_digest") == manifest.get("event_digest"),
        "serial digest mismatch",
    )
    require(
        [item.get("mode_index") for item in audit.get("mode_evaluations", [])] == list(range(60)),
        "serial audit does not cover all 60 completed rows",
    )
    event_counts = audit.get("event_mode_counts")
    expected_counts = {
        str(mode): {
            "started": sum(
                1
                for event in events
                if event.get("mode_index") == mode and event.get("phase") == "branch_started"
            ),
            "result": sum(
                1
                for event in events
                if event.get("mode_index") == mode and event.get("phase") == "branch_result"
            ),
            "failure": sum(
                1
                for event in events
                if event.get("mode_index") == mode and event.get("phase") == "failure"
            ),
        }
        for mode in range(61)
    }
    require(
        event_counts == expected_counts, "serial event inventory differs from exact audit binding"
    )
    # This explicit set is the only coverage later reused for parallel overlap payloads.
    return {int(item["mode_index"]) for item in audit["mode_evaluations"]}


def validate_serial_audit_sources(
    audit: dict[str, Any],
    *,
    run_identity_sha256: str,
    assigned_manifest_sha256: str,
    assigned_rows_sha256: str,
    a20_manifest_sha256: str,
    a20_rows_sha256: str,
) -> None:
    expected = {
        "source_run_identity_sha256": run_identity_sha256,
        "assigned_manifest_sha256": assigned_manifest_sha256,
        "assigned_rows_sha256": assigned_rows_sha256,
        "a20_manifest_sha256": a20_manifest_sha256,
        "a20_rows_sha256": a20_rows_sha256,
    }
    for key, value in expected.items():
        require(audit.get(key) == value, f"serial audit source binding mismatch for {key}")


def _validate_parallel_partial(
    root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    manifest = read_json(root / "manifest.json")
    require(
        manifest.get("schema") == "parallel-assignment-expansion-diagnostic-v1",
        "unexpected parallel run schema",
    )
    require(
        manifest.get("status") == "failed",
        "parallel producer is not the frozen disk-guard interruption",
    )
    require(manifest.get("mode_count") == 64, "parallel mode cap is not 64")
    require(
        manifest.get("completed_mode_indices") == EXPECTED_PARALLEL_COMPLETED,
        "parallel completed mode inventory differs from disk-stop record",
    )
    require(
        manifest.get("unknown_mode_indices") == [48] and manifest.get("failed_mode_indices") == [],
        "parallel pending/failed mode inventory differs",
    )
    require(
        manifest.get("completed_mode_count") == 63
        and manifest.get("unknown_mode_count") == 1
        and manifest.get("failed_mode_count") == 0,
        "parallel completion counters disagree",
    )
    require(
        manifest.get("truth_used") is False and manifest.get("evaluator_used") is False,
        "parallel producer crossed evaluator boundary",
    )
    require(
        manifest.get("certificate_scope") == "none"
        and manifest.get("full_domain_complete") is False,
        "parallel producer claims a certificate",
    )
    require(
        manifest.get("resume_supported") is False,
        "parallel producer unexpectedly claims resume support",
    )
    coordinator_raw = manifest.get("coordinator_loaded_package")
    require(type(coordinator_raw) is dict, "parallel coordinator provenance missing")
    coordinator = cast(dict[str, Any], coordinator_raw)
    normalized = _normalized_parallel_manifest(manifest)
    verify_immutable_sources(normalized, "parallel coordinator")
    worker_raw = manifest.get("worker_provenance")
    require(
        type(worker_raw) is list and len(worker_raw) == 4,
        "four worker provenance records required",
    )
    worker_provenance = cast(list[dict[str, Any]], worker_raw)
    require(
        [item.get("worker_id") for item in worker_provenance] == [0, 1, 2, 3],
        "worker IDs are not canonical",
    )
    for item in worker_provenance:
        require(
            item.get("package_commit") == coordinator.get("package_commit"),
            "worker commit differs from coordinator",
        )
        require(
            item.get("assignment_expansion_sha256")
            == coordinator.get("assignment_expansion_sha256"),
            "worker algorithm source differs",
        )
        scorer_path = Path(str(item.get("scorer_source", "")))
        require(
            scorer_path.is_file() and sha256_file(scorer_path) == item.get("scorer_sha256"),
            "worker scorer source hash mismatch",
        )
    rows_path = root / ROWS_NAME
    events_path = root / EVENTS_NAME
    checkpoint_path = root / CHECKPOINT_NAME
    require(
        manifest.get("rows_file") == ROWS_NAME
        and sha256_file(rows_path) == manifest.get("rows_sha256"),
        "parallel rows file/hash mismatch",
    )
    require(manifest.get("events_file") == EVENTS_NAME, "parallel event filename mismatch")
    require(
        manifest.get("checkpoint_file") == CHECKPOINT_NAME, "parallel checkpoint filename mismatch"
    )
    rows = read_jsonl(rows_path)
    row_ids = [row.get("mode_index") for row in rows]
    require(
        row_ids == EXPECTED_PARALLEL_COMPLETED,
        "parallel durable rows are not the exact 63-mode set",
    )
    events = verify_event_chain(events_path, manifest)
    checkpoint = read_json(checkpoint_path)
    verify_checkpoint_prefix(root, checkpoint, events)
    require(
        checkpoint.get("rows_sha256") == manifest.get("rows_sha256"),
        "parallel checkpoint row hash mismatch",
    )
    require(
        checkpoint.get("completed_mode_indices") == EXPECTED_PARALLEL_COMPLETED,
        "parallel checkpoint mode inventory mismatch",
    )
    require(
        checkpoint.get("unknown_mode_indices") == [48]
        and checkpoint.get("failed_mode_indices") == [],
        "parallel checkpoint dispositions mismatch",
    )
    require(checkpoint.get("status") == "failed", "parallel checkpoint status differs")
    return manifest, rows, events, normalized


def _validate_event_inventory(
    rows_by_id: dict[int, dict[str, Any]], events: list[dict[str, Any]], *, pending_mode: int | None
) -> None:
    indexed = _raw_event_map(events)
    present_modes = {key[0] for key in indexed}
    allowed = set(rows_by_id)
    if pending_mode is not None:
        allowed.add(pending_mode)
    require(present_modes.issubset(allowed), "branch events refer to unknown mode")
    for mode, row in rows_by_id.items():
        started = {key[2] for key in indexed if key[0] == mode and key[1] == "branch_started"}
        result = {key[2] for key in indexed if key[0] == mode and key[1] == "branch_result"}
        require(
            started == result and started == set(range(len(started))),
            f"completed mode {mode} has incomplete event pairs",
        )
        require(
            row.get("branch_event_count") == len(result), f"mode {mode} row branch count mismatch"
        )
        counters_raw = row.get("counters")
        require(type(counters_raw) is dict, f"mode {mode} counters are missing")
        counters = cast(dict[str, Any], counters_raw)
        require(
            counters.get("visited_branches", 0) == len(result),
            f"mode {mode} visited counter mismatch",
        )
    if pending_mode is not None:
        starts = {
            key[2] for key in indexed if key[0] == pending_mode and key[1] == "branch_started"
        }
        results = {
            key[2] for key in indexed if key[0] == pending_mode and key[1] == "branch_result"
        }
        require(results.issubset(starts), "pending mode has a result without a start")
        require(
            starts == set(range(len(starts))) and results == set(range(len(results))),
            "pending mode branch IDs are not contiguous prefixes",
        )
        require(
            len(starts) - len(results) <= 1, "pending mode has multiple unfinished branch calls"
        )


def _strip_row_timing_and_parallel_fields(row: dict[str, Any]) -> dict[str, Any]:
    normalized = strip_timing(row, top_level=True)
    normalized.pop("candidate_branch_count", None)
    return normalized


def _compare_overlap(
    serial_rows: list[dict[str, Any]],
    serial_events: list[dict[str, Any]],
    parallel_rows: list[dict[str, Any]],
    parallel_events: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[tuple[int, str, int], dict[str, Any]]]:
    serial_by_id = {row["mode_index"]: row for row in serial_rows}
    parallel_by_id = {row["mode_index"]: row for row in parallel_rows}
    for mode, row in parallel_by_id.items():
        if mode == 48:
            continue
        require(mode in serial_by_id or mode >= 60, f"unexpected overlap row {mode}")
        if mode < 60:
            counters = row.get("counters")
            require(type(counters) is dict, f"parallel mode {mode} counters missing")
            counter_values = cast(dict[str, Any], counters)
            expected_branches = counter_values.get("candidate_branches", 0)
            require(
                row.get("candidate_branch_count") == expected_branches,
                f"parallel mode {mode} candidate branch count differs from counters",
            )
            require(
                _strip_row_timing_and_parallel_fields(row)
                == _strip_row_timing_and_parallel_fields(serial_by_id[mode]),
                f"serial/parallel row differs for mode {mode}",
            )
    serial_index, parallel_index = _raw_event_map(serial_events), _raw_event_map(parallel_events)
    for mode in [*range(48), *range(49, 60)]:
        s_keys = {key for key in serial_index if key[0] == mode}
        p_keys = {key for key in parallel_index if key[0] == mode}
        require(s_keys == p_keys, f"serial/parallel branch inventory differs for mode {mode}")
        for key in s_keys:
            require(
                _same_branch_payload(serial_index[key], parallel_index[key]),
                f"serial/parallel payload differs at {key}",
            )
    # Parallel mode 48 must match a prefix of serial mode 48.
    # The completed serial row supplies the composite result.
    s48 = {key: value for key, value in serial_index.items() if key[0] == 48}
    p48 = {key: value for key, value in parallel_index.items() if key[0] == 48}
    require(
        bool(p48) and set(p48).issubset(s48),
        "parallel mode 48 partial events are not a serial prefix",
    )
    for key, value in p48.items():
        require(_same_branch_payload(value, s48[key]), f"mode 48 partial payload differs at {key}")
    # Serial mode 60 is a prefix of parallel mode 60.
    # Its partial events are checked, then all parallel events are replayed.
    s60 = {key: value for key, value in serial_index.items() if key[0] == 60}
    p60 = {key: value for key, value in parallel_index.items() if key[0] == 60}
    require(
        bool(s60) and set(s60).issubset(p60),
        "serial mode 60 partial events are not a parallel prefix",
    )
    for key, value in s60.items():
        require(_same_branch_payload(value, p60[key]), f"mode 60 partial payload differs at {key}")
    return serial_by_id, serial_index


def _compose_selected_modes(
    serial_rows: list[dict[str, Any]],
    serial_events: list[dict[str, Any]],
    parallel_rows: list[dict[str, Any]],
    parallel_events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[int, str], dict[int, list[dict[str, Any]]]]:
    serial_by_id = {row["mode_index"]: row for row in serial_rows}
    parallel_by_id = {row["mode_index"]: row for row in parallel_rows}
    require(set(serial_by_id) == set(range(60)), "serial source rows must cover modes 0 through 59")
    require(
        set(parallel_by_id) == set(EXPECTED_PARALLEL_COMPLETED),
        "parallel rows differ from expected 63-mode inventory",
    )
    selected_rows: list[dict[str, Any]] = []
    sources: dict[int, str] = {}
    selected_events: dict[int, list[dict[str, Any]]] = {}
    for mode in range(64):
        if mode in parallel_by_id:
            selected_rows.append(dict(parallel_by_id[mode]))
            sources[mode] = "parallel"
            selected_events[mode] = _event_records_by_mode(parallel_events, mode)
        else:
            require(
                mode == 48 and mode in serial_by_id, f"mode {mode} has no complete selected source"
            )
            selected_rows.append(dict(serial_by_id[mode]))
            sources[mode] = "serial"
            selected_events[mode] = _event_records_by_mode(serial_events, mode)
        selected_rows[-1]["_source_run"] = sources[mode]
    return selected_rows, sources, selected_events


def _exact_rows(
    composite: list[dict[str, Any]],
    assigned_rows: list[dict[str, Any]],
    a20_rows: list[dict[str, Any]],
    scorer: Any,
    cache: dict[tuple[float, float], Any],
) -> list[dict[str, Any]]:
    checked = []
    for mode, (row, assigned, a20) in enumerate(
        zip(composite, assigned_rows, a20_rows, strict=True)
    ):
        require(row.get("mode_index") == mode, "composite rows are not ordered 0 through 63")
        require(
            row.get("input_index") == assigned.get("input_index") == a20.get("input_index"),
            f"mode {mode} source input index mismatch",
        )
        require(
            row.get("candidate_id") == assigned.get("candidate_id") == a20.get("candidate_id"),
            f"mode {mode} source candidate mismatch",
        )
        alternatives_raw = row.get("start_alternatives")
        starts_raw = assigned.get("starts")
        require(
            type(alternatives_raw) is list
            and len(alternatives_raw) == 2
            and type(starts_raw) is list
            and len(starts_raw) == 2,
            f"mode {mode} lacks both assigned starts",
        )
        alternatives = cast(list[dict[str, Any]], alternatives_raw)
        starts = cast(list[dict[str, Any]], starts_raw)
        start_by_label = {item.get("label"): item for item in starts}
        exact_starts = []
        for alt in alternatives:
            label = alt.get("label")
            require(label in start_by_label, f"mode {mode} has an unknown start label")
            saved = decode_hypothesis(alt.get("saved_hypothesis"))
            canonical = decode_hypothesis(alt.get("canonical_hypothesis"))
            source_final = decode_hypothesis(start_by_label[label].get("final_hypothesis"))
            require(
                _same_hypothesis(saved, source_final),
                f"mode {mode} saved start differs from source",
            )
            score = evaluate_exact_position(scorer, cache, (canonical.east_m, canonical.north_m))
            require(
                score.status == "scored" and score.hypothesis is not None,
                f"mode {mode} start cannot be scored",
            )
            require(
                _same_hypothesis(canonical, score.hypothesis)
                and _same_hypothesis(saved, score.hypothesis),
                f"mode {mode} start differs from exact scorer",
            )
            exact_starts.append((label, score.hypothesis))
        selected = min(exact_starts, key=lambda item: (item[1].score, item[0]))
        require(
            row.get("selected_start_label") == selected[0],
            f"mode {mode} selected a nonminimum start",
        )
        seed = decode_hypothesis(row.get("seed"))
        require(
            _same_hypothesis(seed, selected[1]), f"mode {mode} seed differs from selected start"
        )
        best = decode_hypothesis(row.get("best_hypothesis"))
        exact_best = evaluate_exact_position(scorer, cache, (best.east_m, best.north_m))
        require(
            exact_best.status == "scored" and exact_best.hypothesis is not None,
            f"mode {mode} best cannot be scored",
        )
        require(
            _same_hypothesis(best, exact_best.hypothesis), f"mode {mode} best hypothesis differs"
        )
        require(best.score <= seed.score, f"mode {mode} best score exceeds seed")
        checked.append(
            {
                "mode_index": mode,
                "candidate_id": row["candidate_id"],
                "seed": seed,
                "best": best,
                "selected_start_label": selected[0],
            }
        )
    return checked


def _replay_composite_events(
    rows: list[dict[str, Any]],
    events_by_mode: dict[int, list[dict[str, Any]]],
    scorer: Any,
    cache: dict[tuple[float, float], Any],
    *,
    validated_serial_audit_modes: set[int],
) -> dict[str, int]:
    counts = {
        "serial_audited_branch_results_reused": 0,
        "parallel_new_branch_results_rescored": 0,
        "nonexact_branch_results_preserved": 0,
        "invalid_water_results_structurally_retained": 0,
        "max_nfev_results_structurally_retained": 0,
    }
    for mode, row in enumerate(rows):
        events = events_by_mode[mode]
        seed = decode_hypothesis(row["seed"])
        best_score = seed.score
        winning_branch: dict[str, Any] | None = None
        for record in events:
            if record.get("phase") != "branch_result":
                continue
            branch = record["event"]
            exact_score = branch.get("exact_score")
            if exact_score is None:
                status = branch.get("status")
                require(
                    status in {"invalid_water", "max_nfev"},
                    f"mode {mode} scoreless result has unsupported status",
                )
                counts["nonexact_branch_results_preserved"] += 1
                counts[f"{status}_results_structurally_retained"] += 1
                continue
            require(
                type(exact_score) in (int, float) and math.isfinite(float(exact_score)),
                f"mode {mode} exact score invalid",
            )
            require(
                branch.get("status") in {"improved", "no_improvement"},
                f"mode {mode} exact result status invalid",
            )
            pose = branch.get("proposed_pose")
            require(
                type(pose) is list
                and len(pose) == 2
                and all(type(v) in (int, float) and math.isfinite(float(v)) for v in pose),
                f"mode {mode} branch pose invalid",
            )
            if mode in validated_serial_audit_modes:
                counts["serial_audited_branch_results_reused"] += 1
            else:
                result = evaluate_exact_position(scorer, cache, (float(pose[0]), float(pose[1])))
                require(
                    result.status == "scored" and result.hypothesis is not None,
                    f"mode {mode} parallel-only pose cannot be scored",
                )
                require(
                    _same_score(float(exact_score), result.hypothesis.score),
                    f"mode {mode} parallel-only branch score differs",
                )
                retained = any(
                    item.report_index == branch.get("report_index")
                    and item.catalog_key == branch.get("catalog_key")
                    for item in result.hypothesis.assignments
                )
                require(
                    branch.get("added_assignment_retained") is retained,
                    f"mode {mode} branch assignment retention differs",
                )
                counts["parallel_new_branch_results_rescored"] += 1
            score = float(exact_score)
            if branch["status"] == "improved":
                require(score < best_score, f"mode {mode} improvement is not strict")
                best_score = score
                winning_branch = branch
            else:
                require(score >= best_score, f"mode {mode} no-improvement beats current best")
        final_best = decode_hypothesis(row["best_hypothesis"])
        require(
            _same_score(best_score, final_best.score),
            f"mode {mode} final score differs from branch sequence",
        )
        if winning_branch is None:
            require(
                _same_hypothesis(seed, final_best),
                f"mode {mode} final best changed without a strict improvement",
            )
        else:
            winning_pose = winning_branch["proposed_pose"]
            require(
                float(winning_pose[0]) == final_best.east_m
                and float(winning_pose[1]) == final_best.north_m,
                f"mode {mode} final best pose differs from strict winning branch",
            )
            require(
                any(
                    item.report_index == winning_branch.get("report_index")
                    and item.catalog_key == winning_branch.get("catalog_key")
                    for item in final_best.assignments
                ),
                f"mode {mode} final best omits winning branch assignment",
            )
        require(
            row.get("counters", {}).get("visited_branches", 0)
            == sum(1 for e in events if e.get("phase") == "branch_result"),
            f"mode {mode} branch count differs",
        )
        if row.get("counters") == {}:
            require(
                row.get("status") == "no_unmatched_reports",
                f"mode {mode} empty counters have unsupported status",
            )
            assigned = {
                item.report_index for item in decode_hypothesis(row["best_hypothesis"]).assignments
            }
            require(
                assigned == set(range(5)), f"mode {mode} no-unmatched state lacks all five reports"
            )
        else:
            require(
                row.get("status") == "completed"
                and row.get("completed") is True
                and row.get("budget_exhausted") is False,
                f"mode {mode} is not completed",
            )
            require(
                row["counters"].get("unvisited_branches") == 0,
                f"mode {mode} has unvisited branches",
            )
    return counts


def _rank_evaluations(
    metrics: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    score_rank = sorted(metrics, key=lambda item: (item["expanded"]["score"], item["mode_index"]))
    error_rank = sorted(
        metrics, key=lambda item: (item["expanded"]["geodesic_error_m"], item["mode_index"])
    )
    for rank, item in enumerate(score_rank, 1):
        item["expanded_score_rank_within_composite_selected_modes"] = rank
    for rank, item in enumerate(error_rank, 1):
        item["expanded_error_rank_within_composite_selected_modes"] = rank
    return score_rank, error_rank


def _truth_metrics(
    rows: list[dict[str, Any]], truth_dir: Path
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    from maritime_localization_shared.contracts.truth import EvaluationTruth
    from maritime_localization_shared.serialization import read_contract

    paths = {
        name: truth_dir / filename
        for name, filename in (
            ("truth", "truth.json"),
            ("support", "support.json"),
            ("truth_crs", "truth-crs.json"),
        )
    }
    truth, support, crs = (read_json(paths[key]) for key in ("truth", "support", "truth_crs"))
    contract = read_contract(paths["truth"], EvaluationTruth)
    require(
        contract.trial_id == TRIAL_ID and truth.get("trial_id") == TRIAL_ID, "truth trial differs"
    )
    require(crs.get("coordinate_reference_system") == "EPSG:32619", "truth CRS differs")
    require(
        support.get("supported") is True
        and support.get("valid_reference_water") is True
        and support.get("port_id") == "port-001"
        and support.get("placement_id") == "placement-001",
        "frozen support is invalid",
    )
    truth_keys: dict[int, set[str]] = {}
    for item in truth.get("objects", []):
        index = item.get("observation_index")
        if index is not None:
            key = str(item.get("ais_record_id", "")).removeprefix("catalog:")
            if key:
                truth_keys.setdefault(int(index), set()).add(key)
    return evaluate_rows(
        rows,
        truth_xy=(contract.observer_east_m, contract.observer_north_m),
        truth_keys_by_report=truth_keys,
    ), {key: sha256_file(path) for key, path in paths.items()}


def audit_composite(
    *,
    serial_root: Path,
    parallel_root: Path,
    serial_audit_path: Path,
    source_root: Path,
    a20_root: Path,
    assigned_root: Path,
    truth_dir: Path,
    expected_bindings: dict[str, Any],
) -> dict[str, Any]:
    serial_manifest, serial_rows, serial_events = validate_manifest(serial_root)
    reviewer.verify_immutable_sources(serial_manifest, "serial")
    audit = read_json(serial_audit_path)
    serial_audited_modes = validate_serial_audit(
        audit, serial_root, serial_manifest, serial_rows, serial_events
    )
    parallel_manifest, parallel_rows, parallel_events, normalized_parallel = (
        _validate_parallel_partial(parallel_root)
    )
    compare_bindings(serial_manifest, normalized_parallel)
    _validate_event_inventory(
        {row["mode_index"]: row for row in serial_rows}, serial_events, pending_mode=60
    )
    _validate_event_inventory(
        {row["mode_index"]: row for row in parallel_rows}, parallel_events, pending_mode=48
    )
    serial_by_id, serial_index = _compare_overlap(
        serial_rows, serial_events, parallel_rows, parallel_events
    )
    # Build a deterministic composite source set. Producer statuses remain preserved below.
    composite, source_by_mode, events_by_mode = _compose_selected_modes(
        serial_rows, serial_events, parallel_rows, parallel_events
    )
    run = load_run(source_root, expected_bindings=expected_bindings)
    a20_manifest, a20_rows = load_refinement(a20_root, source_root, run)
    assigned_manifest, assigned_rows = load_assigned(
        assigned_root, source_root, a20_root, run, a20_manifest, a20_rows
    )
    assigned_manifest["_manifest_sha256"] = sha256_file(assigned_root / "manifest.json")
    validate_serial_audit_sources(
        read_json(serial_audit_path),
        run_identity_sha256=str(run.manifest.get("run_identity_sha256")),
        assigned_manifest_sha256=assigned_manifest["_manifest_sha256"],
        assigned_rows_sha256=str(assigned_manifest.get("rows_sha256")),
        a20_manifest_sha256=sha256_file(a20_root / "manifest.json"),
        a20_rows_sha256=str(a20_manifest.get("rows_sha256")),
    )
    require(
        parallel_manifest.get("assigned_manifest_sha256") == assigned_manifest["_manifest_sha256"],
        "parallel assigned manifest mismatch",
    )
    require(
        parallel_manifest.get("a20_manifest_sha256") == sha256_file(a20_root / "manifest.json"),
        "parallel A20 manifest mismatch",
    )
    require(
        parallel_manifest.get("a20_rows_sha256") == a20_manifest.get("rows_sha256"),
        "parallel A20 rows mismatch",
    )
    require(
        parallel_manifest.get("source_pins") == assigned_manifest.get("source_pins"),
        "parallel source pins mismatch",
    )
    require(
        parallel_manifest.get("frozen_scoring_source_sha")
        == run.identity.get("scoring_source_sha"),
        "parallel scorer source mismatch",
    )
    assigned_raw = read_json(assigned_root / "manifest.json")
    from tools.run_assigned_refinement_diagnostic import build_scorer

    scorer = build_scorer(
        Path(assigned_raw["source_pins"]["input_paths"]["measurements"]).parent,
        Path(assigned_raw["source_pins"]["config_path"]),
    )
    cache: dict[tuple[float, float], Any] = {}
    _exact_rows(composite, assigned_rows, a20_rows, scorer, cache)
    # The serial evaluator's hash-bound exact replay covers only its completed modes 0..59.
    # Overlap rows/events were just validated byte-for-byte, so those same payloads can be reused
    # for the parallel-selected rows in that range. Serial mode-60 tail events are not covered.
    branch_counts = _replay_composite_events(
        composite,
        events_by_mode,
        scorer,
        cache,
        validated_serial_audit_modes=serial_audited_modes,
    )
    # Strip internal source tags before scoring truth metrics.
    metric_rows = []
    for item in composite:
        row = dict(item)
        row.pop("_source_run", None)
        metric_rows.append(row)
    metrics, truth_hashes = _truth_metrics(metric_rows, truth_dir)
    score_rank, error_rank = _rank_evaluations(metrics)
    raw_parallel_counts = {
        str(mode): {
            "started": sum(
                1
                for e in parallel_events
                if e.get("mode_index") == mode and e.get("phase") == "branch_started"
            ),
            "result": sum(
                1
                for e in parallel_events
                if e.get("mode_index") == mode and e.get("phase") == "branch_result"
            ),
            "failure": sum(
                1
                for e in parallel_events
                if e.get("mode_index") == mode and e.get("phase") == "failure"
            ),
        }
        for mode in range(64)
    }
    # Produce the evidence object consumed by the structural reviewer.
    return {
        "schema": "a35-parallel-assignment-expansion-audit-v1",
        "evaluation_status": "complete_audit",
        "branch_rescore_complete": True,
        "evaluation_only": True,
        "truth_used_for_search_or_proposal_selection": False,
        "truth_used": True,
        "accepted_fix_claim": False,
        "certificate_scope": "none",
        "full_domain_complete": False,
        "expansion_status": parallel_manifest.get("status"),
        "parallel_status": parallel_manifest.get("status"),
        "parallel_completed_mode_indices": parallel_manifest.get("completed_mode_indices"),
        "parallel_pending_mode_indices": sorted(
            set(parallel_manifest.get("unknown_mode_indices", []))
            | set(parallel_manifest.get("failed_mode_indices", []))
        ),
        "expansion_raw_completed_mode_count": parallel_manifest.get("completed_mode_count"),
        "expansion_raw_pending_mode_count": parallel_manifest.get("unknown_mode_count"),
        "expansion_raw_completed_mode_indices": parallel_manifest.get("completed_mode_indices"),
        "mode_count": 64,
        "completed_mode_count": 64,
        "pending_mode_count": 0,
        "composite_selected_mode_status": "complete",
        "composite_mode_sources": {str(mode): source_by_mode[mode] for mode in range(64)},
        "serial_completed_mode_count": 60,
        "serial_pending_mode_count": 4,
        "expansion_manifest_sha256": sha256_file(parallel_root / "manifest.json"),
        "expansion_rows_sha256": parallel_manifest.get("rows_sha256"),
        "expansion_events_sha256": parallel_manifest.get("events_sha256"),
        "expansion_event_count": parallel_manifest.get("event_count"),
        "expansion_event_digest": parallel_manifest.get("event_digest"),
        "serial_manifest_sha256": sha256_file(serial_root / "manifest.json"),
        "serial_audit_sha256": sha256_file(serial_audit_path),
        "source_run_identity_sha256": run.manifest.get("run_identity_sha256"),
        "assigned_manifest_sha256": parallel_manifest.get("assigned_manifest_sha256"),
        "assigned_rows_sha256": assigned_manifest.get("rows_sha256"),
        "a20_manifest_sha256": parallel_manifest.get("a20_manifest_sha256"),
        "a20_rows_sha256": parallel_manifest.get("a20_rows_sha256"),
        "assignment_expansion_sha256": parallel_manifest["coordinator_loaded_package"].get(
            "assignment_expansion_sha256"
        ),
        "frozen_scoring_source_sha256": parallel_manifest.get("frozen_scoring_source_sha"),
        **branch_counts,
        "parallel_raw_event_mode_counts": raw_parallel_counts,
        "event_mode_counts": raw_parallel_counts,
        "mode_evaluations": metrics,
        "truth_inputs_sha256": truth_hashes,
        "expanded_best_score": score_rank[0]["expanded"]["score"],
        "expanded_best_error_m": score_rank[0]["expanded"]["geodesic_error_m"],
        "expanded_best_score_rank_row": score_rank[0]["mode_index"],
        "nearest_expanded_mode_index": error_rank[0]["mode_index"],
        "nearest_expanded_error_m": error_rank[0]["expanded"]["geodesic_error_m"],
        "expanded_modes_within_100m": sum(
            item["expanded"]["geodesic_error_m"] <= 100.0 for item in metrics
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for option in (
        "serial-root",
        "parallel-root",
        "serial-audit",
        "source-root",
        "a20-root",
        "assigned-root",
        "truth-dir",
        "expected-bindings",
        "output",
    ):
        parser.add_argument("--" + option, type=Path, required=True)
    args = parser.parse_args()
    result = audit_composite(
        serial_root=args.serial_root,
        parallel_root=args.parallel_root,
        serial_audit_path=args.serial_audit,
        source_root=args.source_root,
        a20_root=args.a20_root,
        assigned_root=args.assigned_root,
        truth_dir=args.truth_dir,
        expected_bindings=read_json(args.expected_bindings),
    )
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "evaluation_status",
                    "completed_mode_count",
                    "expansion_raw_completed_mode_count",
                    "serial_audited_branch_results_reused",
                    "parallel_new_branch_results_rescored",
                    "expanded_best_score",
                    "expanded_best_error_m",
                    "nearest_expanded_mode_index",
                    "nearest_expanded_error_m",
                    "parallel_status",
                    "parallel_completed_mode_indices",
                    "parallel_pending_mode_indices",
                    "composite_selected_mode_status",
                    "composite_mode_sources",
                )
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
