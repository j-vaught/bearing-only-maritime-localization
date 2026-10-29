"""Combine preserved suite records with explicit unsearched-site retries. J.C. Vaught."""

import argparse
import csv
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("original", type=Path)
parser.add_argument("retry", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
original, retry, output = args.original.resolve(), args.retry.resolve(), args.output.resolve()
assert read(original / "status.json")["stage"] == "finished"
assert read(retry / "status.json")["stage"] == "finished"
first_manifest, retry_manifest = read(original / "manifest.json"), read(retry / "manifest.json")
first_provenance, retry_provenance = first_manifest["provenance"], retry_manifest["provenance"]
for key in (
    "settings",
    "simulation",
    "seed_policy",
    "observers_sha256",
    "map_sha256",
    "catalog_sha256",
    "search_timeout_s",
    "selection_timeout_s",
    "terminal_ceiling",
    "evaluated_ceiling",
    "minimum_positive_aids",
):
    assert first_provenance[key] == retry_provenance[key], key
for name, value in first_provenance["source_hashes"].items():
    if name.startswith("maritime/") or name == "tools/select_resident_result.py":
        assert retry_provenance["source_hashes"][name] == value, name
for root in (original, retry):
    assert read(root / "cache-before.json") == read(root / "cache-after.json")
assert read(original / "cache-before.json") == read(retry / "cache-after.json")
initial = read(original / "results.json")
replacements = {row["site_id"]: row for row in read(retry / "results.json")}
expected_retries = {
    row["site_id"] for row in initial if row.get("reason", "").startswith("Required GPU busy;")
}
assert set(replacements) == expected_retries
records = []
for row in initial:
    replacement = row["site_id"] in replacements
    selected = dict(replacements.get(row["site_id"], row))
    selected["result_source_root"] = str(retry if replacement else original)
    records.append(selected)
assert len(records) == 24
output.mkdir(parents=True, exist_ok=False)
save(output / "results.json", records)
with (original / "results.csv").open() as stream:
    fieldnames = list(csv.DictReader(stream).fieldnames or []) + ["result_source_root"]
with (output / "results.csv").open("w") as stream:
    writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(records)
completed = [row for row in records if row["status"] == "completed"]
searched = [row for row in records if row.get("search_s") is not None]
errors = [row["position_error_m"] for row in completed]
aggregate = {
    "author": "J.C. Vaught",
    "created_utc": datetime.now(UTC).isoformat(),
    "recorded_sites": 24,
    "all24_recorded": True,
    "minimum_positive_aids": 16,
    "eligible_frames": sum(row.get("positive_marker_reports", 0) >= 16 for row in records),
    "attempted_searches": len(searched),
    "status_counts": dict(Counter(row["status"] for row in records)),
    "successful_estimates": len(completed),
    "mean_search_s_all_searched": mean(row["search_s"] for row in searched),
    "mean_error_m_successful_estimates": mean(errors) if errors else None,
    "median_error_m_successful_estimates": median(errors) if errors else None,
    "max_error_m_successful_estimates": max(errors) if errors else None,
    "shared_preparation_s_original": read(original / "shared-preparation.json")[
        "shared_preparation_s"
    ],
    "shared_preparation_s_retry": read(retry / "shared-preparation.json")["shared_preparation_s"],
    "timing_note": "Search time excludes simulation, preparation, selection and accuracy",
    "original_root": str(original),
    "retry_root": str(retry),
    "retry_sites": sorted(expected_retries, key=lambda site: int(site[1:])),
    "estimator_simulator_and_scientific_settings_identical": True,
    "prior_stopped_B1_root": str(original.parent / "b-location-suite-20261029"),
}
save(output / "aggregate.json", aggregate)
combined_manifest = dict(first_manifest)
combined_manifest["combined_sources"] = {
    "original": str(original),
    "retry": str(retry),
    "merge_rule": "Replace only unsearched telemetry-guard cases; retain all other records",
}
save(output / "manifest.json", combined_manifest)
save(output / "source-manifests/original.json", first_manifest)
save(output / "source-manifests/retry.json", retry_manifest)
save(output / "cache-before.json", read(original / "cache-before.json"))
save(output / "cache-after.json", read(retry / "cache-after.json"))
save(
    output / "aid-counts-and-eligibility.json",
    {
        "minimum_positive_aids": 16,
        "sites": [
            {
                "site_id": row["site_id"],
                "positive_marker_reports": row.get("positive_marker_reports"),
                "eligible": row.get("positive_marker_reports", 0) >= 16,
                "status": row["status"],
            }
            for row in records
        ],
    },
)
save(
    output / "status.json",
    {
        "stage": "finished",
        "all24_recorded": True,
        "cache_unchanged": True,
        "updated_utc": datetime.now(UTC).isoformat(),
    },
)
print(json.dumps(aggregate, indent=2))
