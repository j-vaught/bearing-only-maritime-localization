"""Audit completed saved outputs without additional localization searches."""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

from pyproj import Geod


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    root = Path(sys.argv[1]).resolve()

    def read(path: Path):
        return json.loads(path.read_text())

    launch = read(root / "launch.json")
    records = read(root / "results.json")
    assert {r["site_id"] for r in records} == set(launch["approved_sites"])
    assert len(records) == 20
    assert all(r["status"] == "completed" for r in records)
    before, after = read(root / "cache-before.json"), read(root / "cache-after.json")
    assert before == after
    assert all(digest(Path(name)) == values["sha256"] for name, values in before.items())
    assert read(root / "worker-cleanup.json")["live_group_members"] == []
    preload = read(root / "gpu-preload.json")
    assert preload["search_executed"] is False
    assert all(d["created"] and not d["reused"] for d in preload["devices"])
    owner_threads = {d["device"]: d["owner_thread_id"] for d in preload["devices"]}
    expected_config = read(root / "manifest.json")["provenance"]["settings"]["search"]
    checks = []
    for row in records:
        directory = Path(row["attempt_directory"])
        summary = read(directory / "search/summary.json")
        manifest = read(directory / "search/manifest.json")
        assert all(manifest["config"][key] == value for key, value in expected_config.items())
        estimate_record = read(directory / "estimate.json")
        selected = estimate_record["estimate"]
        truth = read(directory / "truth.json")
        freeze = read(directory / "estimate-freeze.json")
        meta = read(directory / "search-process-summary.json")
        assert summary["complete"] and summary["completed_roots"] == 39435
        assert manifest["cpu_query_fallback_available"] is False
        assert manifest["config"]["roots_per_task"] == 128
        assert manifest["config"]["batch_cells"] == 512
        assert manifest["config"]["terminal_size_m"] == 1.0
        assert manifest["config"]["devices"] == [0, 1, 2]
        assert row["gpu_buffers_reused"] and meta["gpu_buffers_reused"]
        assert row["search_worker_pid"] == preload["worker_pid"]
        diagnostics = summary["resident_session"]["devices"]
        assert all(d["reused"] and not d["created"] for d in diagnostics)
        assert {d["device"]: d["owner_thread_id"] for d in diagnostics} == owner_threads
        assert row["base_cache_hit"] and row["object_cache_hit"]
        assert selected["status"] == 0 and selected["flags"] == 0
        assert selected["matched_positive_markers"] == row["positive_marker_reports"]
        assert len(set(selected["matched_object_keys"])) == row["positive_marker_reports"]
        assert estimate_record["truth_used_for_selection"] is False
        assert freeze["estimate_sha256"] == digest(directory / "estimate.json")
        _, _, error = Geod(ellps="WGS84").inv(
            truth["longitude_deg"],
            truth["latitude_deg"],
            selected["longitude_deg"],
            selected["latitude_deg"],
        )
        assert math.isclose(error, row["position_error_m"], abs_tol=1e-9)
        checks.append(
            {
                "site": row["site_id"],
                "passed": True,
                "error_m": error,
                "search_s": row["search_s"],
                "all_devices_reused": True,
            }
        )
    result = {
        "passed": True,
        "approved_sites": 20,
        "accepted_estimates": 20,
        "full_coast_all_sites": True,
        "all_sites_preloaded": True,
        "same_worker_and_owner_threads": True,
        "cache_unchanged": True,
        "independent_wgs84_errors_and_frozen_hashes": True,
        "checks": checks,
        "new_searches_executed_by_audit": 0,
    }
    (root / "saved-output-audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
