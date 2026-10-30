"""Measure WGS84 error after a resident estimate has been frozen."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from pyproj import Geod


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluate_position(
    estimate_path: Path, freeze_path: Path, truth_path: Path, output: Path
) -> dict:
    if output.exists():
        raise FileExistsError("Evaluation already exists")
    freeze = json.loads(freeze_path.read_text())
    digest = file_digest(estimate_path)
    if digest != freeze["estimate_sha256"]:
        raise ValueError("Estimate changed after freezing")
    estimate = json.loads(estimate_path.read_text())
    if not estimate["full_region_search_complete"] or estimate["truth_used_for_selection"]:
        raise ValueError("A completed, truth-independent selection is required")
    selected = estimate["estimate"]
    if selected is None or selected["status"] != 0 or selected["flags"] != 0:
        raise ValueError("No numerically consistent selected location is available")
    # The first evaluator read of the truth occurs after the frozen estimate
    # has been validated. Nothing here changes, ranks or refines candidates.
    truth = json.loads(truth_path.read_text())
    azimuth, _, distance = Geod(ellps="WGS84").inv(
        truth["longitude_deg"],
        truth["latitude_deg"],
        selected["longitude_deg"],
        selected["latitude_deg"],
    )
    result = {
        "evaluated_utc": datetime.now(UTC).isoformat(),
        "estimate_frozen_utc": freeze["frozen_utc"],
        "estimate_sha256": digest,
        "truth_sha256": file_digest(truth_path),
        "method": "WGS84 ellipsoidal geodesic distance; no fitted alignment",
        "position_error_m": distance,
        "bearing_truth_to_estimate_deg": azimuth,
        "estimated_latitude_deg": selected["latitude_deg"],
        "estimated_longitude_deg": selected["longitude_deg"],
        "true_latitude_deg": truth["latitude_deg"],
        "true_longitude_deg": truth["longitude_deg"],
        "matched_positive_markers": selected["matched_positive_markers"],
        "status": selected["status"],
        "flags": selected["flags"],
        "scope": "Single corrected simulated frame, completed declared search domain",
        "truth_used_to_select_or_refine": False,
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("estimate", "freeze", "truth", "output"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate_position(args.estimate, args.freeze, args.truth, args.output)))


if __name__ == "__main__":
    main()
