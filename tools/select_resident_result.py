"""Freeze a resident-search estimate without evaluator truth or new search work."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from pyproj import Transformer

from maritime.search.gpu.resident.cache import file_digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("search", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--device", type=int, default=3)
    args = parser.parse_args()
    if args.device != 3:
        parser.error("This bounded check is restricted to GPU3")
    if args.output.exists():
        parser.error("Estimate output already exists; estimates are frozen once written")
    summary = json.loads((args.search / "summary.json").read_text())
    if not summary["complete"]:
        raise RuntimeError("The search region is incomplete; no completed-region estimate")
    manifest = json.loads((args.search / "manifest.json").read_text())
    reports = len(manifest["marker_bearings_deg"])
    if reports == 0:
        raise ValueError("Positive chart-aid observations are required")
    arrays: dict[str, list[np.ndarray]] = {
        key: [] for key in ("cells", "status", "flags", "residual_deg", "matched_records")
    }
    digest = hashlib.sha256()
    for path in sorted(args.search.rglob("leaf-*.npz")):
        digest.update(str(path.relative_to(args.search)).encode())
        digest.update(bytes.fromhex(file_digest(path)))
        with np.load(path, allow_pickle=False) as data:
            for key in arrays:
                arrays[key].append(data[key])
    import cupy as cp

    inverse = Transformer.from_crs("EPSG:32618", "EPSG:4326", always_xy=True)
    with cp.cuda.Device(args.device):
        data = {key: cp.asarray(np.concatenate(parts)) for key, parts in arrays.items()}
        cells, costs, ids = data["cells"], data["residual_deg"], data["matched_records"]
        center = (cells[:, :2] + cells[:, 2:]) * 0.5
        complete = cp.all((ids >= 0) & (ids < len(manifest["object_keys"])), axis=1)
        if reports > 1:
            complete &= cp.all(cp.diff(cp.sort(ids, axis=1), axis=1) != 0, axis=1)
        complete &= cp.isfinite(costs)
        consistent = complete & (data["status"] == 0) & (data["flags"] == 0)

        def choose(mask):
            if not bool(cp.any(mask).item()):
                return None
            index = int(
                cp.lexsort(cp.stack((center[:, 1], center[:, 0], cp.where(mask, costs, cp.inf))))[
                    0
                ].item()
            )
            east, north = map(float, cp.asnumpy(center[index]))
            longitude, latitude = inverse.transform(east, north)
            matches = cp.asnumpy(ids[index]).tolist()
            return {
                "east_m": east,
                "north_m": north,
                "longitude_deg": longitude,
                "latitude_deg": latitude,
                "sum_absolute_bearing_residual_deg": float(costs[index].item()),
                "status": int(data["status"][index].item()),
                "flags": int(data["flags"][index].item()),
                "matched_positive_markers": len(matches),
                "matched_object_keys": [manifest["object_keys"][i] for i in matches],
            }

        result = {
            "schema": "ResidentBlindEstimateV1",
            "created_utc": datetime.now(UTC).isoformat(),
            "selection_rule": "Minimum sum absolute residual; ties east then north",
            "full_region_search_complete": True,
            "search_summary": summary,
            "positive_marker_reports": reports,
            "terminal_centers": len(cells),
            "consistent_centers": int(cp.count_nonzero(consistent).item()),
            "fully_matched_centers_including_unresolved": int(cp.count_nonzero(complete).item()),
            "status_counts": {
                str(k): int(cp.count_nonzero(data["status"] == k).item()) for k in range(4)
            },
            "estimate": choose(consistent),
            "diagnostic_best_complete_assignment": choose(complete),
            "manifest_sha256": file_digest(args.search / "manifest.json"),
            "summary_sha256": file_digest(args.search / "summary.json"),
            "all_leaf_outputs_sha256": digest.hexdigest(),
            "selector_source_sha256": file_digest(Path(__file__)),
            "truth_used_for_selection": False,
            "selection_device": args.device,
            "cpu_roles": ["read_saved_outputs", "format_selected_coordinates", "write_estimate"],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(
        json.dumps(
            {key: result[key] for key in ("terminal_centers", "consistent_centers", "estimate")}
        )
    )


if __name__ == "__main__":
    main()
