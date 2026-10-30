"""Fit log search time to detected aid count using all completed sites. J.C. Vaught."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent
    source = root / "data.json"
    rows = json.loads(source.read_text())["rows"]
    rows = [row for row in rows if row["status"] == "completed"]
    x = [row["aids"] for row in rows]
    y = [math.log(row["search_s"]) for row in rows]
    slope, intercept = statistics.linear_regression(x, y)
    predicted = [intercept + slope * value for value in x]
    residual_ss = sum((a - b) ** 2 for a, b in zip(y, predicted, strict=True))
    total_ss = sum((value - statistics.mean(y)) ** 2 for value in y)
    # Independently check the slope using centered sums rather than the library fit.
    centered_slope = sum(
        (a - statistics.mean(x)) * (b - statistics.mean(y)) for a, b in zip(x, y, strict=True)
    ) / sum((value - statistics.mean(x)) ** 2 for value in x)
    assert math.isclose(slope, centered_slope, abs_tol=1e-12)
    domain = [min(x) + (max(x) - min(x)) * index / 200 for index in range(201)]
    crossing = (math.log(8) - intercept) / slope
    if min(x) < crossing < max(x):
        domain.append(crossing)
    points = [[value, math.exp(intercept + slope * value)] for value in sorted(domain)]
    result = {
        "author": "J.C. Vaught",
        "model": "ln(search_time_seconds) = intercept + slope * detected_aids",
        "method": "Unweighted ordinary least squares on natural-log search time",
        "n": len(rows),
        "sites": [row["site"] for row in rows],
        "intercept": intercept,
        "slope": slope,
        "multiplier_seconds": math.exp(intercept),
        "r_squared_log_time": 1 - residual_ss / total_ss,
        "source_data_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "domain_aids": [min(x), max(x)],
        "points": points,
        "detail_points": [point for point in points if point[1] <= 8 + 1e-12],
        "interpretation": "Descriptive fit across sites, evaluated only within observed aid counts",
    }
    (root / "time-aids-fit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("n", "intercept", "slope", "multiplier_seconds", "r_squared_log_time")
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
