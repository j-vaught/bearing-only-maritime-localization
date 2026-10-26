"""Independent evaluator for the bounded assignment-expansion diagnostic."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any, cast

from tools.evaluate_a20_cached_refinement import load_refinement
from tools.evaluate_assigned_refinement import load_assigned
from tools.evaluate_cached_finite_domain import (
    decode_hypothesis,
    geodesic_error_m,
    load_run,
    read_json,
    read_jsonl,
    sha256_file,
)

SCHEMA = "assignment-expansion-diagnostic-v1"
ROWS_NAME = "assignment-expansion-rows.jsonl"
EVENTS_NAME = "branch-events.jsonl"
CHECKPOINT_NAME = "checkpoint.json"
MODE_COUNT = 64
ATOL = 1e-8
RTOL = 1e-10


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def evaluate_exact_position(
    scorer: Any,
    cache: dict[tuple[float, float], Any],
    position: tuple[float, float],
) -> Any:
    key = (float(position[0]), float(position[1]))
    if key not in cache:
        cache[key] = scorer.evaluate(key)
    return cache[key]


def _same_score(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=RTOL, abs_tol=ATOL)


def _same_hypothesis(a: Any, b: Any) -> bool:
    return (
        (a.east_m, a.north_m) == (b.east_m, b.north_m)
        and _same_score(a.score, b.score)
        and a.assignments == b.assignments
        and a.score_components == b.score_components
    )


def validate_mode_counts(manifest: dict[str, Any]) -> None:
    require(manifest.get("mode_count") == MODE_COUNT, "mode cap must be exactly 64")
    completed_raw = manifest.get("completed_mode_count")
    pending_raw = manifest.get("pending_mode_count")
    require(
        type(completed_raw) is int and type(pending_raw) is int,
        "completed and pending mode counts must be integers",
    )
    completed = cast(int, completed_raw)
    pending = cast(int, pending_raw)
    require(completed + pending == MODE_COUNT, "completed and pending counts do not sum to 64")
    require(
        completed == MODE_COUNT and pending == 0,
        "incomplete assignment expansion cannot be evaluated",
    )


def choose_start_label(alternatives: list[dict[str, Any]]) -> str:
    require(
        type(alternatives) is list and len(alternatives) == 2,
        "each mode must retain both assigned starts",
    )
    scored = []
    for item in alternatives:
        label = item.get("label")
        require(type(label) is str, "start label must be a string")
        hypothesis = decode_hypothesis(item.get("canonical_hypothesis"))
        scored.append((hypothesis.score, cast(str, label)))
    return min(scored)[1]


def validate_objective_progress(
    seed_score: float, best_score: float, improved_scores: list[float]
) -> None:
    require(best_score <= seed_score, "assignment expansion increased the objective")
    if best_score < seed_score:
        require(bool(improved_scores), "strict improvement lacks an improved branch event")
    expected = min([seed_score, *improved_scores])
    require(
        _same_score(best_score, expected),
        "best score differs from seed and improved branch results",
    )


def validate_branch_score_sequence(
    seed_score: float, branches: list[dict[str, Any]]
) -> list[float]:
    best_score = seed_score
    improved: list[float] = []
    for branch in branches:
        exact_score = branch.get("exact_score")
        status = branch.get("status")
        if exact_score is None:
            require(
                status not in {"improved", "no_improvement"},
                "scored branch status has no exact score",
            )
            continue
        require(
            type(exact_score) in (int, float) and math.isfinite(exact_score),
            "branch exact score must be finite",
        )
        exact_score = float(exact_score)
        if status == "improved":
            require(
                exact_score < best_score,
                "branch marked improved without a strict best-score decrease",
            )
            best_score = exact_score
            improved.append(exact_score)
        else:
            require(
                status == "no_improvement", "exact-scored branch has an unsupported terminal status"
            )
            require(
                exact_score >= best_score,
                "branch marked no_improvement below the current best score",
            )
    return improved


def validate_no_unmatched_mode(
    row: dict[str, Any],
    seed: Any,
    best: Any,
    *,
    measurement_count: int,
    result_event_count: int,
) -> None:
    assigned_reports = {item.report_index for item in seed.assignments}
    require(
        assigned_reports == set(range(measurement_count)),
        "no-unmatched status is false; seed does not cover every report",
    )
    require(
        row.get("counters") == {}
        and result_event_count == 0
        and row.get("branch_event_count") == 0,
        "no-unmatched mode has unexpected counters or branch events",
    )
    require(
        _same_hypothesis(best, seed),
        "no-unmatched mode changed its exact seed",
    )
    require(
        row.get("completed") is True and row.get("budget_exhausted") is False,
        "no-unmatched mode has inconsistent completion flags",
    )


def verify_event_chain(events: list[dict[str, Any]]) -> str:
    previous = ""
    for index, record in enumerate(events):
        require(type(record) is dict, "event record must be an object")
        require(record.get("event_index") == index, "event indices are not contiguous")
        require(record.get("previous_event_sha256") == previous, "event hash chain is broken")
        payload = {key: value for key, value in record.items() if key != "event_sha256"}
        digest = canonical_hash(payload)
        require(record.get("event_sha256") == digest, "event record hash mismatch")
        require(type(record.get("mode_index")) is int, "event mode_index must be an integer")
        require(0 <= record["mode_index"] < MODE_COUNT, "event mode_index is out of range")
        require(
            record.get("phase") in {"branch_started", "branch_result", "failure"},
            "unknown event phase",
        )
        require(type(record.get("event")) is dict, "event payload must be an object")
        previous = digest
    return previous


def verify_mode_event_correspondence(
    rows: list[dict[str, Any]], events: list[dict[str, Any]]
) -> dict[int, dict[str, int]]:
    by_mode: dict[int, dict[str, dict[int, dict[str, Any]]]] = {
        index: {"branch_started": {}, "branch_result": {}, "failure": {}}
        for index in range(MODE_COUNT)
    }
    for record in events:
        mode = record["mode_index"]
        phase = record["phase"]
        event = record["event"]
        require(phase != "failure", "a completed diagnostic cannot contain a failure event")
        branch_id = event.get("branch_id")
        require(type(branch_id) is int and branch_id >= 0, "branch_id must be nonnegative")
        bucket = by_mode[mode][phase]
        require(branch_id not in bucket, "duplicate branch event for mode and phase")
        bucket[branch_id] = event
    result: dict[int, dict[str, int]] = {}
    for index, row in enumerate(rows):
        started = by_mode[index]["branch_started"]
        finished = by_mode[index]["branch_result"]
        require(set(started) == set(finished), "started branch lacks exactly one result event")
        require(
            row.get("branch_event_count") == len(finished),
            "row branch event count differs from durable event ledger",
        )
        counters_raw = row.get("counters")
        require(type(counters_raw) is dict, "row counters must be an object")
        counters = cast(dict[str, Any], counters_raw)
        if row.get("status") == "no_unmatched_reports":
            require(counters == {}, "no-unmatched mode should preserve its empty counters")
            require(
                not started and not finished and row.get("branch_event_count") == 0,
                "no-unmatched mode must have zero branch events",
            )
            result[index] = {"started": 0, "result": 0, "failure": 0}
            continue
        require(
            counters.get("visited_branches") == len(finished),
            "visited branch count differs from durable result events",
        )
        for branch_id, start in started.items():
            end = finished[branch_id]
            for key in ("report_index", "catalog_key"):
                require(
                    start.get(key) == end.get(key),
                    f"branch {key} differs between start and result events",
                )
            require(start.get("status") == "started", "branch-start event has invalid status")
            require(end.get("status") != "started", "branch-result event is not a result")
        result[index] = {
            "started": len(started),
            "result": len(finished),
            "failure": len(by_mode[index]["failure"]),
        }
    return result


def _verify_ranked_manifest(manifest: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    ranked = manifest.get("ranked_selected_modes")
    require(
        type(ranked) is list and len(ranked) == MODE_COUNT,
        "selected-mode ranking must contain all 64 modes",
    )
    expected = [
        {
            "mode_index": row["mode_index"],
            "candidate_id": row["candidate_id"],
            "selected_start_label": row["selected_start_label"],
            "score": row["best_hypothesis"]["score"],
            "search_status": row["status"],
        }
        for row in sorted(
            rows, key=lambda item: (item["best_hypothesis"]["score"], item["mode_index"])
        )
    ]
    require(ranked == expected, "manifest ranking differs from durable rows")


def validate_expansion_bundle(
    root: Path,
    *,
    source_run: Any,
    a20_rows: list[dict[str, Any]],
    assigned_manifest: dict[str, Any],
    assigned_rows: list[dict[str, Any]],
    scorer: Any,
    source_root: Path,
    a20_root: Path,
    assigned_root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    manifest_path = root / "manifest.json"
    manifest = read_json(manifest_path)
    require(manifest.get("schema") == SCHEMA, "unsupported assignment-expansion schema")
    require(manifest.get("status") == "completed", "assignment expansion did not complete")
    validate_mode_counts(manifest)
    require(
        manifest.get("full_domain_complete") is False, "diagnostic claims full-domain completion"
    )
    require(
        manifest.get("decision_status") == "ambiguous", "diagnostic decision must remain ambiguous"
    )
    require(
        manifest.get("search_status") == "budget_exhausted",
        "diagnostic search status must remain budget_exhausted",
    )
    require(manifest.get("certificate_scope") == "none", "diagnostic claims a certificate")
    require(
        manifest.get("truth_used") is False and manifest.get("evaluator_used") is False,
        "runner manifest crossed the evaluator boundary",
    )
    require(manifest.get("resume_supported") is False, "runner unexpectedly claims resume support")
    require(
        manifest.get("assigned_root") == str(assigned_root.resolve()),
        "runner references a different assigned artifact path",
    )
    require(
        manifest.get("a20_root") == str(a20_root.resolve()),
        "runner references a different A20 artifact path",
    )
    require(
        manifest.get("assigned_manifest_sha256") == sha256_file(assigned_root / "manifest.json"),
        "assigned manifest binding mismatch",
    )
    source_pins = manifest.get("source_pins")
    require(type(source_pins) is dict, "runner source pins are missing")
    require(
        Path(source_pins.get("attempt", "")).resolve() == source_root.resolve(),
        "runner references a different finite-domain source run",
    )
    require(
        manifest.get("assigned_manifest_sha256") == assigned_manifest.get("_manifest_sha256"),
        "assigned input differs from bound assigned artifact",
    )
    require(
        manifest.get("a20_manifest_sha256") == assigned_manifest.get("a20_manifest_sha256"),
        "A20 manifest binding differs from assigned artifact",
    )
    require(
        manifest.get("a20_rows_sha256") == assigned_manifest.get("a20_rows_sha256"),
        "A20 rows binding differs from assigned artifact",
    )
    require(
        manifest.get("input_assigned_package_commit") == assigned_manifest.get("package_commit"),
        "assigned package provenance mismatch",
    )
    require(
        manifest.get("frozen_scoring_source_sha") == source_run.identity.get("scoring_source_sha"),
        "frozen scoring package differs from finite-domain source",
    )
    package_source = Path(manifest.get("package_source", ""))
    module_source = Path(manifest.get("assignment_expansion_source", ""))
    require(
        package_source.is_file() and module_source.is_file(),
        "runner package provenance files are unavailable",
    )
    require(
        sha256_file(module_source) == manifest.get("assignment_expansion_sha256"),
        "assignment-expansion source hash mismatch",
    )
    package_root = package_source.parent.parent
    runner_source = package_root / "tools" / "run_assignment_expansion_diagnostic.py"
    require(
        runner_source.is_file() and sha256_file(runner_source) == manifest.get("runner_sha256"),
        "runner source hash mismatch",
    )
    package_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=package_root, text=True
    ).strip()
    require(
        package_commit == manifest.get("package_commit") == manifest.get("harness_commit"),
        "runner package and harness source commits differ",
    )

    rows_path = root / ROWS_NAME
    events_path = root / EVENTS_NAME
    checkpoint_path = root / CHECKPOINT_NAME
    require(manifest.get("rows_file") == ROWS_NAME, "unexpected rows filename")
    require(manifest.get("events_file") == EVENTS_NAME, "unexpected events filename")
    require(manifest.get("checkpoint_file") == CHECKPOINT_NAME, "unexpected checkpoint filename")
    require(sha256_file(rows_path) == manifest.get("rows_sha256"), "rows hash mismatch")
    require(sha256_file(events_path) == manifest.get("events_sha256"), "events hash mismatch")
    rows = read_jsonl(rows_path)
    events = read_jsonl(events_path)
    checkpoint = read_json(checkpoint_path)
    require(len(rows) == MODE_COUNT, "durable rows must contain 64 modes")
    require(checkpoint.get("status") == "completed", "checkpoint is not completed")
    require(
        checkpoint.get("completed_mode_count") == MODE_COUNT
        and checkpoint.get("pending_mode_count") == 0,
        "checkpoint counts are inconsistent",
    )
    require(
        checkpoint.get("rows_sha256") == manifest.get("rows_sha256")
        and checkpoint.get("events_sha256") == manifest.get("events_sha256"),
        "checkpoint file hashes differ from final manifest",
    )
    require(
        checkpoint.get("event_count") == manifest.get("event_count") == len(events),
        "event counts differ",
    )
    event_digest = verify_event_chain(events)
    require(
        event_digest == manifest.get("event_digest") == checkpoint.get("event_digest"),
        "final event chain digest differs from manifest/checkpoint",
    )
    event_counts = verify_mode_event_correspondence(rows, events)
    exact_evaluations: dict[tuple[float, float], Any] = {}

    require(
        len(assigned_rows) == MODE_COUNT and len(a20_rows) == MODE_COUNT,
        "source artifacts must each contain 64 selected modes",
    )
    for index, (row, assigned, a20) in enumerate(zip(rows, assigned_rows, a20_rows, strict=True)):
        require(row.get("mode_index") == index, "expansion rows are not in deterministic order")
        require(
            row.get("input_index") == assigned.get("input_index") == a20.get("input_index"),
            "source input index differs from assigned/A20 selection",
        )
        require(
            row.get("candidate_id") == assigned.get("candidate_id") == a20.get("candidate_id"),
            "source candidate identity differs from assigned/A20 selection",
        )
        alternatives_raw = row.get("start_alternatives")
        require(
            type(alternatives_raw) is list and len(alternatives_raw) == 2,
            "each mode must retain both assigned starts",
        )
        alternatives = cast(list[dict[str, Any]], alternatives_raw)
        require(
            [item.get("label") for item in alternatives]
            == ["cached_hypothesis", "refined_mode.hypothesis"],
            "start alternatives are missing or reordered",
        )
        assigned_starts_raw = assigned.get("starts")
        require(
            type(assigned_starts_raw) is list and len(assigned_starts_raw) == 2,
            "assigned source row does not retain both starts",
        )
        assigned_starts = cast(list[dict[str, Any]], assigned_starts_raw)
        parsed = []
        for alternative, source_start in zip(alternatives, assigned_starts, strict=True):
            saved = decode_hypothesis(alternative.get("saved_hypothesis"))
            canonical = decode_hypothesis(alternative.get("canonical_hypothesis"))
            source_final = decode_hypothesis(source_start.get("final_hypothesis"))
            require(
                _same_hypothesis(saved, source_final),
                "expansion start differs from the finalized assigned source mode",
            )
            require(
                alternative.get("canonical_match") is True,
                "runner did not record canonical start agreement",
            )
            evaluation = evaluate_exact_position(
                scorer, exact_evaluations, (canonical.east_m, canonical.north_m)
            )
            require(
                evaluation.status == "scored" and evaluation.hypothesis is not None,
                "canonical start cannot be exactly rescored",
            )
            exact = evaluation.hypothesis
            require(
                _same_hypothesis(canonical, exact), "canonical start differs from pinned scorer"
            )
            require(_same_hypothesis(saved, exact), "saved start differs from pinned scorer")
            parsed.append((alternative["label"], exact))
        chosen_label = choose_start_label(alternatives)
        chosen = dict(parsed)[chosen_label]
        require(
            row.get("selected_start_label") == chosen_label,
            "chosen start was not the deterministic score minimum",
        )
        seed = decode_hypothesis(row.get("seed"))
        best = decode_hypothesis(row.get("best_hypothesis"))
        require(
            _same_hypothesis(seed, chosen), "expansion seed differs from chosen score-only start"
        )
        exact_best_eval = evaluate_exact_position(
            scorer, exact_evaluations, (best.east_m, best.north_m)
        )
        require(
            exact_best_eval.status == "scored" and exact_best_eval.hypothesis is not None,
            "best hypothesis cannot be exactly rescored",
        )
        exact_best = exact_best_eval.hypothesis
        require(_same_hypothesis(best, exact_best), "best hypothesis differs from pinned scorer")
        require(best.score <= seed.score, "assignment expansion increased the objective")
        counts_raw = row.get("counters")
        require(type(counts_raw) is dict, "mode counters are missing")
        counts = cast(dict[str, Any], counts_raw)
        if row.get("status") == "no_unmatched_reports":
            validate_no_unmatched_mode(
                row,
                seed,
                best,
                measurement_count=len(scorer.measurements.measurements),
                result_event_count=event_counts[index]["result"],
            )
            continue
        require(
            type(counts.get("candidate_branches")) is int
            and type(counts.get("visited_branches")) is int
            and type(counts.get("unvisited_branches")) is int,
            "branch counters must be integer-valued",
        )
        require(
            counts["candidate_branches"]
            == counts["visited_branches"] + counts["unvisited_branches"],
            "branch inventory does not partition into visited and unvisited",
        )
        require(
            counts["visited_branches"] == event_counts[index]["result"],
            "branch results differ from visit count",
        )
        require(
            row.get("branch_event_count") == event_counts[index]["result"],
            "branch-event row count mismatch",
        )
        mode_results = [
            record["event"]
            for record in events
            if record["mode_index"] == index and record["phase"] == "branch_result"
        ]
        improved_scores = validate_branch_score_sequence(seed.score, mode_results)
        exact_branch_hypotheses = []
        for branch in mode_results:
            exact_score = branch.get("exact_score")
            if exact_score is None:
                require(
                    branch.get("status") != "improved",
                    "improved branch lacks a canonical exact score",
                )
                continue
            pose = branch.get("proposed_pose")
            require(
                type(pose) is list and len(pose) == 2,
                "exact branch result lacks its evaluated pose",
            )
            exact_eval = evaluate_exact_position(
                scorer, exact_evaluations, (float(pose[0]), float(pose[1]))
            )
            require(
                exact_eval.status == "scored" and exact_eval.hypothesis is not None,
                "branch exact result cannot be re-scored",
            )
            branch_hypothesis = exact_eval.hypothesis
            require(
                _same_score(branch_hypothesis.score, float(exact_score)),
                "branch exact score differs from pinned scorer",
            )
            added_retained = any(
                assignment.report_index == branch.get("report_index")
                and assignment.catalog_key == branch.get("catalog_key")
                for assignment in branch_hypothesis.assignments
            )
            require(
                branch.get("added_assignment_retained") is added_retained,
                "branch assignment-retention flag differs from exact assignment",
            )
            exact_branch_hypotheses.append(branch_hypothesis)
        validate_objective_progress(seed.score, best.score, improved_scores)
        if best.score < seed.score:
            require(
                any(_same_hypothesis(best, candidate) for candidate in exact_branch_hypotheses),
                "best improved hypothesis is not an exact completed branch result",
            )
        else:
            require(_same_hypothesis(best, seed), "non-improved result changed the seed hypothesis")
        require(
            counts.get("improved_branches") == len(improved_scores),
            "improved branch counter differs from event ledger",
        )
        require(
            row.get("status")
            in {
                "completed",
                "no_unmatched_reports",
                "budget_exhausted",
                "invalid_bounds",
                "invalid_seed",
                "stale_hypothesis",
                "invalid_water",
                "unresolved_coverage",
                "unresolved_model",
                "unsupported_calibration",
                "unsupported_covariance",
                "insufficient_assignments",
                "invalid_assignment",
            },
            "unknown expansion status",
        )
        if row.get("status") == "completed":
            require(
                counts["unvisited_branches"] == 0
                and row.get("completed") is True
                and row.get("budget_exhausted") is False,
                "completed expansion has unvisited work or inconsistent flags",
            )
        elif row.get("status") == "budget_exhausted":
            require(
                counts["unvisited_branches"] > 0 and row.get("budget_exhausted") is True,
                "budget-exhausted expansion has no pending branch work",
            )
    _verify_ranked_manifest(manifest, rows)
    return manifest, rows, events


def _assignment_metrics(
    hypothesis: Any, truth_keys_by_report: dict[int, set[str]]
) -> dict[str, Any]:
    assignments = [
        {
            "report_index": item.report_index,
            "catalog_key": item.catalog_key,
            "correct": item.catalog_key in truth_keys_by_report.get(item.report_index, set()),
        }
        for item in hypothesis.assignments
    ]
    return {
        "assignment_count": len(assignments),
        "correct_assignment_count": sum(item["correct"] for item in assignments),
        "assignments": assignments,
    }


def evaluate_rows(
    rows: list[dict[str, Any]],
    *,
    truth_xy: tuple[float, float],
    truth_keys_by_report: dict[int, set[str]],
) -> list[dict[str, Any]]:
    results = []
    for row in rows:
        seed = decode_hypothesis(row["seed"])
        best = decode_hypothesis(row["best_hypothesis"])
        result = {
            "mode_index": row["mode_index"],
            "candidate_id": row["candidate_id"],
            "selected_start_label": row["selected_start_label"],
        }
        for name, hypothesis in (("seed", seed), ("expanded", best)):
            result[name] = {
                "position_xy_m": [hypothesis.east_m, hypothesis.north_m],
                "score": hypothesis.score,
                "geodesic_error_m": geodesic_error_m(
                    (hypothesis.east_m, hypothesis.north_m),
                    truth_xy,
                    candidate_crs="EPSG:32618",
                    truth_crs="EPSG:32619",
                ),
                **_assignment_metrics(hypothesis, truth_keys_by_report),
            }
        result["status"] = row["status"]
        result["completed"] = row["completed"]
        result["budget_exhausted"] = row["budget_exhausted"]
        result["counters"] = row["counters"]
        result["branch_event_count"] = row["branch_event_count"]
        results.append(result)
    seed_ranked = sorted(results, key=lambda item: (item["seed"]["score"], item["mode_index"]))
    expanded_ranked = sorted(
        results, key=lambda item: (item["expanded"]["score"], item["mode_index"])
    )
    for rank, item in enumerate(seed_ranked, 1):
        item["seed_score_rank_within_64"] = rank
    for rank, item in enumerate(expanded_ranked, 1):
        item["expanded_score_rank_within_64"] = rank
    return results


def evaluate_artifact(
    root: Path,
    *,
    source_root: Path,
    a20_root: Path,
    assigned_root: Path,
    truth_dir: Path,
    expected_bindings: dict[str, Any],
) -> dict[str, Any]:
    run = load_run(source_root, expected_bindings=expected_bindings)
    a20_manifest, a20_rows = load_refinement(a20_root, source_root, run)
    assigned_manifest, assigned_rows = load_assigned(
        assigned_root, source_root, a20_root, run, a20_manifest, a20_rows
    )
    assigned_manifest["_manifest_sha256"] = sha256_file(assigned_root / "manifest.json")
    assigned_manifest["a20_manifest_sha256"] = sha256_file(a20_root / "manifest.json")
    assigned_manifest["a20_rows_sha256"] = a20_manifest.get("rows_sha256")
    # No evaluator truth is opened until all runner/source bindings pass.
    assigned_source = read_json(assigned_root / "manifest.json")
    from tools.run_assigned_refinement_diagnostic import build_scorer

    static = Path(assigned_source["source_pins"]["input_paths"]["measurements"]).parent
    config_path = Path(assigned_source["source_pins"]["config_path"])
    scorer = build_scorer(static, config_path)
    manifest, rows, events = validate_expansion_bundle(
        root,
        source_run=run,
        a20_rows=a20_rows,
        assigned_manifest=assigned_manifest,
        assigned_rows=assigned_rows,
        scorer=scorer,
        source_root=source_root,
        a20_root=a20_root,
        assigned_root=assigned_root,
    )
    truth_path = truth_dir / "truth.json"
    support_path = truth_dir / "support.json"
    crs_path = truth_dir / "truth-crs.json"
    truth = read_json(truth_path)
    support = read_json(support_path)
    crs = read_json(crs_path)
    from maritime_localization_shared.contracts.truth import EvaluationTruth
    from maritime_localization_shared.serialization import read_contract

    truth_contract = read_contract(truth_path, EvaluationTruth)
    trial_id = "development/port-001/placement-001/r0/range10000m/typesoff/nominal"
    require(
        truth_contract.trial_id == trial_id and truth.get("trial_id") == trial_id,
        "evaluator truth differs from frozen development trial",
    )
    require(
        crs.get("coordinate_reference_system") == "EPSG:32619",
        "truth CRS differs from frozen evaluator contract",
    )
    require(
        support.get("supported") is True
        and support.get("valid_reference_water") is True
        and support.get("port_id") == "port-001"
        and support.get("placement_id") == "placement-001",
        "frozen evaluator support is invalid or belongs to a different trial",
    )
    truth_keys: dict[int, set[str]] = {}
    for obj in truth.get("objects", []):
        if obj.get("observation_index") is not None:
            key = str(obj.get("ais_record_id", "")).removeprefix("catalog:")
            if key:
                truth_keys.setdefault(int(obj["observation_index"]), set()).add(key)
    modes = evaluate_rows(
        rows,
        truth_xy=(truth_contract.observer_east_m, truth_contract.observer_north_m),
        truth_keys_by_report=truth_keys,
    )
    expanded_rank = sorted(modes, key=lambda item: (item["expanded"]["score"], item["mode_index"]))
    error_rank = sorted(
        modes, key=lambda item: (item["expanded"]["geodesic_error_m"], item["mode_index"])
    )
    for rank, item in enumerate(error_rank, 1):
        item["expanded_error_rank_within_64"] = rank
    source_errors = [item["seed"]["geodesic_error_m"] for item in modes]
    expanded_errors = [item["expanded"]["geodesic_error_m"] for item in modes]
    return {
        "schema": "a35-assignment-expansion-evaluation-v1",
        "evaluation_only": True,
        "truth_used_for_search_or_proposal_selection": False,
        "accepted_fix_claim": False,
        "full_domain_complete": False,
        "certificate_scope": "none",
        "source_search_status": run.manifest.get("search_status"),
        "source_decision_status": run.manifest.get("decision_status"),
        "source_disposition_counts": run.disposition_counts,
        "expansion_status": manifest.get("status"),
        "mode_count": MODE_COUNT,
        "selected_mode_scope": "fixed 64 modes from the preceding selected-set diagnostic",
        "completed_mode_count": manifest.get("completed_mode_count"),
        "pending_mode_count": manifest.get("pending_mode_count"),
        "source_run_identity_sha256": run.manifest.get("run_identity_sha256"),
        "assigned_manifest_sha256": manifest.get("assigned_manifest_sha256"),
        "assigned_rows_sha256": assigned_manifest.get("rows_sha256"),
        "a20_manifest_sha256": manifest.get("a20_manifest_sha256"),
        "a20_rows_sha256": manifest.get("a20_rows_sha256"),
        "expansion_manifest_sha256": sha256_file(root / "manifest.json"),
        "expansion_rows_sha256": manifest.get("rows_sha256"),
        "expansion_events_sha256": manifest.get("events_sha256"),
        "expansion_event_count": len(events),
        "expansion_event_digest": manifest.get("event_digest"),
        "evaluator_inputs_sha256": {
            "truth": sha256_file(truth_path),
            "support": sha256_file(support_path),
            "truth_crs": sha256_file(crs_path),
        },
        "seed_best_score": min(item["seed"]["score"] for item in modes),
        "expanded_best_score": min(item["expanded"]["score"] for item in modes),
        "seed_best_error_m": min(source_errors),
        "expanded_best_error_m": min(expanded_errors),
        "expanded_best_score_rank_row": expanded_rank[0]["mode_index"],
        "nearest_expanded_mode_index": error_rank[0]["mode_index"],
        "expanded_modes_within_100m": sum(error <= 100.0 for error in expanded_errors),
        "mode_evaluations": modes,
        "event_mode_counts": verify_mode_event_correspondence(rows, events),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "root",
        "source-root",
        "a20-root",
        "assigned-root",
        "truth-dir",
        "expected-bindings",
        "output",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    result = evaluate_artifact(
        args.root,
        source_root=args.source_root,
        a20_root=args.a20_root,
        assigned_root=args.assigned_root,
        truth_dir=args.truth_dir,
        expected_bindings=read_json(args.expected_bindings),
    )
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "expanded_best_score",
                    "expanded_best_error_m",
                    "nearest_expanded_mode_index",
                    "expanded_modes_within_100m",
                    "expansion_event_count",
                )
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
