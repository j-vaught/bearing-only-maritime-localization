"""Summarize pre-search geometry for the frozen nominal development population.

This evaluator-only script reads prepared measurements, compact truth,
preparation diagnostics, support files, and the frozen A42 eligibility snapshot.
It never reads search output.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import shutil
import sys
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

# Make the repository tools package available when this file runs directly.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from bearing_localization.evaluation.difficulty import evaluate_scene_difficulty  # noqa: E402
from maritime_localization_shared.contracts.inputs_v2 import MeasurementSetV2  # noqa: E402
from maritime_localization_shared.contracts.truth import EvaluationTruth  # noqa: E402
from maritime_localization_shared.serialization import read_contract  # noqa: E402
from tools.accuracy_eligibility import verify_eligibility_manifest  # noqa: E402

EXPECTED_ELIGIBILITY_SHA256 = "2330090802952093bab1d5ec6cb97c6298cce27ee2050804f6eab91d66e8d050"
GEOMETRY_STRATA = ("<=25m", "25-100m", ">100m", "singular")
RANGES_M = (1000, 5000, 10000)
TYPE_MODES = (True, False)
MIN_FREE_BYTES = 40 * 1024**3


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_gzip_contents(path: Path) -> str:
    digest = hashlib.sha256()
    with gzip.open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def summarize_difficulty_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate nominal trial metadata and evaluator-only geometry diagnostics."""
    result: dict[str, Any] = {
        "nominal_trial_count": len(rows),
        "supported_trial_count": sum(row["supported"] for row in rows),
        "unsupported_trial_count": sum(not row["supported"] for row in rows),
        "geometry_strata_all": _stratum_counts(rows),
        "geometry_strata_supported": _stratum_counts([row for row in rows if row["supported"]]),
        "catalog_match_count_all": _count_summary(
            [row["difficulty"]["matched_group_count"] for row in rows]
        ),
        "catalog_match_count_supported": _count_summary(
            [row["difficulty"]["matched_group_count"] for row in rows if row["supported"]]
        ),
        "gates": [],
    }
    for range_m in RANGES_M:
        for report_types in TYPE_MODES:
            gate = [
                row
                for row in rows
                if row["range_m"] == range_m and row["report_types"] is report_types
            ]
            result["gates"].append(
                {
                    "range_m": range_m,
                    "report_types": report_types,
                    "nominal_trial_count": len(gate),
                    "supported_trial_count": sum(row["supported"] for row in gate),
                    "unsupported_trial_count": sum(not row["supported"] for row in gate),
                    "geometry_strata_all": _stratum_counts(gate),
                    "geometry_strata_supported": _stratum_counts(
                        [row for row in gate if row["supported"]]
                    ),
                    "catalog_match_count_all": _count_summary(
                        [row["difficulty"]["matched_group_count"] for row in gate]
                    ),
                    "catalog_match_count_supported": _count_summary(
                        [
                            row["difficulty"]["matched_group_count"]
                            for row in gate
                            if row["supported"]
                        ]
                    ),
                }
            )
    return result


def _stratum_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(row["difficulty"]["geometry_stratum"] for row in rows)
    return {stratum: counts.get(stratum, 0) for stratum in GEOMETRY_STRATA}


def _count_summary(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "median": None, "max": None}
    return {
        "count": len(values),
        "min": min(values),
        "median": median(values),
        "max": max(values),
    }


def _same_radius(actual: float | None, expected: object) -> bool:
    if actual is None or expected is None:
        return actual is None and expected is None
    return isinstance(expected, (int, float)) and math.isclose(
        actual, float(expected), rel_tol=1e-12, abs_tol=1e-12
    )


def _trial_dir(input_root: Path, row: dict[str, Any]) -> Path:
    type_mode = "types-on" if row["report_types"] else "types-off"
    return (
        input_root
        / "development"
        / row["port_id"]
        / row["placement_id"]
        / f"r{row['realization']}"
        / f"{row['range_m']}m"
        / type_mode
        / row["sensor_condition"]
    )


def load_development_summary(
    *,
    eligibility_manifest: Path,
    plan_dir: Path,
    input_root: Path,
    preparation_csv: Path,
) -> dict[str, Any]:
    """Verify frozen inputs and compute geometry for every nominal row."""
    if shutil.disk_usage(input_root).free < MIN_FREE_BYTES:
        raise RuntimeError("Refusing analysis while free disk is below 40 GiB.")
    manifest_sha = sha256_file(eligibility_manifest)
    if manifest_sha != EXPECTED_ELIGIBILITY_SHA256:
        raise ValueError(
            "Frozen A42 manifest SHA-256 differs from the authorized development manifest."
        )
    if (
        sha256_file(preparation_csv)
        != "f84072c6cd77af4dfebece37632d80a6675880be0ab8607636be37f27cab1928"
    ):
        raise ValueError("Preparation ledger hash differs from frozen A42 evidence.")

    snapshot = verify_eligibility_manifest(
        eligibility_manifest, plan_dir, input_root, "development"
    )
    if snapshot.expected_nominal_count != 3600 or snapshot.supported_denominator != 2310:
        raise ValueError("Verified A42 development population differs from expected counts.")
    input_view_manifest = json.loads(
        (input_root.parent / "input-view-manifest.json").read_text(encoding="utf-8")
    )
    if (
        input_view_manifest.get("source_tree_modified") is not False
        or input_view_manifest.get("all_reused_files_hardlinked_to_source") is not True
        or Path(str(input_view_manifest.get("input_view_root", ""))).resolve()
        != input_root.resolve()
    ):
        raise ValueError(
            "Prepared input view provenance does not match the frozen hardlinked root."
        )

    with preparation_csv.open(newline="", encoding="utf-8") as stream:
        preparations = {row["trial_id"]: row for row in csv.DictReader(stream)}
    eligible = [row for row in snapshot.nominal_trials]
    if len(eligible) != 3600:
        raise ValueError(f"Expected 3,600 nominal rows, found {len(eligible)}.")

    geometry_rows: list[dict[str, Any]] = []
    for eligibility_row in eligible:
        metadata = {
            "trial_id": eligibility_row.trial_id,
            "port_id": eligibility_row.port_id,
            "placement_id": eligibility_row.placement_id,
            "realization": eligibility_row.realization,
            "range_m": eligibility_row.range_m,
            "report_types": eligibility_row.report_types,
            "sensor_condition": eligibility_row.sensor_condition,
            "supported": eligibility_row.supported is True,
            "support_count": eligibility_row.support_count,
        }
        trial = preparations.get(eligibility_row.trial_id)
        if trial is None or trial.get("status") != "completed":
            raise ValueError(
                f"Nominal preparation is missing or incomplete: {eligibility_row.trial_id}."
            )
        directory = _trial_dir(input_root, metadata)
        truth_path = directory / "truth.json"
        full_truth_path = directory / "truth.full.json.gz"
        truth_crs_path = directory / "truth-crs.json"
        if eligibility_row.support_path is None or eligibility_row.support_sha256 is None:
            raise ValueError(f"Nominal support evidence is missing: {eligibility_row.trial_id}.")
        support_path = input_root / eligibility_row.support_path
        measurements_path = directory / "measurements.json"
        difficulty_path = directory / "difficulty.json"
        if sha256_gzip_contents(full_truth_path) != trial["truth_sha256"]:
            raise ValueError(f"Full truth hash mismatch for {eligibility_row.trial_id}.")
        if sha256_file(truth_crs_path) != trial["truth_crs_sha256"]:
            raise ValueError(
                f"Truth coordinate reference hash mismatch for {eligibility_row.trial_id}."
            )
        if sha256_file(support_path) != eligibility_row.support_sha256:
            raise ValueError(f"Support hash mismatch for {eligibility_row.trial_id}.")
        if sha256_file(measurements_path) != trial["measurements_sha256"]:
            raise ValueError(f"Measurement hash mismatch for {eligibility_row.trial_id}.")
        if sha256_file(difficulty_path) != trial["difficulty_sha256"]:
            raise ValueError(f"Prepared difficulty hash mismatch for {eligibility_row.trial_id}.")

        support = json.loads(support_path.read_text(encoding="utf-8"))
        support_count = support.get(
            "detected_physical_catalog_matched_groups",
            support.get("support_count"),
        )
        if support_count != eligibility_row.support_count:
            raise ValueError(f"Support count mismatch for {eligibility_row.trial_id}.")
        if support.get("valid_reference_water") is not True:
            raise ValueError(
                f"Nominal support row lacks valid reference water: {eligibility_row.trial_id}."
            )
        truth = read_contract(truth_path, EvaluationTruth)
        measurements = read_contract(measurements_path, MeasurementSetV2)
        prepared_difficulty = json.loads(difficulty_path.read_text(encoding="utf-8"))
        difficulty = evaluate_scene_difficulty(
            truth,
            measurements,
            valid_reference_water=True,
            port_id=eligibility_row.port_id,
            placement_id=eligibility_row.placement_id,
        )
        if (
            difficulty.matched_group_count != prepared_difficulty["matched_group_count"]
            or difficulty.geometry_stratum != prepared_difficulty["geometry_stratum"]
            or not _same_radius(
                difficulty.ellipse_major_radius_95_m,
                prepared_difficulty["ellipse_major_radius_95_m"],
            )
        ):
            raise ValueError(
                f"Recomputed geometry disagrees with preparation diagnostic for "
                f"{eligibility_row.trial_id}."
            )
        geometry_rows.append(
            {
                **metadata,
                "difficulty": {
                    "matched_group_count": difficulty.matched_group_count,
                    "ellipse_major_radius_95_m": difficulty.ellipse_major_radius_95_m,
                    "geometry_stratum": difficulty.geometry_stratum,
                },
            }
        )

    summary = summarize_difficulty_rows(geometry_rows)
    if summary["supported_trial_count"] != 2310:
        raise ValueError("Difficulty aggregation changed the frozen support denominator.")
    return {
        "schema": "development-scene-difficulty-v1",
        "source": {
            "eligibility_manifest_sha256": manifest_sha,
            "preparation_sha256": sha256_file(preparation_csv),
            "input_root_view": "a38-anchor-corrected-dev-hardlinked/inputs",
        },
        "population": {
            "split": "development",
            "nominal_trials": summary["nominal_trial_count"],
            "supported_trials": summary["supported_trial_count"],
            "unsupported_trials": summary["unsupported_trial_count"],
            "heldout_rows_read": False,
            "search_outputs_read": False,
            "interpretation": "Known-association geometry diagnostics; not localization accuracy.",
        },
        "geometry": {
            "method": "bearing_localization.evaluation.difficulty.evaluate_scene_difficulty",
            "sigma_fallback_deg": 0.5,
            "sigma_floor_deg": 0.05,
            "summary": summary,
        },
    }


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    shared_project_root = project_root.parent.parent.parent
    default_output_root = shared_project_root / ".agent-work" / "outputs"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=default_output_root
        / "a38-anchor-corrected-dev-hardlinked"
        / "eligibility"
        / "development-a42-full-v2.json",
    )
    parser.add_argument(
        "--plan-dir",
        type=Path,
        default=shared_project_root
        / "benchmarks"
        / "accuracy"
        / "plans"
        / "anchor-corrected-2026-10-26",
    )
    parser.add_argument(
        "--input-root",
        type=Path,
        default=default_output_root / "a38-anchor-corrected-dev-hardlinked" / "inputs",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=project_root / "results" / "accuracy" / "development-difficulty-2026-10-26.json",
    )
    args = parser.parse_args()
    preparation_csv = args.input_root / "development" / "preparation.csv"
    summary = load_development_summary(
        eligibility_manifest=args.manifest,
        plan_dir=args.plan_dir,
        input_root=args.input_root,
        preparation_csv=preparation_csv,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
