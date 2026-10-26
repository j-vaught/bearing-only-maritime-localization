"""Run a preselected, development-only full-domain search feasibility pilot."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from statistics import median
from typing import Any, cast

from pyproj import CRS, Transformer
from shapely import from_geojson, transform
from shapely.ops import unary_union

from bearing_localization.search.config import ObjectiveVersion, SearchConfig
from maritime_localization_shared.contracts.inputs_v2 import MapV2
from maritime_localization_shared.serialization import read_contract

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.accuracy_eligibility import verify_eligibility_manifest  # noqa: E402
from tools.run_accuracy_suite import sha256_file  # noqa: E402

GATES = tuple((range_m, label) for range_m in (1_000, 5_000, 10_000) for label in ("True", "False"))
DEFAULT_CONFIG = ROOT / "benchmarks/accuracy/pilots/development-feasibility.json"


def _run_search_process(
    argv: list[str], *, cwd: Path, timeout_s: float, grace_s: float = 1.0
) -> dict[str, Any]:
    """Run one search in its own process group and reap descendants on timeout."""
    started = time.monotonic()
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
        return {
            "status": "completed" if process.returncode == 0 else "failed",
            "returncode": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "elapsed_s": time.monotonic() - started,
            "process_group_terminated": False,
        }
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=grace_s)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        # The command process may exit before a descendant. Kill the original
        # session group after the grace period even when communicate returned.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        final_stdout, final_stderr = process.communicate()
        stdout = final_stdout or stdout
        stderr = final_stderr or stderr
        return {
            "status": "timeout",
            "returncode": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "elapsed_s": time.monotonic() - started,
            "process_group_terminated": True,
        }


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def select_pilot_rows(
    trial_rows: list[dict[str, str]],
    preparation_rows: list[dict[str, str]],
    *,
    input_root: Path,
    split: str = "development",
    supported_per_gate: int = 1,
    eligibility_trials: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Select by pre-search preparation and support evidence only."""
    if split != "development":
        raise ValueError("The feasibility pilot is restricted to development rows.")
    if supported_per_gate not in (1, 2):
        raise ValueError("supported_per_gate must be one or two.")
    prep_by_trial = {
        row["trial_id"]: row for row in preparation_rows if row["trial_id"].startswith(f"{split}/")
    }
    candidates = [row for row in trial_rows if row["split"] == split]
    eligibility_by_trial = {row["trial_id"]: row for row in (eligibility_trials or [])}
    selected: dict[str, tuple[dict[str, str], str, dict[str, Any]]] = {}

    def trial_dir(row: dict[str, str]) -> Path:
        return (
            input_root
            / split
            / row["port_id"]
            / row["placement_id"]
            / f"r{row['realization']}"
            / f"{row['range_m']}m"
            / ("types-on" if row["report_types"] == "True" else "types-off")
            / row["sensor_condition"]
        )

    for range_m, label in GATES:
        eligible: list[tuple[dict[str, str], dict[str, Any]]] = []
        for row in sorted(candidates, key=lambda item: item["trial_id"]):
            if (
                row["sensor_condition"] != "nominal"
                or int(row["range_m"]) != range_m
                or row["report_types"] != label
            ):
                continue
            prep = prep_by_trial.get(row["trial_id"], {})
            if eligibility_trials is not None:
                support = eligibility_by_trial.get(row["trial_id"], {})
                if prep.get("status") == "completed" and support.get("supported") is True:
                    eligible.append((row, support))
                continue
            support_path = trial_dir(row) / "support.json"
            if prep.get("status") != "completed" or not support_path.is_file():
                continue
            support = json.loads(support_path.read_text(encoding="utf-8"))
            if support.get("supported") is True:
                eligible.append((row, support))
        chosen_units: set[tuple[str, str, str]] = set()
        count = 0
        for row, support in eligible:
            unit = (row["port_id"], row["placement_id"], row["realization"])
            if unit in chosen_units:
                continue
            chosen_units.add(unit)
            selected[row["trial_id"]] = (
                row,
                "supported_nominal",
                {
                    "support_path": support.get("support_path")
                    or str((trial_dir(row) / "support.json").resolve()),
                    "support_sha256": support.get("support_sha256")
                    or sha256_file(trial_dir(row) / "support.json"),
                    "supported": support["supported"],
                    "eligibility_status": support.get("status"),
                    "eligibility_record_sha256": _sha256_json(support)
                    if eligibility_trials is not None
                    else None,
                    "detected_physical_catalog_matched_groups": support.get(
                        "detected_physical_catalog_matched_groups"
                    ),
                },
            )
            count += 1
            if count == supported_per_gate:
                break
        if count != supported_per_gate:
            raise ValueError(
                "Only "
                f"{count} supported pre-search trials are available for "
                f"{range_m}m/types{label}."
            )

    unsupported = []
    for row in sorted(candidates, key=lambda item: item["trial_id"]):
        if row["sensor_condition"] != "nominal" or row["trial_id"] in selected:
            continue
        prep = prep_by_trial.get(row["trial_id"], {})
        if eligibility_trials is not None:
            support = eligibility_by_trial.get(row["trial_id"], {})
            if prep.get("status") == "completed" and support.get("supported") is False:
                unsupported.append((row, Path(support.get("support_path") or ""), support))
            continue
        support_path = trial_dir(row) / "support.json"
        if prep.get("status") != "completed" or not support_path.is_file():
            continue
        support = json.loads(support_path.read_text(encoding="utf-8"))
        if support.get("supported") is False:
            unsupported.append((row, support_path, support))
    if unsupported:
        row, support_path, support = unsupported[0]
        selected[row["trial_id"]] = (
            row,
            "unsupported_nominal",
            {
                "support_path": support.get("support_path") or str(support_path.resolve()),
                "support_sha256": support.get("support_sha256") or sha256_file(support_path),
                "supported": False,
                "eligibility_status": support.get("status"),
                "eligibility_record_sha256": _sha256_json(support)
                if eligibility_trials is not None
                else None,
                "detected_physical_catalog_matched_groups": support.get(
                    "detected_physical_catalog_matched_groups"
                ),
            },
        )

    failures = []
    for row in sorted(candidates, key=lambda item: item["trial_id"]):
        prep = prep_by_trial.get(row["trial_id"], {})
        if prep and prep.get("status") != "completed":
            failures.append((row, prep))
    if failures:
        row, prep = failures[0]
        selected[row["trial_id"]] = (
            row,
            "retained_preparation_failure",
            {
                "preparation_status": prep.get("status"),
                "preparation_failure": prep.get("failure", ""),
            },
        )

    manifests: list[dict[str, Any]] = []
    result_rows: list[dict[str, str]] = []
    for trial_id in sorted(selected):
        row, role, evidence = selected[trial_id]
        prep = prep_by_trial.get(trial_id, {})
        directory = trial_dir(row)
        input_hashes = {
            name: sha256_file(directory / name) if (directory / name).is_file() else ""
            for name in ("measurements.json", "support.json")
        }
        result_rows.append(row)
        manifests.append(
            {
                "trial_id": trial_id,
                "selection_role": role,
                "selection_basis": "development plan order and pre-search preparation/support only",
                "preparation_status": prep.get("status", "missing_preparation_record"),
                "preparation_failure": prep.get("failure", ""),
                "support_evidence": evidence,
                "input_sha256": input_hashes,
                "all_required_inputs_present": all(input_hashes.values()),
            }
        )
    return result_rows, manifests


def full_domain_bounds(
    map_path: Path, projected_crs: str, margin_m: float = 10_000.0
) -> tuple[float, float, float, float]:
    """Project the union of declared known and unknown map domains and pad it."""
    map_v2 = read_contract(map_path, MapV2)
    geometries = [
        from_geojson(value)
        for value in (map_v2.coverage_geometry_geojson, map_v2.unknown_geometry_geojson)
        if value is not None
    ]
    if not geometries:
        raise ValueError("MapV2 must declare coverage or unknown geometry for full-domain bounds.")
    domain = unary_union(geometries)
    transformer = Transformer.from_crs(
        map_v2.crs, CRS.from_user_input(projected_crs), always_xy=True
    )
    projected = transform(domain, transformer.transform, interleaved=False)
    west, south, east, north = projected.bounds
    return (west - margin_m, east + margin_m, south - margin_m, north + margin_m)


def _load_config(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != "accuracy-feasibility-pilot-v1":
        raise ValueError("Pilot config must declare schema accuracy-feasibility-pilot-v1.")
    if value.get("split") != "development" or value.get("search_domain_scope") != "full_coast":
        raise ValueError("Pilot config must remain development-only and full-coast.")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _trial_directory(input_root: Path, row: dict[str, str]) -> Path:
    return (
        input_root
        / "development"
        / row["port_id"]
        / row["placement_id"]
        / f"r{row['realization']}"
        / f"{row['range_m']}m"
        / ("types-on" if row["report_types"] == "True" else "types-off")
        / row["sensor_condition"]
    )


def _proposal_counters(telemetry: dict[str, Any]) -> dict[str, Any]:
    diagnostics = telemetry.get("diagnostics", {})
    names = (
        "proposal_count",
        "source_pairs_total",
        "source_pairs_processed",
        "candidate_cells_total",
        "generation_status",
        "candidate_counts",
        "grid_shape",
        "candidate_count",
        "outcome_counts",
    )
    return {name: diagnostics[name] for name in names if name in diagnostics}


def _search_counters(telemetry: dict[str, Any]) -> dict[str, Any]:
    report = telemetry.get("telemetry", {})
    return {
        "counts": report.get("counts", {}),
        "stages": report.get("stages", []),
        "cap_hits": report.get("cap_hits", {}),
        "unresolved_regions": report.get("unresolved_regions", {}),
        "memory": report.get("memory", {}),
    }


def _runtime_projection(
    records: dict[str, dict[str, Any]], grid_total: int | None
) -> dict[str, Any]:
    samples: list[tuple[float, int, float, float]] = []
    for record in records.values():
        telemetry = record.get("scoring_and_refinement_telemetry", {})
        counts = telemetry.get("counts", {})
        evaluated = int(counts.get("evaluated", 0))
        stages = telemetry.get("stages", [])
        search_stage = sum(
            float(stage.get("wall_s", 0.0))
            for stage in stages
            if stage.get("name") == "candidate_search"
        )
        load_stage = sum(
            float(stage.get("wall_s", 0.0))
            for stage in stages
            if stage.get("name") in {"load_inputs", "project_map"}
        )
        if record.get("status") == "completed" and evaluated > 0 and search_stage > 0:
            samples.append((search_stage / evaluated, evaluated, search_stage, load_stage))
    if not samples:
        return {
            "candidate_search_seconds_per_evaluated_candidate_median": None,
            "estimated_candidate_search_seconds_at_20000_candidates": None,
            "estimated_input_setup_seconds_median": None,
            "declared_grid_points_total": grid_total,
            "budget_20000_completes_declared_grid": None
            if grid_total is None
            else grid_total <= 20_000,
            "runtime_projection_basis": "insufficient successful candidate-search telemetry",
        }
    per_candidate = median(sample[0] for sample in samples)
    setup = median(sample[3] for sample in samples)
    return {
        "candidate_search_seconds_per_evaluated_candidate_median": per_candidate,
        "estimated_candidate_search_seconds_at_20000_candidates": per_candidate * 20_000,
        "estimated_input_setup_seconds_median": setup,
        "declared_grid_points_total": grid_total,
        "budget_20000_completes_declared_grid": None
        if grid_total is None
        else grid_total <= 20_000,
        "runtime_projection_basis": (
            "median combined candidate_search wall time divided by telemetry evaluated count, "
            "linearly extrapolated to 20,000 candidates; proposal generation and refinement "
            "are not separately timed by the current CLI"
        ),
    }


def run_pilot(
    *,
    trial_table_path: Path,
    preparation_manifest_path: Path,
    input_root: Path,
    catalog_path: Path,
    map_path: Path,
    output_root: Path,
    config_path: Path = DEFAULT_CONFIG,
    projected_crs: str = "EPSG:32618",
    select_only: bool = False,
    eligibility_manifest_path: Path | None = None,
    resume: bool = False,
    retry_failures: bool = False,
    pause_file: Path | None = None,
    stop_after_searches: int | None = None,
) -> Path:
    """Run searches only; the evaluator truth and metrics are never opened."""
    config = _load_config(config_path)
    if not select_only and eligibility_manifest_path is None:
        raise ValueError("A frozen --eligibility-manifest is required for a pilot run.")
    eligibility_sha256 = None
    eligibility_rows = None
    eligibility_snapshot = None
    if eligibility_manifest_path is not None:
        eligibility_snapshot = verify_eligibility_manifest(
            eligibility_manifest_path, trial_table_path.parent, input_root, "development"
        )
        eligibility_sha256 = sha256_file(eligibility_manifest_path)
        eligibility_rows = [asdict(record) for record in eligibility_snapshot.trials]
    trials, selection = select_pilot_rows(
        _read_rows(trial_table_path),
        _read_rows(preparation_manifest_path),
        input_root=input_root,
        split="development",
        supported_per_gate=int(config["supported_cases_per_gate"]),
        eligibility_trials=eligibility_rows,
    )
    if output_root.exists() and any(output_root.iterdir()) and not resume and not select_only:
        raise FileExistsError("Output root already contains data; pass --resume to continue it.")
    output_root.mkdir(parents=True, exist_ok=True)
    plan_dir = output_root / "plan"
    _write_rows(plan_dir / "trials.csv", trials)
    bounds = full_domain_bounds(map_path, projected_crs, float(config["domain_margin_m"]))
    selection_doc = {
        "schema": "accuracy-feasibility-selection-v1",
        "split": "development",
        "catalog_path": str(catalog_path.resolve()),
        "catalog_sha256": sha256_file(catalog_path),
        "map_path": str(map_path.resolve()),
        "map_sha256": sha256_file(map_path),
        "map_scope": "full declared coast domain; unknown geometry is retained as unknown",
        "projected_crs": CRS.from_user_input(projected_crs).to_string(),
        "full_domain_bounds_xy_m": list(bounds),
        "domain_margin_m": float(config["domain_margin_m"]),
        "selection_sha256": _sha256_json(selection),
        "trial_table_sha256": sha256_file(trial_table_path),
        "preparation_manifest_sha256": sha256_file(preparation_manifest_path),
        "pilot_config_sha256": sha256_file(config_path),
        "eligibility_manifest_sha256": eligibility_sha256,
        "eligibility_plan_sha256": (
            eligibility_snapshot.plan_sha256 if eligibility_snapshot is not None else None
        ),
        "eligibility_expected_trial_count": (
            eligibility_snapshot.expected_trial_count if eligibility_snapshot is not None else None
        ),
        "eligibility_expected_nominal_count": (
            eligibility_snapshot.expected_nominal_count
            if eligibility_snapshot is not None
            else None
        ),
        "eligibility_supported_nominal_count": (
            eligibility_snapshot.supported_denominator if eligibility_snapshot is not None else None
        ),
        "eligibility_expected_trial_ids_sha256": (
            eligibility_snapshot.expected_trial_ids_sha256
            if eligibility_snapshot is not None
            else None
        ),
        "selected_trial_count": len(selection),
        "trials": selection,
    }
    selection_path = output_root / "selection-manifest.json"
    if selection_path.exists():
        prior_selection = json.loads(selection_path.read_text(encoding="utf-8"))
        if prior_selection != selection_doc:
            raise ValueError("Resume selection fingerprint differs from the frozen selection.")
    else:
        _write_json(selection_path, selection_doc)
    copied_config = output_root / "pilot-config.json"
    if copied_config.exists() and sha256_file(copied_config) != sha256_file(config_path):
        raise ValueError("Resume config hash differs from the frozen pilot config.")
    if not copied_config.exists():
        shutil.copyfile(config_path, copied_config)
    if select_only:
        return selection_path

    from bearing_localization.search.config_serialization import search_config_to_dict
    from bearing_localization.search.reference_grid import grid_shape

    if not catalog_path.is_file() or not map_path.is_file():
        raise FileNotFoundError("Full-coast CatalogV2 and MapV2 are required for the pilot.")
    catalog_hash = sha256_file(catalog_path)
    map_hash = sha256_file(map_path)
    if config.get("expected_catalog_sha256") not in (None, catalog_hash):
        raise ValueError("Catalog hash differs from the version pinned by pilot config.")
    if config.get("expected_map_sha256") not in (None, map_hash):
        raise ValueError("Map hash differs from the version pinned by pilot config.")
    static_root = output_root / "static-inputs"
    static_root.mkdir(parents=True, exist_ok=True)
    frozen_catalog = static_root / f"catalog-{catalog_hash}.json"
    frozen_map = static_root / f"map-{map_hash}.json"
    for source, frozen, expected in (
        (catalog_path, frozen_catalog, catalog_hash),
        (map_path, frozen_map, map_hash),
    ):
        if not frozen.exists():
            shutil.copyfile(source, frozen)
        if sha256_file(frozen) != expected:
            raise ValueError(f"Frozen static input hash mismatch: {frozen.name}")
    if eligibility_manifest_path is not None:
        frozen_eligibility = static_root / f"eligibility-{eligibility_sha256}.json"
        if not frozen_eligibility.exists():
            shutil.copyfile(eligibility_manifest_path, frozen_eligibility)
        if sha256_file(frozen_eligibility) != eligibility_sha256:
            raise ValueError("Frozen eligibility manifest hash mismatch.")
    projected_crs = CRS.from_user_input(projected_crs).to_string()
    frozen_measurements: dict[str, Path] = {}
    for row in trials:
        source = _trial_directory(input_root, row) / "measurements.json"
        if source.is_file():
            digest = sha256_file(source)
            target = static_root / "measurements" / f"{_sha256_json(row)[:20]}-{digest}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copyfile(source, target)
            if sha256_file(target) != digest:
                raise ValueError(f"Frozen measurement hash mismatch for {row['trial_id']}")
            frozen_measurements[row["trial_id"]] = target
    results: list[dict[str, Any]] = []
    searches_started = 0
    pause_reason: str | None = None
    python = sys.executable
    command_base = [
        python,
        "-m",
        "bearing_localization.search_cli",
        "--schema-version",
        "2",
    ]
    controller_source_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    run_identity = _sha256_json(
        {
            "selection_sha256": selection_doc["selection_sha256"],
            "catalog_sha256": catalog_hash,
            "map_sha256": map_hash,
            "pilot_config_sha256": sha256_file(copied_config),
            "controller_source_sha": controller_source_sha,
        }
    )
    progress_path = output_root / "pilot-progress.json"
    if resume:
        if progress_path.is_file():
            previous_progress = json.loads(progress_path.read_text(encoding="utf-8"))
            if previous_progress.get("run_identity_sha256") != run_identity:
                raise ValueError(
                    "Resume controller or static-input identity changed; use a new output root."
                )
        for previous_manifest in (output_root / "runs").glob("*/*/*/run-manifest.json"):
            previous = json.loads(previous_manifest.read_text(encoding="utf-8"))
            if previous.get("controller_source_sha") != controller_source_sha:
                raise ValueError("Resume controller source differs from an archived attempt.")
    contexts: dict[tuple[str, int], dict[str, Any]] = {}
    for algorithm in config["algorithms"]:
        for budget in config["candidate_budgets"]:
            grid_total = None
            if algorithm in {"grid", "reference_grid"}:
                nx, ny = grid_shape(bounds, float(config["grid_spacing_m"]))
                grid_total = nx * ny
            run_config = {
                "schema": "accuracy-feasibility-run-config-v1",
                "declared_grid_points_total": grid_total,
                "selection_sha256": selection_doc["selection_sha256"],
                "pilot_config_sha256": sha256_file(copied_config),
                "controller_source_sha": controller_source_sha,
                "accuracy_cli_sha256": sha256_file(ROOT / "bearing_localization/accuracy_cli.py"),
                "catalog_sha256": catalog_hash,
                "map_sha256": map_hash,
                "algorithm": str(algorithm),
                "candidate_budget": int(budget),
                "objective_version": config["objective_version"],
                "projected_crs": projected_crs,
                "bounds_xy_m": list(bounds),
                "grid_spacing_m": float(config["grid_spacing_m"]),
                "wedge_error_deg": float(config["max_grid_bearing_error_deg"]),
                "wedge_cell_size_m": float(config["wedge_cell_size_m"]),
                "timeout_s": float(config["timeout_s"]),
            }
            run_id = _sha256_json(run_config)[:24]
            run_dir = output_root / "runs" / str(algorithm) / f"budget-{int(budget)}" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            scope_path = run_dir / "scope-manifest.json"
            scope_doc = {
                "schema_version": 1,
                "geographic_scope": "full_coast",
                "catalog_sha256": catalog_hash,
                "map_sha256": map_hash,
                "projected_crs": projected_crs,
                "bounds_xy_m": list(bounds),
            }
            _write_json(scope_path, scope_doc)
            records: dict[str, dict[str, Any]] = {}
            manifest_path = run_dir / "run-manifest.json"
            if manifest_path.is_file():
                if not resume:
                    raise FileExistsError("Existing run manifest requires --resume.")
                prior = json.loads(manifest_path.read_text(encoding="utf-8"))
                if prior.get("eligibility_manifest_sha256") != eligibility_sha256:
                    raise ValueError("Resume eligibility manifest hash differs.")
                records = {item["trial_id"]: item for item in prior.get("trials", [])}
            if not manifest_path.is_file():
                _write_json(
                    manifest_path,
                    {
                        "schema": "accuracy-feasibility-run-v1",
                        "run_id": run_id,
                        **run_config,
                        "scope_manifest_path": "scope-manifest.json",
                        "scope_sha256": sha256_file(scope_path),
                        "eligibility_manifest_sha256": eligibility_sha256,
                        "truth_passed_to_search": False,
                        "evaluator_invoked": False,
                        "trials": [records[key] for key in sorted(records)],
                    },
                )
            contexts[(str(algorithm), int(budget))] = {
                "algorithm": algorithm,
                "budget": budget,
                "grid_total": grid_total,
                "run_config": run_config,
                "run_id": run_id,
                "run_dir": run_dir,
                "scope_path": scope_path,
                "records": records,
                "manifest_path": manifest_path,
            }
    # Round-robin each budget across all methods before moving to the next trial.
    for budget in config["candidate_budgets"]:
        for row in trials:
            for algorithm in config["algorithms"]:
                context = contexts[(str(algorithm), int(budget))]
                grid_total = context["grid_total"]
                run_config = context["run_config"]
                run_id = context["run_id"]
                run_dir = context["run_dir"]
                scope_path = context["scope_path"]
                records = context["records"]
                manifest_path = context["manifest_path"]
                if pause_reason is not None:
                    break
                if pause_file is not None and pause_file.exists():
                    pause_reason = "pause_file"
                    break
                if stop_after_searches is not None and searches_started >= stop_after_searches:
                    pause_reason = "stop_after_searches"
                    break
                trial_id = row["trial_id"]
                directory = _trial_directory(input_root, row)
                source_measurements_path = directory / "measurements.json"
                measurements_path = frozen_measurements.get(trial_id, source_measurements_path)
                required = (
                    source_measurements_path,
                    directory / "support.json",
                    directory / "physical-identity-audit.json",
                )
                input_hashes = {
                    path.name: sha256_file(path) if path.is_file() else "" for path in required
                }
                base_config = SearchConfig(
                    algorithm=str(algorithm),
                    bounds_xy=bounds,
                    max_range_m=float(row["range_m"]),
                    candidate_budget=int(budget),
                    objective_version=cast(ObjectiveVersion, config["objective_version"]),
                    generated_sigma_deg=float(config["generated_sigma_deg"]),
                    detection_probability=float(config["detection_probability"]),
                    bearing_outlier_probability=float(config["bearing_outlier_probability"]),
                    expected_unmatched_reports=float(config["expected_unmatched_reports"]),
                    use_types=row["report_types"] == "True",
                )
                config_doc = search_config_to_dict(base_config)
                config_sha = _sha256_json(config_doc)
                fingerprint = _sha256_json(
                    {
                        "trial_id": trial_id,
                        "inputs": input_hashes,
                        "config_sha256": config_sha,
                        "scope_sha256": sha256_file(scope_path),
                        "command_config": run_config,
                    }
                )
                artifact_dir = run_dir / "artifacts" / fingerprint[:24]
                artifact_dir.mkdir(parents=True, exist_ok=True)
                search_config_path = artifact_dir / "search-config.json"
                estimate_path = artifact_dir / "estimate.json"
                telemetry_path = artifact_dir / "telemetry.json"
                stdout_path = artifact_dir / "stdout.txt"
                stderr_path = artifact_dir / "stderr.txt"
                _write_json(search_config_path, config_doc)
                input_hashes["search_config.json"] = sha256_file(search_config_path)
                prior = records.get(trial_id)
                if (
                    prior
                    and prior.get("fingerprint_sha256") == fingerprint
                    and prior.get("status") == "completed"
                    and estimate_path.is_file()
                    and telemetry_path.is_file()
                    and sha256_file(estimate_path) == prior.get("estimate_sha256")
                    and sha256_file(telemetry_path) == prior.get("telemetry_sha256")
                ):
                    continue
                if (
                    prior
                    and prior.get("fingerprint_sha256") == fingerprint
                    and prior.get("status") not in {"completed", "missing_inputs"}
                    and not retry_failures
                ):
                    continue
                if not source_measurements_path.is_file():
                    records[trial_id] = {
                        "trial_id": trial_id,
                        "selection_role": next(
                            item["selection_role"]
                            for item in selection
                            if item["trial_id"] == trial_id
                        ),
                        "status": "missing_inputs",
                        "failure": "prepared_measurements_missing;preparation_failure_retained",
                        "fingerprint_sha256": fingerprint,
                        "input_sha256": input_hashes,
                    }
                    _write_json(
                        manifest_path,
                        {
                            "schema": "accuracy-feasibility-run-v1",
                            "run_id": run_id,
                            **run_config,
                            "scope_manifest_path": "scope-manifest.json",
                            "scope_sha256": sha256_file(scope_path),
                            "eligibility_manifest_sha256": eligibility_sha256,
                            "truth_passed_to_search": False,
                            "evaluator_invoked": False,
                            "trials": [records[key] for key in sorted(records)],
                        },
                    )
                    continue
                argv = [
                    *command_base,
                    "--measurements",
                    str(measurements_path),
                    "--catalog",
                    str(frozen_catalog.resolve()),
                    "--map",
                    str(frozen_map.resolve()),
                    "--config",
                    str(search_config_path),
                    "--projected-crs",
                    projected_crs,
                    "--scope-manifest",
                    str(scope_path),
                    "--algorithm",
                    str(algorithm),
                    "--spacing-m",
                    str(config["grid_spacing_m"]),
                    "--max-grid-bearing-error-deg",
                    str(config["max_grid_bearing_error_deg"]),
                    "--wedge-cell-size-m",
                    str(config["wedge_cell_size_m"]),
                    "--telemetry-output",
                    str(telemetry_path),
                    "--output",
                    str(estimate_path),
                ]
                status = "completed"
                failure = ""
                returncode: int | None = None
                searches_started += 1
                outcome = _run_search_process(argv, cwd=ROOT, timeout_s=float(config["timeout_s"]))
                returncode = outcome["returncode"]
                stdout_path.write_text(str(outcome["stdout"]), encoding="utf-8")
                stderr_path.write_text(str(outcome["stderr"]), encoding="utf-8")
                if outcome["status"] == "timeout":
                    status = "search_timeout"
                    failure = "search_timeout_process_group_terminated"
                elif outcome["status"] != "completed":
                    status = "search_failed"
                    failure = f"search_returncode_{returncode}"
                elapsed = float(outcome["elapsed_s"])
                telemetry_doc: dict[str, Any] = {}
                result_status = ""
                result_contract = {}
                if status == "completed" and (
                    not estimate_path.is_file() or not telemetry_path.is_file()
                ):
                    status = "missing_output"
                    failure = "estimate_or_telemetry_missing"
                if telemetry_path.is_file():
                    telemetry_doc = json.loads(telemetry_path.read_text(encoding="utf-8"))
                if estimate_path.is_file():
                    result_contract = json.loads(estimate_path.read_text(encoding="utf-8"))
                    result_status = str(result_contract.get("search_status", ""))
                proposal_counts = _proposal_counters(telemetry_doc)
                if grid_total is not None:
                    proposal_counts["declared_grid_points_total"] = grid_total
                record = {
                    "trial_id": trial_id,
                    "selection_role": next(
                        item["selection_role"] for item in selection if item["trial_id"] == trial_id
                    ),
                    "eligibility_status": next(
                        item["support_evidence"].get("eligibility_status")
                        for item in selection
                        if item["trial_id"] == trial_id
                    ),
                    "eligibility_record_sha256": next(
                        item["support_evidence"].get("eligibility_record_sha256")
                        for item in selection
                        if item["trial_id"] == trial_id
                    ),
                    "eligibility_manifest_sha256": eligibility_sha256,
                    "port_id": row["port_id"],
                    "placement_id": row["placement_id"],
                    "range_m": int(row["range_m"]),
                    "report_types": row["report_types"],
                    "sensor_condition": row["sensor_condition"],
                    "algorithm": algorithm,
                    "candidate_budget": int(budget),
                    "status": status,
                    "failure": failure,
                    "returncode": returncode,
                    "wall_elapsed_s": elapsed,
                    "process_group_terminated": bool(outcome["process_group_terminated"]),
                    "search_status": result_status,
                    "budget_limit": result_contract.get("budget_limit"),
                    "budget_used": result_contract.get("budget_used"),
                    "certificate_scope": result_contract.get("certificate_scope"),
                    "proposal_generation_counters": proposal_counts,
                    "scoring_and_refinement_telemetry": _search_counters(telemetry_doc),
                    "input_sha256": input_hashes,
                    "config_sha256": sha256_file(search_config_path),
                    "scope_sha256": sha256_file(scope_path),
                    "argv": argv,
                    "estimate_path": str(estimate_path.relative_to(run_dir)),
                    "estimate_sha256": sha256_file(estimate_path)
                    if estimate_path.is_file()
                    else "",
                    "telemetry_path": str(telemetry_path.relative_to(run_dir)),
                    "telemetry_sha256": sha256_file(telemetry_path)
                    if telemetry_path.is_file()
                    else "",
                    "stdout_path": str(stdout_path.relative_to(run_dir)),
                    "stdout_sha256": sha256_file(stdout_path),
                    "stderr_path": str(stderr_path.relative_to(run_dir)),
                    "stderr_sha256": sha256_file(stderr_path),
                    "fingerprint_sha256": fingerprint,
                }
                records[trial_id] = record
                _write_json(
                    manifest_path,
                    {
                        "schema": "accuracy-feasibility-run-v1",
                        "run_id": run_id,
                        **run_config,
                        "scope_manifest_path": "scope-manifest.json",
                        "scope_sha256": sha256_file(scope_path),
                        "eligibility_manifest_sha256": eligibility_sha256,
                        "truth_passed_to_search": False,
                        "evaluator_invoked": False,
                        "counter_notes": {
                            "proposal_generation_counters": (
                                "Algorithm-reported proposal/generation counts. Null when "
                                "the method does not expose them."
                            ),
                            "scoring_and_refinement_telemetry.counts": (
                                "Scorer generated/evaluated/retained counts from search "
                                "telemetry. These are not accuracy metrics."
                            ),
                        },
                        "trials": [records[key] for key in sorted(records)],
                    },
                )
                if pause_reason is not None:
                    break
            if pause_reason is not None:
                break
        if pause_reason is not None:
            break
    for algorithm in config["algorithms"]:
        for budget in config["candidate_budgets"]:
            context = contexts[(str(algorithm), int(budget))]
            grid_total = context["grid_total"]
            run_config = context["run_config"]
            run_id = context["run_id"]
            run_dir = context["run_dir"]
            scope_path = context["scope_path"]
            records = context["records"]
            manifest_path = context["manifest_path"]
            results.append(
                {
                    "algorithm": algorithm,
                    "candidate_budget": int(budget),
                    "run_manifest_path": str(manifest_path),
                    "run_manifest_sha256": sha256_file(manifest_path),
                    "completed_searches": sum(
                        record.get("status") == "completed" for record in records.values()
                    ),
                    "budget_exhausted_searches": sum(
                        record.get("search_status") == "budget_exhausted"
                        for record in records.values()
                    ),
                    "search_elapsed_s": [
                        record["wall_elapsed_s"]
                        for record in records.values()
                        if record.get("status") in {"completed", "search_failed", "search_timeout"}
                    ],
                    **_runtime_projection(records, grid_total),
                }
            )
    progress_path = output_root / "pilot-progress.json"
    _write_json(
        progress_path,
        {
            "schema": "accuracy-feasibility-progress-v1",
            "status": "paused" if pause_reason else "completed",
            "pause_reason": pause_reason,
            "searches_started_this_invocation": searches_started,
            "selection_manifest_sha256": sha256_file(selection_path),
            "eligibility_manifest_sha256": eligibility_sha256,
            "run_identity_sha256": run_identity,
        },
    )
    result_path = output_root / "pilot-results.json"
    _write_json(
        result_path,
        {
            "schema": "accuracy-feasibility-results-v1",
            "selection_manifest_path": str(selection_path),
            "selection_manifest_sha256": sha256_file(selection_path),
            "truth_passed_to_search": False,
            "evaluator_invoked": False,
            "progress_status": "paused" if pause_reason else "completed",
            "runs": results,
        },
    )
    return result_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial-table", type=Path, required=True)
    parser.add_argument("--preparation-manifest", type=Path, required=True)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--map", dest="map_path", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--projected-crs", default="EPSG:32618")
    parser.add_argument("--select-only", action="store_true")
    parser.add_argument("--eligibility-manifest", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failures", action="store_true")
    parser.add_argument("--pause-file", type=Path)
    parser.add_argument("--stop-after-searches", type=int)
    args = parser.parse_args()
    result = run_pilot(
        trial_table_path=args.trial_table,
        preparation_manifest_path=args.preparation_manifest,
        input_root=args.input_root,
        catalog_path=args.catalog,
        map_path=args.map_path,
        output_root=args.output_root,
        config_path=args.config,
        projected_crs=args.projected_crs,
        select_only=args.select_only,
        eligibility_manifest_path=args.eligibility_manifest,
        resume=args.resume,
        retry_failures=args.retry_failures,
        pause_file=args.pause_file,
        stop_after_searches=args.stop_after_searches,
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
