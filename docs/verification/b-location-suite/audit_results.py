"""Audit saved suite records without new localization work. J.C. Vaught."""

import argparse
import csv
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path

from pyproj import Geod


def read(path):
    return json.loads(path.read_text())


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("output", type=Path)
args = parser.parse_args()
output = args.output.resolve()
repo = Path(__file__).resolve().parents[3]
records = read(output / "results.json")
manifest = read(output / "manifest.json")
assert read(output / "status.json")["stage"] == "finished"
assert len(records) == 24
assert [row["site_id"] for row in records] == [f"B{i}" for i in range(1, 25)]
assert manifest["provenance"]["minimum_positive_aids"] == 16
assert read(output / "cache-before.json") == read(output / "cache-after.json")
with (repo / "data/observer_locations.csv").open() as stream:
    observers = list(csv.DictReader(stream))
geod = Geod(ellps="WGS84")
audited = []
for row, observer in zip(records, observers, strict=True):
    site = row["site_id"]
    directory = Path(row["attempt_directory"])
    assert row["latitude_deg"] == observer["latitude_deg"]
    assert row["longitude_deg"] == observer["longitude_deg"]
    search = directory / "search"
    if row["status"] == "skipped_aid_count":
        assert row["positive_marker_reports"] <= 15
        assert not search.exists()
    if search.exists():
        assert row["positive_marker_reports"] >= 16
        search_manifest = read(search / "manifest.json")
        assert len(search_manifest["marker_bearings_deg"]) == row["positive_marker_reports"]
        for key, value in manifest["provenance"]["settings"]["search"].items():
            assert search_manifest["config"][key] == value
        assert not read(directory / "process-cleanup.json")["live_group_members"]
    if row["status"] == "completed":
        freeze = read(directory / "estimate-freeze.json")
        estimate = read(directory / "estimate.json")
        assert digest(directory / "estimate.json") == freeze["estimate_sha256"]
        assert estimate["full_region_search_complete"]
        assert not estimate["truth_used_for_selection"]
        selected = estimate["estimate"]
        assert selected["status"] == 0 and selected["flags"] == 0
        assert selected["matched_positive_markers"] == row["positive_marker_reports"]
        truth = read(directory / "truth.json")
        _, _, error = geod.inv(
            truth["longitude_deg"],
            truth["latitude_deg"],
            selected["longitude_deg"],
            selected["latitude_deg"],
        )
        assert math.isclose(error, row["position_error_m"], rel_tol=0, abs_tol=1e-9)
        assert sum(estimate["status_counts"].values()) == row["terminal"]
        assert read(search / "summary.json")["completed_roots"] == 39435
    audited.append({"site_id": site, "status": row["status"], "passed": True})
result = {
    "author": "J.C. Vaught",
    "audited_utc": datetime.now(UTC).isoformat(),
    "passed": True,
    "records": audited,
    "cache_fingerprints_unchanged": True,
    "strict_more_than15_aids_verified": True,
    "completed_estimates_and_geodesic_errors_verified": True,
    "authoritative_coordinates_preserved": True,
    "all_searched_configs_match_saved_full_coast_reference": True,
    "new_searches_run_for_audit": 0,
}
(output / "saved-output-audit.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
