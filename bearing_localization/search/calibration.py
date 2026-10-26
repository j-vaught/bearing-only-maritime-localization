"""Immutable, hash-verified development calibration parameters."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCORE_GAPS = (0.0, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0)
RADIUS_LIMITS: tuple[float | None, ...] = (25.0, 50.0, 75.0, 100.0, 150.0, 200.0, None)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@dataclass(frozen=True, slots=True)
class DecisionCalibration:
    """Frozen selection produced from development trials only."""

    min_score_gap: float
    max_95_radius_m: float | None
    split: str
    status: str
    availability: float
    conditional_accuracy: float
    accepted_p95_error_m: float | None
    artifact_sha256: str
    schema_version: int = 1
    verified_frozen_artifact: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != 1 or self.split != "development":
            raise ValueError("Calibration must be schema v1 and development-only.")
        if self.min_score_gap not in SCORE_GAPS or self.max_95_radius_m not in RADIUS_LIMITS:
            raise ValueError("Calibration thresholds are outside the frozen grid.")
        if self.status not in ("PASS", "FAIL"):
            raise ValueError("Calibration status must be PASS or FAIL.")
        if not 0.0 <= self.availability <= 1.0 or not 0.0 <= self.conditional_accuracy <= 1.0:
            raise ValueError("Calibration rates must be within [0, 1].")
        if len(self.artifact_sha256) != 64:
            raise ValueError("Calibration artifact must carry a SHA-256 hash.")


def calibration_payload(calibration: DecisionCalibration) -> dict[str, Any]:
    return {
        "schema_version": calibration.schema_version,
        "split": calibration.split,
        "status": calibration.status,
        "thresholds": {
            "min_score_gap": calibration.min_score_gap,
            "max_95_radius_m": calibration.max_95_radius_m,
        },
        "metrics": {
            "availability": calibration.availability,
            "conditional_accuracy": calibration.conditional_accuracy,
            "accepted_p95_error_m": calibration.accepted_p95_error_m,
        },
    }


def load_frozen_calibration(path: str | Path) -> DecisionCalibration:
    """Load and verify a frozen calibration; validation/held-out files are rejected."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        document.get("split") != "development"
        or document.get("frozen") is not True
        or document.get("validation_confirmed") is not True
        or not isinstance(document.get("validation_metrics"), dict)
    ):
        raise ValueError(
            "Runtime calibration must be frozen, dev-selected, and validation-confirmed."
        )
    payload = {key: value for key, value in document.items() if key not in ("frozen", "sha256")}
    actual = hashlib.sha256(canonical_json(payload)).hexdigest()
    if document.get("sha256") != actual:
        raise ValueError("Frozen calibration SHA-256 verification failed.")
    thresholds = document["thresholds"]
    metrics = document["metrics"]
    return DecisionCalibration(
        min_score_gap=thresholds["min_score_gap"],
        max_95_radius_m=thresholds["max_95_radius_m"],
        split=document["split"],
        status=document["status"],
        availability=metrics["availability"],
        conditional_accuracy=metrics["conditional_accuracy"],
        accepted_p95_error_m=metrics["accepted_p95_error_m"],
        artifact_sha256=actual,
        schema_version=document["schema_version"],
        verified_frozen_artifact=True,
    )
