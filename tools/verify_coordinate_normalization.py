"""Check 20 cm identity grouping against saved coordinates without localization."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from maritime.catalog_identity import (
    COORDINATE_MERGE_RADIUS_M,
    RULE,
    canonicalize_chart_records,
    chart_coordinate_groups,
)
from maritime.schemas.inputs_v2 import CatalogRecordV2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = root / "docs/verification/b-location-analysis/aid-clustering-nearcoincident.json"
    saved = json.loads(source.read_text())
    results = []
    total = 0
    for site in saved["sites"]:
        pairs = site["pairs_below_1m"]
        positions = {}
        for pair in pairs:
            positions[pair["first_key"]] = (pair["first_latitude_deg"], pair["first_longitude_deg"])
            positions[pair["second_key"]] = (
                pair["second_latitude_deg"],
                pair["second_longitude_deg"],
            )
        records = tuple(
            CatalogRecordV2(key, lat, lon, raw_type="physical_aid")
            for key, (lat, lon) in sorted(positions.items())
        )
        groups = chart_coordinate_groups(records)
        owner = {records[i].key: group for group, members in enumerate(groups) for i in members}
        audited = [pair for pair in pairs if pair["distance_m"] <= COORDINATE_MERGE_RADIUS_M]
        resolved = sum(owner[pair["first_key"]] == owner[pair["second_key"]] for pair in audited)
        if resolved != len(audited):
            raise AssertionError(f"Saved duplicate pair remains separate at {site['site_id']}")
        canonical = canonicalize_chart_records(records)
        if canonicalize_chart_records(canonical) != canonical:
            raise AssertionError("Grouping must remain stable when repeated")
        if canonicalize_chart_records(reversed(records)) != canonical:
            raise AssertionError("Grouping must not depend on input order")
        total += resolved
        results.append(
            {"site_id": site["site_id"], "saved_pairs": len(audited), "merged_pairs": resolved}
        )
    # The requested correction must not touch any warning or certification source.
    protected = [
        "maritime/search/gpu/resident/engine.py",
        "maritime/search/gpu/resident/parallel_prediction.py",
        "maritime/search/gpu/resident/parallel_prediction.cu",
        "maritime/search/gpu/resident/distinct_pruning.py",
        "tools/select_resident_result.py",
    ]
    hashes = {}
    for name in protected:
        current = (root / name).read_bytes()
        prior = subprocess.run(
            ["git", "show", f"HEAD:{name}"], cwd=root, check=True, capture_output=True
        ).stdout
        if current != prior:
            raise AssertionError(f"Protected warning/certification source changed: {name}")
        hashes[name] = hashlib.sha256(current).hexdigest()
    report = {
        "author": "J.C. Vaught",
        "rule": RULE,
        "merge_radius_m": COORDINATE_MERGE_RADIUS_M,
        "scope": "Saved-coordinate grouping only; no simulation, scoring, search, or cache rebuild",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "saved_pairs_checked": total,
        "saved_pairs_merged": total,
        "sites": results,
        "warning_and_certification_sources_unchanged": hashes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Merged all {total} saved duplicate pairs. Warning and certification sources unchanged.")


if __name__ == "__main__":
    main()
