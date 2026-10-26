from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path
from time import perf_counter
from typing import Any

from bearing_localization.search.config_serialization import read_search_config
from bearing_localization.search.scoring import CandidateScorer
from bearing_localization.search.visibility_screen import (
    _screen_candidate_visibility_batched,
    _screen_candidate_visibility_scalar,
)
from maritime_localization_shared.contracts.inputs_v2 import (
    CatalogV2,
    MapV2,
    MeasurementSetV2,
)
from maritime_localization_shared.geospatial.vector_map import VectorMap
from maritime_localization_shared.serialization import read_contract

ROOT = Path("/home/jvaught2/projects/bearing-only-maritime-localization")
RUN_DIR = ROOT / ".agent-work/outputs/a26-wedge-complete-07d8036"
STATIC_DIR = RUN_DIR / "static-inputs"
OUTPUT_DIR = ROOT / ".agent-work/outputs/a14-screen"
EXPECTED_HASHES = {
    "catalog": "2554a1f6e574b6b9ffdefe6bb7b96636d756c9cbd87018be8f7e078738227704",
    "map": "cce049c5911b39072d30f26c09947782a78e350e65a3f69c6923312fba3f9d93",
    "measurements": "ed1145cee93d4f06ffa983f671319dee49e9a95cb4692a2dd14a44c4a8eeceb1",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _summary(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "median_ms": statistics.median(values) * 1e3,
        "p95_ms": ordered[int(0.95 * (len(ordered) - 1))] * 1e3,
        "mean_ms": statistics.mean(values) * 1e3,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare scalar and batched A14 full-screen visibility on saved proposals."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=128,
        help="Number of saved hypotheses to profile, in saved order; use 0 for all.",
    )
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    if args.limit < 0 or args.repeats < 1:
        parser.error("--limit must be nonnegative and --repeats must be positive.")

    paths = {
        "catalog": STATIC_DIR / "catalog.json",
        "map": STATIC_DIR / "map.json",
        "measurements": STATIC_DIR / "measurements.json",
    }
    hashes = {name: _sha256(path) for name, path in paths.items()}
    if hashes != EXPECTED_HASHES:
        raise SystemExit(f"Frozen input hashes do not match the expected set: {hashes}")

    measurements = read_contract(paths["measurements"], MeasurementSetV2)
    catalog = read_contract(paths["catalog"], CatalogV2)
    map_v2 = read_contract(paths["map"], MapV2)
    config = read_search_config(RUN_DIR / "run/search-config.json")
    hypotheses: list[dict[str, Any]] = json.loads((RUN_DIR / "run/estimate.json").read_text())[
        "hypotheses"
    ]
    if args.limit:
        hypotheses = hypotheses[: args.limit]
    positions = [(float(row["east_m"]), float(row["north_m"])) for row in hypotheses]

    vector_map = VectorMap.from_map_v2(map_v2, target_crs="EPSG:32618")
    scorer = CandidateScorer(measurements, catalog, vector_map, config)
    context = scorer._prediction_context
    times = {index: {"scalar": [], "batch": []} for index in range(len(positions))}
    ray_counts: dict[int, int] = {}

    for position in positions[:4]:
        _screen_candidate_visibility_scalar(position, context, scan_all_members=True)
        _screen_candidate_visibility_batched(position, context, scan_all_members=True)

    for repeat in range(args.repeats):
        for index, position in enumerate(positions):
            functions = (
                (
                    ("scalar", _screen_candidate_visibility_scalar),
                    ("batch", _screen_candidate_visibility_batched),
                )
                if (index + repeat) % 2 == 0
                else (
                    ("batch", _screen_candidate_visibility_batched),
                    ("scalar", _screen_candidate_visibility_scalar),
                )
            )
            results = {}
            for name, function in functions:
                started = perf_counter()
                results[name] = function(position, context, scan_all_members=True)
                times[index][name].append(perf_counter() - started)
            if results["scalar"] != results["batch"]:
                raise AssertionError(f"Scalar/batch result mismatch at candidate rank {index + 1}.")
            ray_counts[index] = results["scalar"].ray_check_count

    rows = []
    for index, hypothesis in enumerate(hypotheses):
        scalar_ms = statistics.median(times[index]["scalar"]) * 1e3
        batch_ms = statistics.median(times[index]["batch"]) * 1e3
        rows.append(
            {
                "rank": index + 1,
                "east_m": float(hypothesis["east_m"]),
                "north_m": float(hypothesis["north_m"]),
                "ray_count": ray_counts[index],
                "scalar_ms": scalar_ms,
                "batch_ms": batch_ms,
                "speedup": scalar_ms / batch_ms,
            }
        )

    thresholds = []
    for threshold in (1, 2, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 256):
        selected = [row for row in rows if row["ray_count"] >= threshold]
        thresholds.append(
            {
                "minimum_rays": threshold,
                "candidate_count": len(selected),
                "scalar_total_ms": sum(row["scalar_ms"] for row in selected),
                "batch_total_ms": sum(row["batch_ms"] for row in selected),
            }
        )

    result = {
        "source": "saved estimate hypotheses in saved order; no truth input",
        "input_sha256": hashes,
        "catalog_records": len(catalog.records),
        "candidate_count": len(positions),
        "repeats_per_candidate": args.repeats,
        "exact_result_parity_pairs": len(positions) * args.repeats,
        "scalar": _summary([x for row in times.values() for x in row["scalar"]]),
        "batch": _summary([x for row in times.values() for x in row["batch"]]),
        "rows": rows,
        "ray_threshold_summaries": thresholds,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "profile_crossover.json"
    output_path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
