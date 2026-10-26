"""Score saved wedge hypotheses against evaluator-only truth coordinates."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from pyproj import Geod, Transformer


def main() -> None:
    run_root = Path(sys.argv[1]).resolve()
    truth_root = Path(sys.argv[2]).resolve()
    estimate = json.loads((run_root / "estimate.json").read_text())
    truth_crs = json.loads((truth_root / "truth-crs.json").read_text())
    truth = json.loads((truth_root / "truth.json").read_text())
    truth_to_geo = Transformer.from_crs(
        truth_crs["coordinate_reference_system"], "EPSG:4326", always_xy=True
    )
    truth_lon, truth_lat = truth_to_geo.transform(
        truth["observer_east_m"], truth["observer_north_m"]
    )
    estimate_to_geo = Transformer.from_crs("EPSG:32618", "EPSG:4326", always_xy=True)
    geod = Geod(ellps="WGS84")
    rows = []
    for index, hypothesis in enumerate(estimate["hypotheses"]):
        lon, lat = estimate_to_geo.transform(hypothesis["east_m"], hypothesis["north_m"])
        _, _, error_m = geod.inv(truth_lon, truth_lat, lon, lat)
        rows.append(
            {
                "hypothesis_index": index,
                "east_m": hypothesis["east_m"],
                "north_m": hypothesis["north_m"],
                "score": hypothesis["score"],
                "geodesic_error_m": abs(error_m),
                "assignment_count": len(hypothesis.get("assignments", [])),
                "assignments": hypothesis.get("assignments", []),
            }
        )
    result = {
        "schema_version": 1,
        "truth_crs": truth_crs["coordinate_reference_system"],
        "estimate_crs": "EPSG:32618",
        "truth_position_lon_lat_deg": [truth_lon, truth_lat],
        "method": "WGS84 geodesic after independent CRS transforms",
        "hypotheses": rows,
    }
    path = run_root / "hypothesis-errors.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "count": len(rows),
                "best_error": min(row["geodesic_error_m"] for row in rows),
                "worst_error": max(row["geodesic_error_m"] for row in rows),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    main()
