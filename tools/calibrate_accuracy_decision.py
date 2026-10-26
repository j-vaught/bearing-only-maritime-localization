"""Select thresholds on development and confirm them on validation before freezing."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from bearing_localization.search.calibration import RADIUS_LIMITS, SCORE_GAPS, canonical_json


def _eligible(trial: dict[str, Any]) -> bool:
    return (
        trial.get("search_status") == "completed"
        and trial.get("distinct_matched_groups", 0) >= 3
        and trial.get("ellipse_rank") == 2
        and trial.get("meaningful_distant_coverage") is True
        and isinstance(trial.get("score_gap"), (int, float))
        and isinstance(trial.get("radius_95_m"), (int, float))
        and isinstance(trial.get("error_m"), (int, float))
    )


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
    accepted = [
        row
        for row in eligible
        if row["score_gap"] >= gap and (radius is None or row["radius_95_m"] <= radius)
    ]
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
