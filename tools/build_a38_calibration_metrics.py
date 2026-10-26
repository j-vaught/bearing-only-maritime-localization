"""Build evaluator-only A24 calibration rows from search evidence and trial truth."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from tools.run_a38_execution import folder, hfile
from tools.run_accuracy_suite import _read_table

EVIDENCE_SCHEMA = "search-mode-evidence-v1"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _public_evidence(telemetry: dict[str, Any] | None) -> dict[str, Any] | None:
    if telemetry is None:
        return None
    diagnostics = telemetry.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return None
    evidence = diagnostics.get("public_evidence")
    if not isinstance(evidence, dict) or evidence.get("schema") != EVIDENCE_SCHEMA:
        return None
    return evidence


def _matches_top(mode: dict[str, Any], top: dict[str, Any]) -> bool:
    return all(
        _number(mode.get(key)) is not None and abs(float(mode[key]) - float(top[key])) <= 1e-6
        for key in ("east_m", "north_m", "score")
    )


def build_trial_metrics(
    trial: dict[str, Any],
    *,
    run_state: dict[str, Any] | None,
    estimate: dict[str, Any] | None,
    telemetry: dict[str, Any] | None,
    evaluation: dict[str, Any] | None,
    truth: dict[str, Any] | None,
    support: dict[str, Any] | None,
) -> dict[str, Any]:
    """Derive one calibration row without giving truth to search-side evidence."""
    split = str(trial["split"])
    if split not in {"development", "validation"}:
        raise ValueError("Calibration metrics are restricted to development or validation.")
    row: dict[str, Any] = {
        "split": split,
        "trial_id": str(trial["trial_id"]),
        "port_id": str(trial["port_id"]),
        "placement_id": str(trial["placement_id"]),
        "realization": int(trial["realization"]),
        "range_m": float(trial["range_m"]),
        "report_types": trial["report_types"] == "True",
        "sensor_condition": str(trial["sensor_condition"]),
        "run_status": str((run_state or {}).get("status", "missing")),
        "failure": str((run_state or {}).get("failure", "")),
        "supported": (support or {}).get("supported") is True,
        "search_status": "failed",
        "distinct_matched_groups": 0,
        "ellipse_rank": None,
        "meaningful_distant_coverage": False,
        "score_gap": None,
        "radius_95_m": None,
        "error_m": None,
        "evidence_status": "missing",
        "error_source": None,
        "evaluation_acceptance_status": (evaluation or {}).get("acceptance_status"),
    }
    if estimate is None:
        row["evidence_status"] = "estimate_missing"
        return row

    status = estimate.get("search_status")
    row["search_status"] = (
        status if status in {"completed", "budget_exhausted", "failed"} else "failed"
    )
    hypotheses = estimate.get("hypotheses")
    if not isinstance(hypotheses, list) or not hypotheses:
        row["evidence_status"] = "no_ranked_hypotheses"
        return row
    top = hypotheses[0]
    if not isinstance(top, dict):
        row["evidence_status"] = "malformed_top_hypothesis"
        return row
    top_east = _number(top.get("east_m"))
    top_north = _number(top.get("north_m"))
    top_score = _number(top.get("score"))
    assignments = top.get("assignments")
    if isinstance(assignments, list):
        row["distinct_matched_groups"] = len(
            {
                item.get("catalog_key")
                for item in assignments
                if isinstance(item, dict)
                and isinstance(item.get("catalog_key"), str)
                and item["catalog_key"].strip()
            }
        )
    if truth is not None and top_east is not None and top_north is not None:
        truth_east = _number(truth.get("observer_east_m"))
        truth_north = _number(truth.get("observer_north_m"))
        if truth_east is not None and truth_north is not None:
            row["error_m"] = math.hypot(top_east - truth_east, top_north - truth_north)
            row["error_source"] = "evaluator_truth_pose_vs_top_ranked_hypothesis"

    evidence = _public_evidence(telemetry)
    if evidence is None:
        row["evidence_status"] = "public_mode_evidence_missing_or_unsupported_schema"
        return row
    if evidence.get("search_status") != row["search_status"]:
        row["evidence_status"] = "search_status_mismatch"
        return row
    modes = evidence.get("mode_evidence")
    if not isinstance(modes, list):
        row["evidence_status"] = "mode_evidence_not_a_list"
        return row
    if type(evidence.get("top_mode_converged")) is not bool:
        row["evidence_status"] = "top_mode_convergence_missing"
        return row
    if (
        not evidence["top_mode_converged"]
        or top_east is None
        or top_north is None
        or top_score is None
    ):
        row["evidence_status"] = "top_mode_not_converged"
        return row

    converged: list[dict[str, Any]] = []
    for mode in modes:
        if not isinstance(mode, dict) or type(mode.get("converged")) is not bool:
            row["evidence_status"] = "mode_convergence_flag_missing"
            return row
        if mode["converged"]:
            if any(_number(mode.get(key)) is None for key in ("east_m", "north_m", "score")):
                row["evidence_status"] = "malformed_converged_mode"
                return row
            converged.append(mode)
    top_modes = [mode for mode in converged if _matches_top(mode, top)]
    if not top_modes:
        row["evidence_status"] = "top_mode_not_in_converged_evidence"
        return row
    top_mode = top_modes[0]
    rank = top_mode.get("ellipse_rank")
    row["ellipse_rank"] = rank if type(rank) is int and rank >= 0 else None
    radius = _number(top_mode.get("radius_95_m"))
    row["radius_95_m"] = radius

    distant_gaps: list[float] = []
    for mode in converged:
        east = float(mode["east_m"])
        north = float(mode["north_m"])
        score = float(mode["score"])
        distance = math.hypot(east - top_east, north - top_north)
        if distance >= 100.0:
            gap = score - top_score
            if gap < -1e-8:
                row["evidence_status"] = "converged_mode_ranks_better_than_top"
                return row
            distant_gaps.append(max(0.0, gap))
    row["meaningful_distant_coverage"] = bool(distant_gaps)
    row["score_gap"] = min(distant_gaps) if distant_gaps else None
    row["evidence_status"] = "complete"
    return row


def build_document(
    trial_rows: list[dict[str, Any]],
    run_rows: list[dict[str, Any]],
    input_root: Path,
    split: str,
) -> dict[str, Any]:
    if split not in {"development", "validation"}:
        raise ValueError("Calibration metrics cannot include held-out rows.")
    run_index = {row["trial_id"]: row for row in run_rows}
    all_trials: list[dict[str, Any]] = []
    for trial in trial_rows:
        if trial["split"] != split:
            continue
        trial_dir = folder(input_root, split, trial)

        def read(name: str) -> dict[str, Any] | None:
            return _read_json(trial_dir / name)

        row = build_trial_metrics(
            trial,
            run_state=run_index.get(trial["trial_id"]),
            estimate=read("estimate.json"),
            telemetry=read("estimate.json.telemetry.json"),
            evaluation=read("evaluation.json"),
            truth=read("truth.json"),
            support=read("support.json"),
        )
        all_trials.append(row)
    return {
        "schema_version": 1,
        "split": split,
        "frozen": False,
        "trials": [row for row in all_trials if row["supported"]],
        "all_trials": all_trials,
        "trial_count": len(all_trials),
        "supported_trial_count": sum(row["supported"] for row in all_trials),
        "unsupported_trial_count": sum(not row["supported"] for row in all_trials),
        "evidence_status_counts": _count_status(all_trials),
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def _count_status(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row["evidence_status"])
        counts[status] = counts.get(status, 0) + 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--split", choices=("development", "validation"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    trials = _read_table(args.plan.parent / plan["trial_table"])
    run_path = args.run_root / "trials.csv"
    run_rows = _read_table(run_path) if run_path.is_file() else []
    document = build_document(trials, run_rows, args.input_root, args.split)
    document["plan_sha256"] = hfile(args.plan)
    document["trial_table_sha256"] = plan.get("trial_table_sha256")
    document["run_state_sha256"] = hfile(run_path) if run_path.is_file() else None
    document["run_root"] = str(args.run_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                "split": args.split,
                "trial_count": document["trial_count"],
                "supported_trial_count": document["supported_trial_count"],
                "evidence_status_counts": document["evidence_status_counts"],
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
