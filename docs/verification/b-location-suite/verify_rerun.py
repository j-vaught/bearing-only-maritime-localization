"""Audit a saved eight-site normalization rerun without more searches. J.C. Vaught."""

import argparse
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def fingerprint(path):
    data = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            data.update(block)
    return {
        "sha256": data.hexdigest(),
        "size": path.stat().st_size,
        "mtime_ns": path.stat().st_mtime_ns,
    }


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("output", type=Path)
parser.add_argument("previous", type=Path)
args = parser.parse_args()
output, previous = args.output.resolve(), args.previous.resolve()
manifest = read(output / "manifest.json")
new = manifest["provenance"]
old = read(previous / "manifest.json")["provenance"]
expected = ["B2", "B6", "B7", "B18", "B19", "B20", "B21", "B22"]
assert manifest["suite_order"] == expected
assert read(output / "status.json")["stage"] == "finished"
assert read(output / "saved-output-audit.json")["passed"]
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
    assert new[key] == old[key], key
allowed = {"maritime/catalog_identity.py", "maritime/search/gpu/resident/aid_view.py"}
changed = {
    name
    for name, value in old["source_hashes"].items()
    if name.startswith("maritime/") and new["source_hashes"][name] != value
}
assert changed == allowed, changed
assert (
    new["source_hashes"]["tools/select_resident_result.py"]
    == old["source_hashes"]["tools/select_resident_result.py"]
)
assert read(output / "normalized-catalog-alignment.json")["passed"]
assert read(output / "normalized-catalog-alignment.json")["merge_radius_m"] == 0.20
assert read(output / "cache-before.json") == read(output / "cache-after.json")
new_cache = read(output / "cache-after.json")
old_cache = read(previous / "cache-before.json")
for name, value in old_cache.items():
    current = new_cache[name] if name in new_cache else fingerprint(Path(name))
    assert current == value, name
shared = read(output / "shared-preparation.json")
assert shared["base_cache_hit"]
base_path = Path(shared["cache_paths"][0])
assert str(base_path) in old_cache
assert base_path.name == shared["base_cache_key"] + ".npz"

rows = read(output / "results.json")
assert [row["site_id"] for row in rows] == expected
summaries = []
for row in rows:
    attempt = Path(row["attempt_directory"])
    devices = sorted(
        {int(p.name.rsplit("-", 1)[1]) for p in (attempt / "search").glob("roots-*-gpu-*")}
    )
    assert devices == [0, 1, 2], (row["site_id"], devices)
    summaries.append(
        {
            "site_id": row["site_id"],
            "status": row["status"],
            "search_s": row.get("search_s"),
            "position_error_m": row.get("position_error_m"),
            "devices_with_saved_root_outputs": devices,
        }
    )
report = {
    "author": "J.C. Vaught",
    "passed": True,
    "sites": summaries,
    "scientific_settings_and_seed_unchanged": True,
    "changed_production_sources": sorted(changed),
    "warning_certification_and_selection_sources_unchanged": True,
    "saved_map_cache_reused_and_unchanged": True,
    "previous_object_sidecar_unchanged": True,
    "new_object_sidecar_fingerprints_stable": True,
    "observations_regenerated_from_normalized_catalog": True,
    "searches_performed_by_audit": 0,
}
(output / "normalization-rerun-audit.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
