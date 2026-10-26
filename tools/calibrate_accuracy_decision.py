"""Select and freeze an accuracy decision threshold grid from dev-only trials."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from bearing_localization.search.calibration import (
    RADIUS_LIMITS,
    SCORE_GAPS,
    canonical_json,
)


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


def calibrate(document: dict[str, Any]) -> dict[str, Any]:
    """Calibrate grid thresholds; input rows must be development split only."""
    if document.get("split") != "development":
        raise ValueError("Calibration input must be development split; held-out data is forbidden.")
    if document.get("frozen") is True:
        raise ValueError("Calibration input cannot be a previously frozen artifact.")
    trials = document.get("trials")
    if not isinstance(trials, list) or not trials:
        raise ValueError("Development calibration requires a nonempty trials list.")
    if any(
        not isinstance(row, dict) or row.get("split", "development") != "development"
        for row in trials
    ):
        raise ValueError("Every trial must be explicitly development-only.")
    support_n = len(trials)
    eligible = [row for row in trials if _eligible(row)]
    candidates: list[dict[str, Any]] = []
    for gap in SCORE_GAPS:
        for radius in RADIUS_LIMITS:
            accepted = [
                row
                for row in eligible
                if row["score_gap"] >= gap and (radius is None or row["radius_95_m"] <= radius)
            ]
            n = len(accepted)
            correct = sum(row["error_m"] <= 100.0 for row in accepted)
            accuracy = correct / n if n else 0.0
            availability = n / support_n
            errors = sorted(float(row["error_m"]) for row in accepted)
            p95 = errors[min(len(errors) - 1, int(0.95 * len(errors)))] if errors else None
            candidates.append(
                {
                    "gap": gap,
                    "radius": radius,
                    "accepted": n,
                    "availability": availability,
                    "accuracy": accuracy,
                    "p95": p95,
                }
            )
    accurate = [
        candidate
        for candidate in candidates
        if candidate["accepted"] and candidate["accuracy"] >= 0.95
    ]
    pool = accurate if accurate else candidates
    chosen = max(
        pool,
        key=lambda c: (
            c["availability"],
            -(c["p95"] if c["p95"] is not None else float("inf")),
            c["gap"],
            -(c["radius"] if c["radius"] is not None else float("inf")),
        ),
    )
    status = "PASS" if chosen["availability"] >= 0.90 and chosen["accuracy"] >= 0.95 else "FAIL"
    payload = {
        "schema_version": 1,
        "split": "development",
        "status": status,
        "thresholds": {"min_score_gap": chosen["gap"], "max_95_radius_m": chosen["radius"]},
        "metrics": {
            "availability": chosen["availability"],
            "conditional_accuracy": chosen["accuracy"],
            "accepted_p95_error_m": chosen["p95"],
            "supported_trials": support_n,
            "eligible_trials": len(eligible),
            "accepted_trials": chosen["accepted"],
            "accuracy_gate": 0.95,
            "availability_gate": 0.90,
        },
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
    args = parser.parse_args()
    source = json.loads(args.development_metrics.read_text(encoding="utf-8"))
    artifact = calibrate(source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": artifact["status"],
                "sha256": artifact["sha256"],
                "thresholds": artifact["thresholds"],
                "metrics": artifact["metrics"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
