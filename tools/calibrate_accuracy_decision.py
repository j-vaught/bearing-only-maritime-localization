"""Select thresholds on development and confirm them on validation before freezing."""

from __future__ import annotations

import argparse
import hashlib
import json
from math import isfinite
from pathlib import Path
from typing import Any

from bearing_localization.search.calibration import RADIUS_LIMITS, SCORE_GAPS, canonical_json
from bearing_localization.search.decision import (
    CompetitorCoverageEvidence,
    competitor_gap_clears_threshold,
    global_exclusion_evidence_is_valid,
    raw_candidate_vetoes_margin,
    score_tolerance_for_top,
)
from bearing_localization.search.modes import SearchModeEvidence, verified_mode_competitor_gap


def _value(trial: dict[str, Any], key: str, default: Any = None) -> Any:
    public_evidence = trial.get("public_evidence")
    if isinstance(public_evidence, dict) and key in public_evidence:
        return public_evidence[key]
    return trial.get(key, default)


def _score_tolerance(trial: dict[str, Any]) -> float:
    supplied = _value(trial, "score_tolerance")
    if isinstance(supplied, (int, float)) and isfinite(float(supplied)) and supplied >= 0.0:
        return float(supplied)
    top_score = _value(trial, "top_score")
    if isinstance(top_score, (int, float)) and isfinite(float(top_score)):
        return score_tolerance_for_top(float(top_score))
    # Legacy metric inputs did not carry top-score/tolerance telemetry.
    return 1e-12


def _coverage(trial: dict[str, Any]) -> CompetitorCoverageEvidence | None:
    available = _value(trial, "candidate_outcome_counts_available", True)
    if not isinstance(available, bool):
        return None
    unresolved = _value(trial, "unresolved_candidate_count", 0 if available else None)
    candidate_count = _value(trial, "evaluated_candidate_count", 0)
    unresolved_regions = _value(trial, "unresolved_region_count", 0)
    extent = _value(trial, "search_extent_m", 0.0)
    try:
        return CompetitorCoverageEvidence(
            evaluated_candidate_count=candidate_count,
            search_extent_m=extent,
            coverage_complete=_value(trial, "coverage_complete") is True,
            global_bound_excludes_outside=(_value(trial, "global_bound_excludes_outside") is True),
            unresolved_candidate_count=unresolved,
            candidate_outcome_counts_available=available,
            unresolved_region_count=unresolved_regions,
            distant_region_score_lower_bound=_value(trial, "distant_region_score_lower_bound"),
            distant_region_bound_scope=_value(trial, "distant_region_bound_scope"),
            distant_region_bound_top_east_m=_value(trial, "distant_region_bound_top_east_m"),
            distant_region_bound_top_north_m=_value(trial, "distant_region_bound_top_north_m"),
        )
    except (TypeError, ValueError):
        return None


def _global_proof_at_gap(trial: dict[str, Any], min_score_gap: float) -> bool:
    coverage = _coverage(trial)
    top_score = _value(trial, "top_score")
    tolerance = _score_tolerance(trial)
    if (
        coverage is None
        or not isinstance(top_score, (int, float))
        or not isfinite(float(top_score))
    ):
        return False
    certificate_scope = _value(
        trial, "candidate_certificate_scope", _value(trial, "certificate_scope")
    )
    bound_gap = _value(trial, "candidate_bound_gap", _value(trial, "bound_gap"))
    return global_exclusion_evidence_is_valid(
        search_status=_value(trial, "search_status", ""),
        coverage=coverage,
        certificate_scope=certificate_scope,
        bound_gap=bound_gap,
        top_score=float(top_score),
        min_score_gap=min_score_gap,
        score_tolerance=tolerance,
        top_east_m=_value(trial, "top_east_m", float("nan")),
        top_north_m=_value(trial, "top_north_m", float("nan")),
    )


def _mode_gap(trial: dict[str, Any]) -> tuple[bool, float | None]:
    """Read serialized converged modes using the runtime competitor predicate."""
    public_evidence = trial.get("public_evidence")
    has_modes = "mode_evidence" in trial or (
        isinstance(public_evidence, dict) and "mode_evidence" in public_evidence
    )
    if has_modes:
        records = _value(trial, "mode_evidence")
        if not isinstance(records, list):
            return True, None
        try:
            modes = tuple(
                SearchModeEvidence(
                    east_m=record["east_m"],
                    north_m=record["north_m"],
                    score=record["score"],
                    matched_catalog_keys=tuple(record["matched_catalog_keys"]),
                    radius_95_m=record["radius_95_m"],
                    stop_reason=record["stop_reason"],
                )
                for record in records
                if isinstance(record, dict)
            )
        except (KeyError, TypeError, ValueError):
            return True, None
        if len(modes) != len(records):
            return True, None
        top_east_m = _value(trial, "top_east_m")
        top_north_m = _value(trial, "top_north_m")
        top_score = _value(trial, "top_score")
        if not all(
            isinstance(value, (int, float)) and isfinite(float(value))
            for value in (top_east_m, top_north_m, top_score)
        ):
            return True, None
        _, gap = verified_mode_competitor_gap(
            top_east_m=float(top_east_m),
            top_north_m=float(top_north_m),
            top_score=float(top_score),
            modes=modes,
        )
        return True, gap
    meaningful = _value(trial, "meaningful_distant_coverage")
    gap = _value(trial, "score_gap")
    if meaningful is not True or not isinstance(gap, (int, float)) or not isfinite(float(gap)):
        return False, None
    # Backward-compatible metric rows predate serialized mode evidence.
    return True, float(gap)


def _has_mode_competitor(trial: dict[str, Any]) -> bool:
    available = _value(trial, "candidate_outcome_counts_available", True)
    unresolved = _value(trial, "unresolved_candidate_count", 0 if available else None)
    has_gap, score_gap = _mode_gap(trial)
    return available is True and unresolved == 0 and has_gap and score_gap is not None


def _eligible(trial: dict[str, Any]) -> bool:
    if (
        _value(trial, "search_status") != "completed"
        or _value(trial, "distinct_matched_groups", 0) < 3
        or _value(trial, "ellipse_rank") != 2
        or not isinstance(_value(trial, "radius_95_m"), (int, float))
        or not isfinite(float(_value(trial, "radius_95_m")))
        or not isinstance(_value(trial, "error_m"), (int, float))
        or not isfinite(float(_value(trial, "error_m")))
    ):
        return False
    if _has_mode_competitor(trial):
        return True
    return _global_proof_at_gap(trial, 0.0)


def _raw_candidate_vetoes(trial: dict[str, Any], min_score_gap: float) -> bool:
    public_evidence = trial.get("public_evidence")
    has_nested = isinstance(public_evidence, dict) and (
        "raw_distant_hypothesis_veto_evidence" in public_evidence
    )
    if "raw_distant_hypothesis_veto_evidence" not in trial and not has_nested:
        return False
    evidence = _value(trial, "raw_distant_hypothesis_veto_evidence")
    if not isinstance(evidence, list):
        return True
    for candidate in evidence:
        if not isinstance(candidate, dict):
            return True
        score_gap = candidate.get("score_gap")
        tolerance = candidate.get("score_tolerance")
        if (
            not isinstance(score_gap, (int, float))
            or not isfinite(float(score_gap))
            or not isinstance(tolerance, (int, float))
            or not isfinite(float(tolerance))
            or tolerance < 0.0
        ):
            return True
        if raw_candidate_vetoes_margin(float(score_gap), min_score_gap, float(tolerance)):
            return True
    return False


def _accepted_at_threshold(trial: dict[str, Any], gap: float, radius: float | None) -> bool:
    if not _eligible(trial):
        return False
    radius_95_m = float(_value(trial, "radius_95_m"))
    if radius is not None and radius_95_m > radius:
        return False
    if _raw_candidate_vetoes(trial, gap):
        return False
    if _global_proof_at_gap(trial, gap):
        return True
    if not _has_mode_competitor(trial):
        return False
    tolerance = _score_tolerance(trial)
    _, mode_gap = _mode_gap(trial)
    return mode_gap is not None and competitor_gap_clears_threshold(mode_gap, gap, tolerance)


def _check_trials(document: dict[str, Any], split: str) -> list[dict[str, Any]]:
    if document.get("split") != split or document.get("frozen") is True:
        raise ValueError(f"Metrics input must be unfrozen {split} data.")
    trials = document.get("trials")
    if not isinstance(trials, list) or not trials:
        raise ValueError(f"{split.capitalize()} metrics require a nonempty trials list.")
    if any(not isinstance(row, dict) or row.get("split", split) != split for row in trials):
        raise ValueError(f"Every trial must be explicitly {split}-only.")
    return trials


def _measure(trials: list[dict[str, Any]], gap: float, radius: float | None) -> dict[str, Any]:
    eligible = [row for row in trials if _eligible(row)]
    accepted = [row for row in eligible if _accepted_at_threshold(row, gap, radius)]
    errors = sorted(float(row["error_m"]) for row in accepted)
    count = len(accepted)
    return {
        "supported_trials": len(trials),
        "eligible_trials": len(eligible),
        "accepted_trials": count,
        "availability": count / len(trials),
        "conditional_accuracy": (
            sum(row["error_m"] <= 100.0 for row in accepted) / count if count else 0.0
        ),
        "accepted_p95_error_m": (
            errors[min(len(errors) - 1, int(0.95 * len(errors)))] if errors else None
        ),
    }


def calibrate(
    document: dict[str, Any], validation_document: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Select thresholds only on dev, then optionally confirm without retuning on validation."""
    trials = _check_trials(document, "development")
    candidates: list[dict[str, Any]] = []
    for gap in SCORE_GAPS:
        for radius in RADIUS_LIMITS:
            metrics = _measure(trials, gap, radius)
            candidates.append({"gap": gap, "radius": radius, **metrics})
    accurate = [
        candidate
        for candidate in candidates
        if candidate["accepted_trials"] and candidate["conditional_accuracy"] >= 0.95
    ]
    pool = accurate if accurate else candidates
    chosen = max(
        pool,
        key=lambda candidate: (
            candidate["availability"],
            -(
                candidate["accepted_p95_error_m"]
                if candidate["accepted_p95_error_m"] is not None
                else float("inf")
            ),
            candidate["gap"],
            -(candidate["radius"] if candidate["radius"] is not None else float("inf")),
        ),
    )
    dev_pass = chosen["availability"] >= 0.90 and chosen["conditional_accuracy"] >= 0.95
    validation_metrics = None
    validation_pass = False
    if validation_document is not None:
        validation_trials = _check_trials(validation_document, "validation")
        validation_metrics = _measure(validation_trials, chosen["gap"], chosen["radius"])
        validation_pass = (
            validation_metrics["availability"] >= 0.90
            and validation_metrics["conditional_accuracy"] >= 0.95
        )
    status = "PASS" if dev_pass and validation_pass else "FAIL"
    payload = {
        "schema_version": 1,
        "split": "development",
        "status": status,
        "thresholds": {"min_score_gap": chosen["gap"], "max_95_radius_m": chosen["radius"]},
        "metrics": {
            **{
                key: chosen[key]
                for key in (
                    "availability",
                    "conditional_accuracy",
                    "accepted_p95_error_m",
                    "supported_trials",
                    "eligible_trials",
                    "accepted_trials",
                )
            },
            "accuracy_gate": 0.95,
            "availability_gate": 0.90,
        },
        "validation_metrics": validation_metrics,
        "validation_confirmed": validation_pass,
    }
    return {
        **payload,
        "frozen": True,
        "sha256": hashlib.sha256(canonical_json(payload)).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("development_metrics", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--validation-metrics", type=Path)
    args = parser.parse_args()
    source = json.loads(args.development_metrics.read_text(encoding="utf-8"))
    validation = (
        json.loads(args.validation_metrics.read_text(encoding="utf-8"))
        if args.validation_metrics
        else None
    )
    artifact = calibrate(source, validation)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": artifact["status"],
                "sha256": artifact["sha256"],
                "thresholds": artifact["thresholds"],
                "metrics": artifact["metrics"],
                "validation_metrics": artifact["validation_metrics"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
