"""Build frozen port splits and orchestrate paired accuracy trials."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import signal
import subprocess
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, cast

import numpy as np
from pyproj import CRS, Geod, Transformer

from bearing_localization.accuracy_cli import _calibration_binding
from bearing_localization.evaluation.difficulty import (
    build_support_manifest,
    evaluate_scene_difficulty,
)
from bearing_localization.search.config_serialization import search_config_from_dict
from bearing_localization.simulator.mismatch import (
    KnownWaterObserver,
    PhysicalSceneV2,
    SensorConfigurationV2,
    SimulatorMismatchConfig,
)
from bearing_localization.simulator.runner import run_simulator_v2
from maritime_localization_shared.contracts.inputs_v2 import CatalogV2, MapV2
from maritime_localization_shared.contracts.localization import LocalizationResult
from maritime_localization_shared.geospatial.vector_map import VectorMap
from maritime_localization_shared.serialization import read_contract, write_contract
from tools.accuracy_eligibility import EligibilitySnapshot, verify_eligibility_manifest

ROOT = Path(__file__).resolve().parents[1]
PORTS_CSV = ROOT / "data/sites/east-coast-major-ports-top150-2024.csv"
PORT_MAP = {
    **{f"B{i}": f"port-{i:03d}" for i in range(1, 25)},
}
SHARED_ROOT = ROOT.parents[2]
DEFAULT_MAP_CACHE = SHARED_ROOT / ".agent-work/cache/maps"
DEFAULT_OUTPUT = Path("benchmarks/accuracy")
RANGES_M = (1_000.0, 5_000.0, 10_000.0)
LABEL_MODES = (True, False)
STRESS_PROFILES = {
    "nominal": {},
    "miss": {"detection_probability": 0.7},
    "clutter": {"expected_clutter_reports": 3.0},
    "noise": {"bearing_sigma_deg": 1.0},
    "label_error": {"type_error_probability": 0.2},
    "heading_bias": {"heading_bias_deg": 1.0},
    "catalog_displacement": {"catalog_displacement_east_m": 20.0},
    "map_buffer": {"land_buffer_m": 10.0},
}
INITIAL_PLACEMENTS = 25
MAX_PLACEMENTS = 100
REALIZATIONS = 2
PLACEMENT_RADIUS_M = 3_000.0
SPLIT_PORTS = {
    "development": (1, 3, 6, 7, 9, 10, 11, 13, 14, 17, 20, 23),
    "validation": (2, 8, 16, 22),
    "heldout": (4, 5, 12, 15, 18, 19, 21, 24),
}
_GEOD = Geod(ellps="WGS84")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_seed(*parts: object) -> int:
    value = "/".join(str(part) for part in parts).encode()
    return int.from_bytes(hashlib.sha256(value).digest()[:4], "big")


def _load_ports(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        result: list[dict[str, Any]] = []
        for row in csv.DictReader(stream):
            legacy_id = row["site_id"]
            if legacy_id not in PORT_MAP:
                raise ValueError(f"Unknown port identifier {legacy_id!r} in {path}.")
            result.append(
                {
                    "source_site_id": legacy_id,
                    "port_id": PORT_MAP[legacy_id],
                    "port_name": row["port"],
                    "latitude_deg": float(row["latitude_deg"]),
                    "longitude_deg": float(row["longitude_deg"]),
                    "anchor_latitude_deg": float(row["reference_anchor_latitude_deg"]),
                    "anchor_longitude_deg": float(row["reference_anchor_longitude_deg"]),
                    "usace_2024_rank": int(row["usace_2024_rank"]),
                }
            )
    if len(result) != 24:
        raise ValueError(f"Expected 24 major ports, found {len(result)}.")
    return result


def _distance_m(a: dict[str, Any], b: dict[str, Any]) -> float:
    _, _, distance = _GEOD.inv(
        float(a["anchor_longitude_deg"]),
        float(a["anchor_latitude_deg"]),
        float(b["anchor_longitude_deg"]),
        float(b["anchor_latitude_deg"]),
    )
    return float(distance)


def _components(ports: list[dict[str, Any]], threshold_m: float) -> list[tuple[str, ...]]:
    remaining = {str(port["port_id"]) for port in ports}
    adjacency = {port_id: set() for port_id in remaining}
    for index, first in enumerate(ports):
        for second in ports[index + 1 :]:
            if _distance_m(first, second) <= threshold_m:
                left, right = str(first["port_id"]), str(second["port_id"])
                adjacency[left].add(right)
                adjacency[right].add(left)
    groups: list[tuple[str, ...]] = []
    while remaining:
        root = min(remaining)
        stack = [root]
        component: set[str] = set()
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            stack.extend(adjacency[node] - component)
        remaining -= component
        groups.append(tuple(sorted(component)))
    return groups


def build_split_manifest(ports_path: Path = PORTS_CSV) -> dict[str, Any]:
    """Apply the predefined split and move overlapping whole groups to development."""
    ports = _load_ports(ports_path)
    initial = {
        f"port-{number:03d}": split for split, numbers in SPLIT_PORTS.items() for number in numbers
    }
    if set(initial) != {str(port["port_id"]) for port in ports}:
        raise ValueError("The predefined split must assign every major port exactly once.")
    effective = dict(initial)
    decisions: list[dict[str, object]] = []
    # Each anchor can be displaced by 3 km and each catalogue footprint extends 10 km.
    # Two placements can therefore share evidence when anchors are within 26 km.
    for group in _components(ports, 26_000.0):
        original = sorted({initial[port_id] for port_id in group})
        if len(original) > 1:
            for port_id in group:
                effective[port_id] = "development"
            decisions.append(
                {
                    "port_ids": list(group),
                    "original_splits": original,
                    "effective_split": "development",
                    "reason": "overlapping_3km_placement_and_10km_catalog_footprints",
                    "maximum_anchor_separation_m": max(
                        _distance_m(
                            next(p for p in ports if p["port_id"] == left),
                            next(p for p in ports if p["port_id"] == right),
                        )
                        for left in group
                        for right in group
                    ),
                }
            )
    port_rows = []
    for port in ports:
        port_id = str(port["port_id"])
        port_rows.append(
            {
                **port,
                "declared_split": initial[port_id],
                "effective_split": effective[port_id],
            }
        )
    try:
        source_label = str(ports_path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        source_label = str(ports_path.resolve())
    return {
        "schema": "accuracy-split-manifest-v2",
        "port_source": source_label,
        "port_source_sha256": sha256_file(ports_path),
        "legacy_b1_b5_separate": True,
        "split_geometry": {
            "placement_radius_m": PLACEMENT_RADIUS_M,
            "catalog_footprint_radius_m": 10_000.0,
            "overlap_anchor_threshold_m": 26_000.0,
            "method": "connected_components_reference_anchor_geodesic_overlap_groups",
        },
        "ports": port_rows,
        "overlap_decisions": decisions,
        "split_port_ids": {
            split: sorted(port_id for port_id, value in effective.items() if value == split)
            for split in ("development", "validation", "heldout")
        },
    }


def _utm_epsg(lon: float, lat: float) -> int:
    zone = min(60, max(1, int((lon + 180.0) // 6.0) + 1))
    return (32600 if lat >= 0.0 else 32700) + zone


def _water_sampler(
    port: dict[str, Any], map_path: Path
) -> tuple[VectorMap, Transformer, Transformer]:
    map_v2 = read_contract(map_path, MapV2)
    epsg = _utm_epsg(float(port["anchor_longitude_deg"]), float(port["anchor_latitude_deg"]))
    vector_map = VectorMap.from_map_v2(map_v2, target_crs=f"EPSG:{epsg}")
    forward = Transformer.from_crs("EPSG:4326", vector_map.crs, always_xy=True)
    inverse = Transformer.from_crs(vector_map.crs, "EPSG:4326", always_xy=True)
    return vector_map, forward, inverse


def _sample_placements(
    port: dict[str, Any], map_path: Path, *, count: int, port_split: str
) -> list[dict[str, object]]:
    vector_map, forward, _ = _water_sampler(port, map_path)
    anchor_lon = float(port["anchor_longitude_deg"])
    anchor_lat = float(port["anchor_latitude_deg"])
    rng = np.random.default_rng(stable_seed("20261025", port_split, port["port_id"], "placements"))
    placements: list[dict[str, object]] = []
    attempts = 0
    while len(placements) < count and attempts < count * 20_000:
        attempts += 1
        bearing = float(rng.uniform(0.0, 360.0))
        distance = PLACEMENT_RADIUS_M * math.sqrt(float(rng.random()))
        lon, lat, _ = _GEOD.fwd(anchor_lon, anchor_lat, bearing, distance)
        x, y = forward.transform(lon, lat)
        if vector_map.point_water_status((x, y)) != "water":
            continue
        placement_id = f"placement-{len(placements) + 1:03d}"
        placements.append(
            {
                "placement_id": placement_id,
                "longitude_deg": float(lon),
                "latitude_deg": float(lat),
                "anchor_distance_m": float(distance),
                "sampling_bearing_deg": bearing,
                "reference_map_sha256": sha256_file(map_path),
            }
        )
    if len(placements) != count:
        raise ValueError(
            f"Only found {len(placements)} reference-water placements within 3 km for "
            f"{port} after {attempts} attempts using {map_path}."
        )
    return placements


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def create_plan(
    output_dir: Path,
    *,
    split_manifest_path: Path | None = None,
    placements_per_port: int = INITIAL_PLACEMENTS,
) -> tuple[Path, Path]:
    if placements_per_port != INITIAL_PLACEMENTS:
        raise ValueError("Plans start with 25 placements; support evidence controls extension.")
    split_manifest: dict[str, Any] = (
        json.loads(split_manifest_path.read_text(encoding="utf-8"))
        if split_manifest_path
        else build_split_manifest()
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    split_path = output_dir / "splits.json"
    _atomic_json(split_path, split_manifest)
    port_by_id = {str(port["port_id"]): port for port in split_manifest["ports"]}
    trial_rows: list[dict[str, Any]] = []
    for port_id in sorted(port_by_id):
        port = port_by_id[port_id]
        split = str(port["effective_split"])
        for placement_index in range(placements_per_port):
            placement_id = f"placement-{placement_index + 1:03d}"
            for realization in range(REALIZATIONS):
                for range_m in RANGES_M:
                    physical_seed = stable_seed(
                        20261025, split, port_id, placement_id, realization, int(range_m)
                    )
                    for profile_name in STRESS_PROFILES:
                        if profile_name != "nominal" and (placement_index >= 5 or realization != 0):
                            continue
                        for report_types in LABEL_MODES:
                            type_mode = "on" if report_types else "off"
                            trial_id = (
                                f"{split}/{port_id}/{placement_id}/r{realization}/"
                                f"range{int(range_m)}m/types{type_mode}/{profile_name}"
                            )
                            trial_rows.append(
                                {
                                    "trial_id": trial_id,
                                    "split": split,
                                    "port_id": port_id,
                                    "source_site_id": port["source_site_id"],
                                    "placement_id": placement_id,
                                    "realization": realization,
                                    "range_m": int(range_m),
                                    "report_types": report_types,
                                    "physical_seed": physical_seed,
                                    "sensor_condition": profile_name,
                                    "profile_parameters": json.dumps(
                                        STRESS_PROFILES[profile_name], sort_keys=True
                                    ),
                                    "status": "planned",
                                }
                            )
    trials_path = output_dir / "trials.csv"
    _write_table(trials_path, trial_rows)
    config = {
        "schema": "accuracy-suite-plan-v1",
        "base_seed": 20261025,
        "source_commit": _git_head(),
        "split_manifest": split_path.name,
        "split_manifest_sha256": sha256_file(split_path),
        "trial_table": trials_path.name,
        "trial_table_sha256": sha256_file(trials_path),
        "ranges_m": list(RANGES_M),
        "type_labels": list(LABEL_MODES),
        "stress_profiles": STRESS_PROFILES,
        "stress_placements_per_port": 5,
        "stress_realizations": [0],
        "stress_six_gate_reporting": "nominal, each stress profile, and pooled separately",
        "realizations_per_placement": REALIZATIONS,
        "initial_placements_per_port": placements_per_port,
        "maximum_placements_per_port": MAX_PLACEMENTS,
        "placement_radius_m": PLACEMENT_RADIUS_M,
        "seed_tuple": [20261025, "split", "port", "placement", "realization", "range_m"],
        "seed_excludes": ["algorithm", "report_types"],
        "trial_count": len(trial_rows),
        "heldout_sealed": True,
    }
    config_path = output_dir / "config.json"
    _atomic_json(config_path, config)
    return config_path, trials_path


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def _write_table(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    columns = list(rows[0])
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _read_table(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _terminate_process_group(process: subprocess.Popen[str], *, grace_s: float = 1.0) -> None:
    """Stop and reap a command plus descendants in its isolated process group."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=grace_s)
    except subprocess.TimeoutExpired:
        pass
    # The session leader can exit while a descendant remains alive with its
    # output streams closed. Always signal the original process group after
    # the grace interval, then wait for the leader and drain its pipes.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.communicate()


def _run_process(argv: list[str], *, cwd: Path, timeout_s: float) -> dict[str, Any]:
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        return {
            "status": "crash",
            "returncode": None,
            "elapsed_s": time.monotonic() - started,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
        }
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_group(process)
        return {
            "status": "timeout",
            "returncode": process.returncode,
            "elapsed_s": time.monotonic() - started,
            "stdout": _decode_output(exc.stdout),
            "stderr": _decode_output(exc.stderr),
            "process_group_terminated": True,
        }
    return {
        "status": "completed" if process.returncode == 0 else "failed",
        "returncode": process.returncode,
        "elapsed_s": time.monotonic() - started,
        "stdout": stdout,
        "stderr": stderr,
        "process_group_terminated": False,
    }


def _decode_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def _format_argv(template: list[str], values: dict[str, Any]) -> list[str]:
    return [
        part.format_map({key: str(value) for key, value in values.items()}) for part in template
    ]


def _format_search_argv(
    template: list[str], values: dict[str, Any], *, algorithm: str, timeout_s: float
) -> list[str]:
    """Format search inputs and bind the internal expansion deadline when enabled."""
    argv = _format_argv(template, values)
    options = _search_execution_options(template, algorithm=algorithm)
    if options["expand_assignments_enabled"]:
        if "--request-timeout-s" in argv:
            timeout_index = argv.index("--request-timeout-s")
            argv[timeout_index + 1] = str(timeout_s)
        else:
            argv.extend(("--request-timeout-s", str(timeout_s)))
    elif "--request-timeout-s" in argv:
        timeout_index = argv.index("--request-timeout-s")
        del argv[timeout_index : timeout_index + 2]
    return argv


def _load_command_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "search_argv",
        "evaluator_argv",
        "search_catalog",
        "search_map",
        "search_config",
        "search_domain_scope",
        "search_map_scope",
        "projected_crs",
    }
    missing = required - set(config)
    if missing:
        raise ValueError(f"Command config is missing required fields: {sorted(missing)}.")
    for name in ("search_argv", "evaluator_argv"):
        value = config[name]
        if (
            not isinstance(value, list)
            or not value
            or any(not isinstance(item, str) for item in value)
        ):
            raise ValueError(f"{name} must be a nonempty argv array of strings.")
    if config["search_domain_scope"] != "full_coast":
        raise ValueError("search_domain_scope must be full_coast.")
    if config["search_map_scope"] != "full_coast":
        raise ValueError("search_map_scope must be full_coast.")
    # Search receives the complete declared input set and no evaluator-only files.
    search_argv = config["search_argv"]
    required_search = (
        "{measurements_path}",
        "{search_catalog_path}",
        "{search_map_path}",
        "{search_config_path}",
        "{search_scope_manifest_path}",
        "{estimate_path}",
    )
    if any(not any(token in item for item in search_argv) for token in required_search):
        raise ValueError("search_argv must explicitly use each full-coast estimator input.")
    if not isinstance(config["projected_crs"], str) or not config["projected_crs"].strip():
        raise ValueError("projected_crs must be a nonempty string.")
    for option, expected_value in (
        ("--projected-crs", "{projected_crs}"),
        ("--scope-manifest", "{search_scope_manifest_path}"),
    ):
        try:
            option_index = search_argv.index(option)
        except ValueError as exc:
            raise ValueError(f"search_argv must include {option}.") from exc
        if option_index + 1 >= len(search_argv) or search_argv[option_index + 1] != expected_value:
            raise ValueError(f"search_argv must pass {expected_value} after {option}.")
    forbidden = ("{truth_path}", "{diagnostics_path}", "{support_path}", "{difficulty_path}")
    if any(token in item for item in search_argv for token in forbidden):
        raise ValueError("search_argv may not include evaluator-only file placeholders.")
    evaluator_argv = config["evaluator_argv"]
    required_evaluator = (
        "{evaluator_estimate_path}",
        "{truth_path}",
        "{support_path}",
        "{evaluation_path}",
        "{truth_coordinate_crs}",
    )
    if any(not any(token in item for item in evaluator_argv) for token in required_evaluator):
        raise ValueError(
            "evaluator_argv must use transformed evaluator estimate, truth, support, output, "
            "and coordinate CRS."
        )
    if "--coordinate-crs" not in evaluator_argv:
        raise ValueError("evaluator_argv must declare --coordinate-crs.")
    crs_index = evaluator_argv.index("--coordinate-crs")
    if (
        crs_index + 1 >= len(evaluator_argv)
        or evaluator_argv[crs_index + 1] != "{truth_coordinate_crs}"
    ):
        raise ValueError("evaluator_argv must pass {truth_coordinate_crs} after --coordinate-crs.")
    return config


def prepare_inputs(
    plan_path: Path,
    *,
    catalog_path: Path,
    map_cache: Path,
    input_root: Path,
    split: str = "development",
    placements_per_port: int = INITIAL_PLACEMENTS,
    only_trial_ids: set[str] | None = None,
    _allow_extension: bool = True,
) -> Path:
    """Generate paired v2 measurements and separate evaluator files via A15/A25."""
    if split not in {"development", "validation"}:
        raise ValueError("Input preparation is restricted to development and validation splits.")
    if not INITIAL_PLACEMENTS <= placements_per_port <= MAX_PLACEMENTS:
        raise ValueError("placements_per_port must be between 25 and 100.")
    if not catalog_path.is_file():
        raise FileNotFoundError(f"Full-coast CatalogV2 is required at {catalog_path}.")
    plan: dict[str, Any] = json.loads(plan_path.read_text(encoding="utf-8"))
    split_manifest_path = plan_path.parent / str(plan["split_manifest"])
    split_manifest: dict[str, Any] = json.loads(split_manifest_path.read_text(encoding="utf-8"))
    ports = {str(row["port_id"]): row for row in split_manifest["ports"]}
    catalog = read_contract(catalog_path, CatalogV2)
    catalog_hash = sha256_file(catalog_path)
    input_root.mkdir(parents=True, exist_ok=True)
    table_path = plan_path.parent / str(plan["trial_table"])
    table = _read_table(table_path)
    port_placements: dict[str, list[dict[str, Any]]] = {}
    placement_failures: dict[str, str] = {}
    maps: dict[str, tuple[Path, VectorMap]] = {}
    sensor_fields = {
        "detection_probability",
        "type_error_probability",
        "expected_clutter_reports",
        "bearing_sigma_deg",
    }
    preparation_path = input_root / split / "preparation.csv"
    preparation_attempts_path = input_root / split / "preparation-attempts.csv"
    preparation_rows = (
        {row["trial_id"]: row for row in _read_table(preparation_path)}
        if preparation_path.exists()
        else {}
    )
    preparation_attempts = (
        _read_table(preparation_attempts_path) if preparation_attempts_path.exists() else []
    )

    def prepare_row(row: dict[str, Any]) -> None:
        trial_id = row["trial_id"]
        port_id = row["port_id"]
        if port_id in placement_failures:
            raise RuntimeError(placement_failures[port_id])
        if port_id not in maps:
            port_number = int(port_id.rsplit("-", 1)[1])
            map_path = map_cache / f"noaa-enc-port-B{port_number}-15km.map-v2.json"
            try:
                vector_map, _, _ = _water_sampler(ports[port_id], map_path)
                port_placements[port_id] = _sample_placements(
                    ports[port_id],
                    map_path,
                    count=placements_per_port,
                    port_split=split,
                )
            except Exception as exc:
                sampling_path = input_root / split / "placement-sampling-attempts.csv"
                sampling_rows = _read_table(sampling_path) if sampling_path.exists() else []
                sampling_rows.append(
                    {
                        "attempt_id": f"{split}/{port_id}#{len(sampling_rows) + 1}",
                        "split": split,
                        "port_id": port_id,
                        "requested_placements": placements_per_port,
                        "placement_radius_m": PLACEMENT_RADIUS_M,
                        "reference_map": str(map_path),
                        "reference_map_sha256": (
                            sha256_file(map_path) if map_path.is_file() else ""
                        ),
                        "source_sha": _git_head(),
                        "status": "failed",
                        "failure": f"{type(exc).__name__}: {exc}",
                    }
                )
                _write_table(sampling_path, sampling_rows)
                placement_failures[port_id] = f"{type(exc).__name__}: {exc}"
                raise
            maps[port_id] = (map_path, vector_map)
        placement_number = int(row["placement_id"].rsplit("-", 1)[1])
        placement = port_placements[port_id][placement_number - 1]
        profile_name = row["sensor_condition"]
        profile = STRESS_PROFILES[profile_name]
        type_mode = "types-on" if row["report_types"] == "True" else "types-off"
        trial_dir = (
            input_root
            / split
            / port_id
            / str(row["placement_id"])
            / f"r{row['realization']}"
            / f"{row['range_m']}m"
            / type_mode
            / profile_name
        )
        measurement_path = trial_dir / "measurements.json"
        truth_path = trial_dir / "truth.json"
        diagnostics_path = trial_dir / "diagnostics.json"
        support_path = trial_dir / "support.json"
        difficulty_path = trial_dir / "difficulty.json"
        audit_path = trial_dir / "physical-identity-audit.json"
        truth_crs_path = trial_dir / "truth-crs.json"
        required_outputs = (
            measurement_path,
            truth_path,
            diagnostics_path,
            support_path,
            difficulty_path,
            audit_path,
            truth_crs_path,
        )
        if all(path.is_file() for path in required_outputs):
            cached_audit = json.loads(audit_path.read_text(encoding="utf-8"))
            if (
                cached_audit.get("catalog_sha256") == catalog_hash
                and cached_audit.get("reference_map_sha256") == sha256_file(maps[port_id][0])
                and cached_audit.get("simulator_seed") == int(row["physical_seed"])
                and cached_audit.get("stress_profile") == profile_name
            ):
                return
        trial_seed = int(row["physical_seed"])
        sensor_values: dict[str, Any] = {
            name: profile[name] for name in sensor_fields if name in profile
        }
        sensor_values.update(
            visibility_radius_m=float(row["range_m"]),
            report_type_labels=row["report_types"] == "True",
        )
        sensor = SensorConfigurationV2(**sensor_values)
        mismatch_values: dict[str, Any] = {
            name: profile[name]
            for name in (
                "heading_bias_deg",
                "catalog_displacement_east_m",
                "catalog_displacement_north_m",
            )
            if name in profile
        }
        observation_map = None
        if profile_name == "map_buffer":
            observation_map = _buffered_observation_map(maps[port_id][0], maps[port_id][1])
        mismatch_values["observation_map"] = observation_map
        scene = PhysicalSceneV2(catalog)
        observer = KnownWaterObserver(
            longitude_deg=float(placement["longitude_deg"]),
            latitude_deg=float(placement["latitude_deg"]),
        )
        run = run_simulator_v2(
            scene,
            observer,
            maps[port_id][1],
            sensor,
            seed=trial_seed,
            mismatch=SimulatorMismatchConfig(**mismatch_values),
            trial_id=trial_id,
            timestamp_utc=catalog.timestamp_utc,
        )
        valid_water = maps[port_id][1].observer_valid(
            _project_observer(maps[port_id][1], placement)
        )
        eligible_groups, audit = _physical_support_groups(catalog, run.diagnostics)
        eligible_diagnostics = replace(
            run.diagnostics,
            groups=tuple(eligible_groups),
            detected_physical_group_count=len(eligible_groups),
        )
        support = build_support_manifest(
            valid_water,
            cast(Any, eligible_diagnostics),
            port_id=port_id,
            placement_id=row["placement_id"],
        )
        difficulty = evaluate_scene_difficulty(
            run.truth,
            run.measurements,
            cast(Any, eligible_diagnostics),
            valid_reference_water=valid_water,
            port_id=port_id,
            placement_id=row["placement_id"],
        )
        trial_dir.mkdir(parents=True, exist_ok=True)
        write_contract(measurement_path, run.measurements)
        write_contract(truth_path, run.truth)
        _atomic_json(
            truth_crs_path,
            {
                "schema_version": 1,
                "trial_id": trial_id,
                "reference_map_path": str(maps[port_id][0]),
                "reference_map_sha256": sha256_file(maps[port_id][0]),
                "coordinate_reference_system": maps[port_id][1].crs.to_string(),
                "coordinate_units": "metre",
                "axis_order": "east_north",
            },
        )
        _atomic_json(diagnostics_path, asdict(run.diagnostics))
        _atomic_json(support_path, {**asdict(support), "supported": support.supported})
        _atomic_json(difficulty_path, asdict(difficulty))
        _atomic_json(
            audit_path,
            {
                **audit,
                "catalog_sha256": catalog_hash,
                "reference_map_sha256": sha256_file(maps[port_id][0]),
                "simulator_seed": trial_seed,
                "stress_profile": profile_name,
            },
        )

    for row in table:
        if row["split"] != split:
            continue
        trial_id = row["trial_id"]
        if only_trial_ids is not None and trial_id not in only_trial_ids:
            continue
        try:
            prepare_row(row)
            status = "completed"
            failure = ""
        except Exception as exc:
            status = "failed"
            failure = f"{type(exc).__name__}: {exc}"
        port_number = int(row["port_id"].rsplit("-", 1)[1])
        reference_map = map_cache / f"noaa-enc-port-B{port_number}-15km.map-v2.json"
        attempt_number = 1 + sum(item["trial_id"] == trial_id for item in preparation_attempts)
        attempt = {
            "attempt_id": f"{trial_id}#{attempt_number}",
            "trial_id": trial_id,
            "status": status,
            "failure": failure,
            "source_sha": _git_head(),
            "catalog_sha256": catalog_hash,
            "reference_map_sha256": (sha256_file(reference_map) if reference_map.is_file() else ""),
            "physical_seed": row["physical_seed"],
            "sensor_condition": row["sensor_condition"],
            "profile_parameters": row["profile_parameters"],
            "measurements_sha256": _trial_hash(input_root, split, row, "measurements.json"),
            "truth_sha256": _trial_hash(input_root, split, row, "truth.json"),
            "support_sha256": _trial_hash(input_root, split, row, "support.json"),
            "difficulty_sha256": _trial_hash(input_root, split, row, "difficulty.json"),
            "truth_crs_sha256": _trial_hash(input_root, split, row, "truth-crs.json"),
        }
        preparation_attempts.append(attempt)
        preparation_rows[trial_id] = attempt
        _write_table(preparation_path, list(preparation_rows.values()))
        _write_table(preparation_attempts_path, preparation_attempts)
    if _allow_extension:
        while added_ids := _extend_nominal_plan_if_needed(
            plan_path,
            input_root=input_root,
            split=split,
        ):
            prepare_inputs(
                plan_path,
                catalog_path=catalog_path,
                map_cache=map_cache,
                input_root=input_root,
                split=split,
                placements_per_port=MAX_PLACEMENTS,
                only_trial_ids=set(added_ids),
                _allow_extension=False,
            )
    return input_root


def _trial_hash(input_root: Path, split: str, row: dict[str, Any], filename: str) -> str:
    type_mode = "types-on" if row["report_types"] == "True" else "types-off"
    path = (
        input_root
        / split
        / row["port_id"]
        / row["placement_id"]
        / f"r{row['realization']}"
        / f"{row['range_m']}m"
        / type_mode
        / row["sensor_condition"]
        / filename
    )
    return sha256_file(path) if path.is_file() else ""


def _extend_nominal_plan_if_needed(
    plan_path: Path,
    *,
    input_root: Path,
    split: str,
) -> list[str]:
    """Add one deterministic 25-placement batch when split support is below 100."""
    plan: dict[str, Any] = json.loads(plan_path.read_text(encoding="utf-8"))
    table_path = plan_path.parent / str(plan["trial_table"])
    rows = _read_table(table_path)
    existing = {row["trial_id"] for row in rows}
    max_placement: dict[str, int] = {}
    initial_rows_by_port: dict[str, int] = {}
    supported_units: dict[tuple[int, str], set[tuple[str, str, str]]] = {}
    incomplete_initial_support = False
    for row in rows:
        if row["split"] != split or row["sensor_condition"] != "nominal":
            continue
        port_id = row["port_id"]
        placement_index = int(row["placement_id"].rsplit("-", 1)[1])
        max_placement[port_id] = max(max_placement.get(port_id, 0), placement_index)
        if placement_index > INITIAL_PLACEMENTS:
            continue
        initial_rows_by_port[port_id] = initial_rows_by_port.get(port_id, 0) + 1
        support_path = (
            input_root
            / split
            / port_id
            / row["placement_id"]
            / f"r{row['realization']}"
            / f"{row['range_m']}m"
            / ("types-on" if row["report_types"] == "True" else "types-off")
            / "nominal"
            / "support.json"
        )
        if not support_path.is_file():
            incomplete_initial_support = True
            continue
        support = json.loads(support_path.read_text(encoding="utf-8"))
        if support.get("supported") is True:
            gate = (int(row["range_m"]), row["report_types"])
            # The unit within a gate is a physical placement-realization. Paired
            # report-label rows belong to separate gates and are never pooled.
            supported_units.setdefault(gate, set()).add(
                (port_id, row["placement_id"], row["realization"])
            )
    # Wait for complete nominal support across every port in this split before
    # deciding whether any additional scenes are needed.
    expected_initial_rows_per_port = (
        INITIAL_PLACEMENTS * REALIZATIONS * len(RANGES_M) * len(LABEL_MODES)
    )
    if any(count != expected_initial_rows_per_port for count in initial_rows_by_port.values()):
        incomplete_initial_support = True
    split_manifest_name = plan.get("split_manifest")
    if split_manifest_name:
        split_manifest_path = plan_path.parent / str(split_manifest_name)
        if split_manifest_path.is_file():
            split_manifest = json.loads(split_manifest_path.read_text(encoding="utf-8"))
            expected_ports = set(split_manifest["split_port_ids"][split])
            if set(initial_rows_by_port) != expected_ports:
                incomplete_initial_support = True
    if incomplete_initial_support:
        return []
    deficient_gates = [
        (range_m, report_types)
        for range_m in RANGES_M
        for report_types in ("True", "False")
        if len(supported_units.get((int(range_m), report_types), set())) < 100
    ]
    if not deficient_gates:
        return []
    eligible_ports = [
        port_id for port_id in sorted(max_placement) if max_placement[port_id] < MAX_PLACEMENTS
    ]
    if not eligible_ports:
        return []
    # Extend one port at a time in stable order. Preparation then computes the
    # new A25 support before another 25-placement batch can be considered.
    port_id = eligible_ports[0]
    first_placement = max_placement[port_id] + 1
    last_placement = min(first_placement + 24, MAX_PLACEMENTS)
    added: list[dict[str, Any]] = []
    for placement_index in range(first_placement, last_placement + 1):
        placement_id = f"placement-{placement_index:03d}"
        for realization in range(REALIZATIONS):
            for range_m in RANGES_M:
                seed = stable_seed(
                    20261025,
                    split,
                    port_id,
                    placement_id,
                    realization,
                    int(range_m),
                )
                for report_types in LABEL_MODES:
                    trial_id = (
                        f"{split}/{port_id}/{placement_id}/r{realization}/"
                        f"range{int(range_m)}m/types{'on' if report_types else 'off'}/nominal"
                    )
                    if trial_id in existing:
                        continue
                    added.append(
                        {
                            "trial_id": trial_id,
                            "split": split,
                            "port_id": port_id,
                            "source_site_id": f"B{int(port_id.rsplit('-', 1)[1])}",
                            "placement_id": placement_id,
                            "realization": realization,
                            "range_m": int(range_m),
                            "report_types": report_types,
                            "physical_seed": seed,
                            "sensor_condition": "nominal",
                            "profile_parameters": "{}",
                            "status": "planned",
                        }
                    )
    if not added:
        return []
    rows.extend(added)
    _write_table(table_path, rows)
    plan["trial_table_sha256"] = sha256_file(table_path)
    plan["trial_count"] = len(rows)
    history = list(plan.get("support_extension_batches", []))
    history.append(
        {
            "split": split,
            "port_id": port_id,
            "first_placement": first_placement,
            "last_placement": last_placement,
            "deficient_gates_before_batch": [
                {"range_m": int(range_m), "report_types": report_types}
                for range_m, report_types in deficient_gates
            ],
            "supported_units_before_batch": {
                f"{range_m}m/types{'on' if report_types == 'True' else 'off'}": len(
                    supported_units.get((int(range_m), report_types), set())
                )
                for range_m, report_types in deficient_gates
            },
        }
    )
    plan["support_extension_batches"] = history
    plan["extension_rule"] = (
        "split-level support below 100 in any range/label gate; add deterministic 25-placement "
        "batches to one port at a time, up to 100, and reevaluate from pre-search support"
    )
    _atomic_json(plan_path, plan)
    return [str(row["trial_id"]) for row in added]


def _buffered_observation_map(map_path: Path, base_map: VectorMap) -> VectorMap:
    from shapely import from_geojson, transform

    map_v2 = read_contract(map_path, MapV2)
    transformer = Transformer.from_crs(map_v2.crs, base_map.crs, always_xy=True)

    def projected(value: str | None):
        if value is None:
            return None
        geometry = from_geojson(value)
        return transform(geometry, transformer.transform, interleaved=False)

    land = projected(map_v2.land_geometry_geojson)
    coverage = projected(map_v2.coverage_geometry_geojson)
    unknown = projected(map_v2.unknown_geometry_geojson)
    return VectorMap(
        land.buffer(10.0) if land is not None else None,
        crs=base_map.crs,
        coverage_geometry=coverage,
        unknown_geometry=unknown,
    )


def _project_observer(vector_map: VectorMap, placement: dict[str, Any]) -> tuple[float, float]:
    epsg = vector_map.crs.to_epsg()
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    return transformer.transform(
        float(placement["longitude_deg"]), float(placement["latitude_deg"])
    )


def _physical_support_groups(
    catalog: CatalogV2, diagnostics: object
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Select detected representatives with proven physical linkage for support."""
    records = {record.key: record for record in catalog.records}
    groups = tuple(getattr(diagnostics, "groups", ()))
    entity_by_key: dict[str, str | None] = {}
    identity_limited_keys: list[str] = []
    for record in catalog.records:
        metadata = dict(record.metadata)
        provenance = dict(record.provenance)
        entity = metadata.get("physical_entity_id", provenance.get("physical_entity_id"))
        if (
            metadata.get("identity_ambiguous") is True
            or metadata.get("physical_linkage_status") == "unresolved"
        ):
            entity = None
        if not entity and record.key.startswith("mmsi:"):
            entity = record.key
        entity_by_key[record.key] = str(entity) if entity else None
        if entity is None:
            identity_limited_keys.append(record.key)

    selected: list[Any] = []
    observed: dict[str, set[str]] = {}
    identity_limited_groups: list[dict[str, Any]] = []
    seen_entities: set[str] = set()
    for group in groups:
        if not getattr(group, "detected", False):
            continue
        representative_key = getattr(group, "representative_catalog_key", None)
        if representative_key is None:
            continue
        representative_key = str(representative_key)
        entity = entity_by_key.get(representative_key)
        member_keys = tuple(getattr(group, "catalog_member_keys", ()))
        member_entities = sorted(
            {entity for key in member_keys if (entity := entity_by_key.get(key)) is not None}
        )
        if entity is None:
            identity_limited_groups.append(
                {
                    "representative_catalog_key": representative_key,
                    "catalog_member_keys": list(member_keys),
                    "resolved_background_physical_entity_ids": member_entities,
                }
            )
            continue
        observed.setdefault(entity, set()).add(representative_key)
        if entity not in seen_entities:
            selected.append(group)
            seen_entities.add(entity)
    collapsed = {entity: sorted(keys) for entity, keys in observed.items() if len(keys) > 1}
    audit = {
        "catalog_record_count": len(records),
        "catalog_with_physical_entity_id_count": len(records) - len(identity_limited_keys),
        "identity_limited_catalog_keys": identity_limited_keys,
        "detected_distinct_physical_entity_count": len(seen_entities),
        "detected_identity_limited_group_count": len(identity_limited_groups),
        "detected_identity_limited_groups": identity_limited_groups,
        "detected_duplicate_representations_collapsed": collapsed,
        "support_status": "lower_bound_from_resolved_representative_identity",
        "support_policy": (
            "count at most one post-merge detection per representative's resolved physical "
            "entity; a resolved background member does not make an identity-limited "
            "representative a proven detected counterpart"
        ),
        "physical_linkage_method": (
            "same-chart normalized-name complementary-component grouping within 20 m; "
            "heuristic and not externally validated"
        ),
        "physical_entity_by_catalog_key": entity_by_key,
    }
    return tuple(selected), audit


def _physical_identity_audit(catalog: CatalogV2, diagnostics: object) -> dict[str, Any]:
    """Report distinct support lower bound and retained identity-limited detections."""
    _, audit = _physical_support_groups(catalog, diagnostics)
    return audit


def _trial_search_config(base: dict[str, Any], row: dict[str, str]) -> dict[str, Any]:
    """Bind search range and type-label use to one physical trial."""
    if base.get("contract_type") != "SearchConfig":
        raise ValueError("SearchConfig must declare contract_type SearchConfig.")
    config = dict(base)
    config["max_range_m"] = float(row["range_m"])
    config["use_types"] = row["report_types"] == "True"
    return config


def _write_evaluator_estimate(
    estimate_path: Path,
    truth_path: Path,
    truth_crs_path: Path,
    output_path: Path,
    provenance_path: Path,
    *,
    trial_id: str,
    estimate_crs: str,
) -> dict[str, Any]:
    """Transform only a separate evaluator estimate into the source truth CRS."""
    truth_crs_document = json.loads(truth_crs_path.read_text(encoding="utf-8"))
    if (
        truth_crs_document.get("schema_version") != 1
        or truth_crs_document.get("trial_id") != trial_id
    ):
        raise ValueError("Truth CRS metadata is missing or does not match the trial.")
    source_crs = CRS.from_user_input(estimate_crs)
    target_crs = CRS.from_user_input(str(truth_crs_document["coordinate_reference_system"]))
    estimate = read_contract(estimate_path, LocalizationResult)
    transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
    transformed_hypotheses = []
    for hypothesis in estimate.hypotheses:
        east_m, north_m = transformer.transform(hypothesis.east_m, hypothesis.north_m)
        transformed_hypotheses.append(
            replace(hypothesis, east_m=float(east_m), north_m=float(north_m))
        )
    evaluator_estimate = replace(estimate, hypotheses=tuple(transformed_hypotheses))
    write_contract(output_path, evaluator_estimate)
    provenance = {
        "schema_version": 1,
        "trial_id": trial_id,
        "source_estimate_sha256": sha256_file(estimate_path),
        "source_truth_sha256": sha256_file(truth_path),
        "evaluator_estimate_sha256": sha256_file(output_path),
        "estimate_source_crs": source_crs.to_string(),
        "truth_coordinate_crs": target_crs.to_string(),
        "estimate_target_crs": target_crs.to_string(),
        "transform_method": "pyproj.Transformer.from_crs(always_xy=True)",
        "transformer_description": transformer.description,
        "transformer_definition": transformer.definition,
        "transformer_accuracy_m": float(transformer.accuracy),
        "axis_order": "xy",
        "truth_crs_metadata_sha256": sha256_file(truth_crs_path),
    }
    _atomic_json(provenance_path, provenance)
    return provenance


def _search_execution_options(search_argv: list[str], *, algorithm: str) -> dict[str, Any]:
    """Read the exact execution options whose values enter telemetry provenance."""
    values: dict[str, Any] = {
        "spacing_m": 250.0,
        "seed": 0,
        "seed_policy": "fixed",
        "wedge_error_deg": None,
        "wedge_tolerance_deg": 0.5,
        "wedge_cell_size_m": 250.0,
        "branch_max_regions": 999,
        "refine": True,
        "refine_modes_count": 8,
        "refine_initial_step_m": 100.0,
        "refine_iterations": 50,
        "expand_assignments_enabled": False,
        "expansion_local_radius_m": 1000.0,
        "expansion_max_nfev": 100,
        "expansion_max_branches": 5000,
        "expansion_request_timeout_s": 1800.0,
        "device_ids": [],
    }
    value_options = {
        "--spacing-m": ("spacing_m", float),
        "--seed": ("seed", int),
        "--seed-policy": ("seed_policy", str),
        "--max-grid-bearing-error-deg": ("wedge_error_deg", float),
        "--wedge-tolerance-deg": ("wedge_tolerance_deg", float),
        "--wedge-cell-size-m": ("wedge_cell_size_m", float),
        "--branch-max-regions": ("branch_max_regions", int),
        "--refine-modes": ("refine_modes_count", int),
        "--refine-initial-step-m": ("refine_initial_step_m", float),
        "--refine-iterations": ("refine_iterations", int),
        "--expansion-local-radius-m": ("expansion_local_radius_m", float),
        "--expansion-max-nfev": ("expansion_max_nfev", int),
        "--expansion-max-branches": ("expansion_max_branches", int),
        "--request-timeout-s": ("expansion_request_timeout_s", float),
    }
    index = 0
    while index < len(search_argv):
        token = search_argv[index]
        if token in value_options:
            if index + 1 >= len(search_argv):
                raise ValueError(f"Search command option {token} has no value.")
            key, converter = value_options[token]
            raw = search_argv[index + 1]
            if "{" in raw or "}" in raw:
                raise ValueError(f"Search execution option {token} must be concrete before search.")
            values[key] = converter(raw)
            index += 2
            continue
        if token == "--expand-assignments":
            values["expand_assignments_enabled"] = True
            index += 1
            continue
        if token == "--device-id":
            if index + 1 >= len(search_argv):
                raise ValueError("Search command --device-id has no value.")
            values["device_ids"].append(int(search_argv[index + 1]))
            index += 2
            continue
        if token == "--no-refine":
            values["refine"] = False
            index += 1
            continue
        if token == "--algorithm":
            if index + 1 >= len(search_argv) or search_argv[index + 1] != algorithm:
                raise ValueError("Search command algorithm must match SearchConfig.algorithm.")
            index += 2
            continue
        if token.startswith("--"):
            if token not in {
                "--schema-version",
                "--measurements",
                "--catalog",
                "--map",
                "--config",
                "--projected-crs",
                "--scope-manifest",
                "--output",
                "--telemetry-output",
            }:
                raise ValueError(f"Unbound search command option {token}.")
            if index + 1 >= len(search_argv):
                raise ValueError(f"Search command option {token} has no value.")
            index += 2
            continue
        index += 1
    radius = values["expansion_local_radius_m"]
    if not math.isfinite(radius) or radius <= 0.0:
        raise ValueError("Search command --expansion-local-radius-m must be finite and positive.")
    if not 1 <= values["expansion_max_nfev"] <= 100:
        raise ValueError("Search command --expansion-max-nfev must be in [1, 100].")
    if not 1 <= values["expansion_max_branches"] <= 5000:
        raise ValueError("Search command --expansion-max-branches must be in [1, 5000].")
    if (
        not math.isfinite(values["expansion_request_timeout_s"])
        or values["expansion_request_timeout_s"] <= 0.0
    ):
        raise ValueError("Search command --request-timeout-s must be finite and positive.")
    if not values["expand_assignments_enabled"]:
        values.pop("expansion_request_timeout_s")
    return values


def _formal_bindings(
    *,
    rows: list[dict[str, Any]],
    base_search_config: dict[str, Any],
    command_config: dict[str, Any],
    catalog_path: Path,
    map_path: Path,
    scope_manifest_path: Path,
    projected_crs: str,
    timeout_s: float,
) -> dict[str, dict[str, Any]]:
    """Freeze all six condition/context pairs using the search telemetry binding."""
    cli_options = _search_execution_options(
        command_config["search_argv"], algorithm=str(base_search_config["algorithm"])
    )
    if cli_options["expand_assignments_enabled"]:
        cli_options["expansion_request_timeout_s"] = timeout_s
    bindings: dict[str, dict[str, Any]] = {}
    for range_m in (1000, 5000, 10000):
        for use_types in (True, False):
            key = f"{range_m}m_types{'on' if use_types else 'off'}"
            trial_config = dict(base_search_config)
            trial_config["max_range_m"] = float(range_m)
            trial_config["use_types"] = use_types
            config = search_config_from_dict(trial_config)
            condition, context = _calibration_binding(
                config=config,
                algorithm=config.algorithm,
                projected_crs=projected_crs,
                catalog_path=catalog_path,
                map_path=map_path,
                scope_manifest_path=scope_manifest_path,
                **cli_options,
            )
            bindings[key] = {
                "calibration_condition": condition.as_dict(),
                "calibration_context": context.as_dict(),
            }
    if not rows:
        raise ValueError("Formal run has no trial table rows.")
    return bindings


def _execution_protocol(timeout_s: float) -> dict[str, Any]:
    return {
        "schema": "accuracy-execution-protocol-v1",
        "timeout_s": timeout_s,
        "timeout_scope": "per_command_subprocess",
        "deadline_clock": "monotonic_wall_clock_from_spawn",
        "timeout_status": "search_timeout",
        "termination_policy": {
            "start_new_session": True,
            "timeout_signal": "SIGTERM",
            "grace_s": 1.0,
            "escalation_signal": "SIGKILL",
            "reap_process_group": True,
        },
    }


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )
    return hashlib.sha256(payload).hexdigest()


def _run_identity(
    *,
    split: str,
    command_config_hash: str,
    plan_path: Path,
    trial_table_path: Path,
    catalog_path: Path,
    map_path: Path,
    search_config_path: Path,
    projected_crs: str,
    execution_protocol: dict[str, Any],
) -> str:
    identity = {
        "split": split,
        "command_config_sha256": command_config_hash,
        "plan_sha256": sha256_file(plan_path),
        "trial_table_sha256": sha256_file(trial_table_path),
        "catalog_sha256": sha256_file(catalog_path),
        "map_sha256": sha256_file(map_path),
        "search_config_sha256": sha256_file(search_config_path),
        "projected_crs": projected_crs,
        "execution_protocol": execution_protocol,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()[:24]


def _formal_run_identity(
    *,
    plan_sha256: str,
    eligibility_plan_sha256: str,
    eligibility_manifest_sha256: str,
    config_sha256: str,
    splits_sha256: str,
    trial_table_sha256: str,
    condition_contexts: dict[str, dict[str, Any]],
    execution_protocol: dict[str, Any],
) -> str:
    identity = {
        "plan_sha256": plan_sha256,
        "eligibility_plan_sha256": eligibility_plan_sha256,
        "eligibility_manifest_sha256": eligibility_manifest_sha256,
        "config_sha256": config_sha256,
        "splits_sha256": splits_sha256,
        "trial_table_sha256": trial_table_sha256,
        "condition_contexts": condition_contexts,
        "execution_protocol": execution_protocol,
    }
    payload = json.dumps(
        {"schema": "accuracy-formal-run-identity-v1", **identity},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _reject_execution_protocol_mismatch(
    run_root: Path,
    *,
    split: str,
    command_config_sha256: str,
    plan_sha256: str,
    catalog_sha256: str,
    map_sha256: str,
    eligibility_manifest_sha256: str,
    execution_protocol_sha256: str,
) -> None:
    if not run_root.exists():
        return
    for manifest_path in run_root.glob("*/run-manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        same_run_inputs = (
            manifest.get("split") == split
            and manifest.get("command_config_sha256") == command_config_sha256
            and manifest.get("plan_sha256") == plan_sha256
            and manifest.get("search_catalog_sha256") == catalog_sha256
            and manifest.get("search_map_sha256") == map_sha256
            and manifest.get("eligibility_manifest_sha256", "") == eligibility_manifest_sha256
        )
        old_protocol_hash = manifest.get("execution_protocol_sha256")
        if same_run_inputs and old_protocol_hash != execution_protocol_sha256:
            raise ValueError(
                "Existing run uses a different or unbound execution protocol; use a separate "
                "run root to change timeout semantics."
            )


def _formal_trial_rows(
    *,
    rows: list[dict[str, Any]],
    split: str,
    snapshot: EligibilitySnapshot,
    condition_contexts: dict[str, dict[str, Any]],
    execution_protocol_sha256: str,
) -> list[dict[str, Any]]:
    eligibility_by_id = {item.trial_id: item for item in snapshot.all_trials}
    result: list[dict[str, Any]] = []
    for planned in rows:
        if planned["split"] != split:
            continue
        trial_id = str(planned["trial_id"])
        eligibility = eligibility_by_id.get(trial_id)
        if eligibility is None:
            raise ValueError(f"Eligibility snapshot does not contain planned trial {trial_id}.")
        range_m = int(planned["range_m"])
        report_types = planned["report_types"] == "True"
        key = f"{range_m}m_types{'on' if report_types else 'off'}"
        binding = condition_contexts[key]

        def canonical(value: Any) -> bytes:
            return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
                "utf-8"
            )

        result.append(
            {
                "trial_id": trial_id,
                "split": str(planned["split"]),
                "port_id": str(planned["port_id"]),
                "placement_id": str(planned["placement_id"]),
                "realization": str(planned["realization"]),
                "range_m": range_m,
                "report_types": report_types,
                "physical_seed": int(planned["physical_seed"]),
                "sensor_condition": str(planned["sensor_condition"]),
                "condition_context_key": key,
                "condition_context_sha256": hashlib.sha256(canonical(binding)).hexdigest(),
                "calibration_condition_sha256": hashlib.sha256(
                    canonical(binding["calibration_condition"])
                ).hexdigest(),
                "calibration_context_sha256": hashlib.sha256(
                    canonical(binding["calibration_context"])
                ).hexdigest(),
                "eligibility_record_sha256": hashlib.sha256(
                    canonical(asdict(eligibility))
                ).hexdigest(),
                "eligibility_status": eligibility.eligibility_status,
                "execution_protocol_sha256": execution_protocol_sha256,
                "supported": eligibility.supported,
                "support_sha256": eligibility.support_sha256 or "",
                "status": "missing",
                "failure": "search_not_attempted",
            }
        )
    return result


def run_trials(
    plan_path: Path,
    *,
    command_config_path: Path,
    input_root: Path,
    run_root: Path,
    split: str,
    timeout_s: float,
    heldout_manifest: Path | None = None,
    authorize_a38_release: bool = False,
    retry_failures: bool = False,
    eligibility_manifest_path: Path | None = None,
) -> Path:
    """Run independent estimator/evaluator programs with hash-bound artifacts."""
    if timeout_s <= 0.0 or not math.isfinite(timeout_s):
        raise ValueError("timeout_s must be finite and positive.")
    execution_protocol = _execution_protocol(timeout_s)
    execution_protocol_sha256 = _canonical_sha256(execution_protocol)
    if split == "heldout":
        if not authorize_a38_release or heldout_manifest is None:
            raise ValueError(
                "Held-out execution requires the frozen release manifest and A38 release flag."
            )
        if not heldout_manifest.is_file():
            raise FileNotFoundError(heldout_manifest)
        frozen = json.loads(heldout_manifest.read_text(encoding="utf-8"))
        if frozen.get("authorized_by") != "A38" or frozen.get("freeze_complete") is not True:
            raise ValueError("Held-out manifest is not an A38-authorized frozen release manifest.")
    elif authorize_a38_release or heldout_manifest is not None:
        raise ValueError("A38 held-out authorization applies only to the heldout split.")
    if split not in {"development", "validation", "heldout"}:
        raise ValueError("split must be development, validation, or heldout.")
    command_config = _load_command_config(command_config_path)
    plan: dict[str, Any] = json.loads(plan_path.read_text(encoding="utf-8"))
    trial_table_path = plan_path.parent / str(plan["trial_table"])
    rows = _read_table(trial_table_path)
    formal_snapshot: EligibilitySnapshot | None = None
    eligibility_manifest_hash = ""
    if eligibility_manifest_path is not None:
        formal_snapshot = verify_eligibility_manifest(
            eligibility_manifest_path, plan_path.parent, input_root, split
        )
        eligibility_manifest_hash = sha256_file(eligibility_manifest_path)
    command_config_hash = sha256_file(command_config_path)
    source_sha = _git_head()
    catalog_path = Path(str(command_config["search_catalog"])).expanduser().resolve()
    search_map_path = Path(str(command_config["search_map"])).expanduser().resolve()
    base_search_config_path = Path(str(command_config["search_config"])).expanduser().resolve()
    if not all(path.is_file() for path in (catalog_path, search_map_path, base_search_config_path)):
        raise FileNotFoundError(
            "Full-coast search catalog, coast map, and search config must exist."
        )
    base_search_config = json.loads(base_search_config_path.read_text(encoding="utf-8"))
    bounds_xy = base_search_config.get("bounds_xy")
    if (
        base_search_config.get("contract_type") != "SearchConfig"
        or not isinstance(bounds_xy, list)
        or len(bounds_xy) != 4
    ):
        raise ValueError(
            "SearchConfig must declare contract_type SearchConfig and four projected bounds_xy "
            "values."
        )
    projected_crs = str(command_config["projected_crs"])
    plan_config_sha256 = sha256_file(plan_path)
    trial_table_sha256 = sha256_file(trial_table_path)
    split_manifest_name = plan.get("split_manifest")
    splits_path = plan_path.parent / str(split_manifest_name) if split_manifest_name else Path()
    if formal_snapshot is not None and not split_manifest_name:
        raise ValueError("Formal plan must name its split manifest.")
    splits_sha256 = (
        sha256_file(splits_path) if split_manifest_name and splits_path.is_file() else ""
    )
    if formal_snapshot is not None and (
        formal_snapshot.config_sha256 != plan_config_sha256
        or formal_snapshot.trial_table_sha256 != trial_table_sha256
        or formal_snapshot.splits_sha256 != splits_sha256
    ):
        raise ValueError("Verified eligibility snapshot hashes differ from the current plan.")
    scope_manifest = {
        "schema_version": 1,
        "geographic_scope": "full_coast",
        "catalog_sha256": sha256_file(catalog_path),
        "map_sha256": sha256_file(search_map_path),
        "projected_crs": projected_crs,
        "bounds_xy_m": bounds_xy,
    }
    formal_contexts: dict[str, dict[str, Any]] = {}
    formal_run_identity = ""
    formal_trial_rows: list[dict[str, Any]] = []
    if formal_snapshot is not None:
        preflight_dir = run_root / ".preflight" / split / eligibility_manifest_hash
        preflight_dir.mkdir(parents=True, exist_ok=True)
        preflight_scope = preflight_dir / "scope-manifest.json"
        _atomic_json(preflight_scope, scope_manifest)
        formal_contexts = _formal_bindings(
            rows=rows,
            base_search_config=base_search_config,
            command_config=command_config,
            catalog_path=catalog_path,
            map_path=search_map_path,
            scope_manifest_path=preflight_scope,
            projected_crs=projected_crs,
            timeout_s=timeout_s,
        )
        formal_run_identity = _formal_run_identity(
            plan_sha256=plan_config_sha256,
            eligibility_plan_sha256=formal_snapshot.plan_sha256,
            eligibility_manifest_sha256=eligibility_manifest_hash,
            config_sha256=formal_snapshot.config_sha256,
            splits_sha256=formal_snapshot.splits_sha256,
            trial_table_sha256=formal_snapshot.trial_table_sha256,
            condition_contexts=formal_contexts,
            execution_protocol=execution_protocol,
        )
        formal_trial_rows = _formal_trial_rows(
            rows=rows,
            split=split,
            snapshot=formal_snapshot,
            condition_contexts=formal_contexts,
            execution_protocol_sha256=execution_protocol_sha256,
        )
        run_id = hashlib.sha256(
            f"{formal_run_identity}:{command_config_hash}".encode("ascii")
        ).hexdigest()[:24]
    else:
        run_id = _run_identity(
            split=split,
            command_config_hash=command_config_hash,
            plan_path=plan_path,
            trial_table_path=trial_table_path,
            catalog_path=catalog_path,
            map_path=search_map_path,
            search_config_path=base_search_config_path,
            projected_crs=projected_crs,
            execution_protocol=execution_protocol,
        )
    _reject_execution_protocol_mismatch(
        run_root,
        split=split,
        command_config_sha256=command_config_hash,
        plan_sha256=plan_config_sha256,
        catalog_sha256=sha256_file(catalog_path),
        map_sha256=sha256_file(search_map_path),
        eligibility_manifest_sha256=eligibility_manifest_hash,
        execution_protocol_sha256=execution_protocol_sha256,
    )
    run_dir = run_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / "trials.csv"
    attempts_path = run_dir / "attempts.csv"
    manifest_path = run_dir / "run-manifest.json"
    current = (
        {row["trial_id"]: row for row in _read_table(state_path)} if state_path.exists() else {}
    )
    attempts: list[dict[str, Any]] = _read_table(attempts_path) if attempts_path.exists() else []
    scope_manifest_path = run_dir / "scope-manifest.json"
    _atomic_json(scope_manifest_path, scope_manifest)
    scope_manifest_sha256 = sha256_file(scope_manifest_path)

    run_manifest: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "split": split,
        "command_config_sha256": command_config_hash,
        "plan_sha256": sha256_file(plan_path),
        "trial_table_sha256": sha256_file(trial_table_path),
        "search_catalog_sha256": sha256_file(catalog_path),
        "search_map_sha256": sha256_file(search_map_path),
        "base_search_config_sha256": sha256_file(base_search_config_path),
        "search_scope_manifest_sha256": scope_manifest_sha256,
        "projected_crs": projected_crs,
        "geographic_scope": "full_coast",
        "execution_protocol": execution_protocol,
        "execution_protocol_sha256": execution_protocol_sha256,
        "trials": [],
    }
    if formal_snapshot is not None:
        run_manifest.update(
            {
                "config_sha256": formal_snapshot.config_sha256,
                "splits_sha256": formal_snapshot.splits_sha256,
                "eligibility_plan_sha256": formal_snapshot.plan_sha256,
                "eligibility_manifest_sha256": eligibility_manifest_hash,
                "condition_contexts": formal_contexts,
                "run_identity_sha256": formal_run_identity,
            }
        )

    def flush_state() -> None:
        _write_table(state_path, list(current.values()))
        _write_table(attempts_path, attempts)
        merged = {row["trial_id"]: row for row in formal_trial_rows}
        merged.update(current)
        if formal_snapshot is not None:
            for row in merged.values():
                for path_key, hash_key in (
                    ("estimate_path", "estimate_sha256"),
                    ("telemetry_path", "telemetry_sha256"),
                    ("evaluator_estimate_path", "evaluator_estimate_sha256"),
                    ("evaluation_path", "evaluation_sha256"),
                    ("coordinate_provenance_path", "coordinate_provenance_sha256"),
                ):
                    if not row.get(hash_key):
                        row[path_key] = ""
        run_manifest["trials"] = [merged[key] for key in sorted(merged)]
        _atomic_json(manifest_path, run_manifest)

    if formal_snapshot is not None:
        flush_state()

    hash_names = (
        "measurements_sha256",
        "truth_sha256",
        "support_sha256",
        "truth_crs_sha256",
        "physical_identity_audit_sha256",
        "search_config_sha256",
        "command_config_sha256",
        "search_catalog_sha256",
        "search_map_sha256",
        "search_scope_manifest_sha256",
        "condition_context_sha256",
        "calibration_condition_sha256",
        "calibration_context_sha256",
        "eligibility_record_sha256",
        "execution_protocol_sha256",
    )
    missing_input_updates = 0
    for row in rows:
        if row["split"] != split:
            continue
        trial_id = row["trial_id"]
        type_mode = "types-on" if row["report_types"] == "True" else "types-off"
        input_trial_dir = (
            input_root
            / split
            / row["port_id"]
            / row["placement_id"]
            / f"r{row['realization']}"
            / f"{row['range_m']}m"
            / type_mode
            / row["sensor_condition"]
        )
        measurements_path = input_trial_dir / "measurements.json"
        truth_path = input_trial_dir / "truth.json"
        support_path = input_trial_dir / "support.json"
        diagnostics_path = input_trial_dir / "diagnostics.json"
        truth_crs_path = input_trial_dir / "truth-crs.json"
        audit_path = input_trial_dir / "physical-identity-audit.json"
        trial_search_config = _trial_search_config(base_search_config, row)
        trial_key_seed = {
            "trial_id": trial_id,
            "measurements_sha256": sha256_file(measurements_path)
            if measurements_path.is_file()
            else "",
            "truth_sha256": sha256_file(truth_path) if truth_path.is_file() else "",
            "support_sha256": sha256_file(support_path) if support_path.is_file() else "",
            "truth_crs_sha256": sha256_file(truth_crs_path) if truth_crs_path.is_file() else "",
            "search_config": trial_search_config,
        }
        trial_key = hashlib.sha256(
            json.dumps(trial_key_seed, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:24]
        artifact_dir = run_dir / "artifacts" / trial_key
        search_config_path = run_dir / "search-configs" / f"{trial_key}.json"
        _atomic_json(search_config_path, trial_search_config)
        search_config_hash = sha256_file(search_config_path)
        estimate_path = artifact_dir / "estimate.json"
        telemetry_path = artifact_dir / "estimate.json.telemetry.json"
        evaluator_estimate_path = artifact_dir / "evaluator-estimate.json"
        evaluation_path = artifact_dir / "evaluation.json"
        provenance_path = artifact_dir / "evaluation-coordinate-provenance.json"
        formal_base = (
            next((item for item in formal_trial_rows if item["trial_id"] == trial_id), {})
            if formal_snapshot is not None
            else {}
        )
        base: dict[str, Any] = {
            **formal_base,
            "trial_id": trial_id,
            "trial_key": trial_key,
            "split": split,
            "port_id": row["port_id"],
            "placement_id": row["placement_id"],
            "realization": (
                str(row["realization"]) if formal_snapshot is not None else row["realization"]
            ),
            "range_m": (int(row["range_m"]) if formal_snapshot is not None else row["range_m"]),
            "report_types": (
                row["report_types"] == "True"
                if formal_snapshot is not None
                else row["report_types"]
            ),
            "sensor_condition": row["sensor_condition"],
            "physical_seed": (
                int(row["physical_seed"]) if formal_snapshot is not None else row["physical_seed"]
            ),
            "source_sha": source_sha,
            "command_config_sha256": command_config_hash,
            "search_catalog_sha256": sha256_file(catalog_path),
            "search_map_sha256": sha256_file(search_map_path),
            "base_search_config_sha256": sha256_file(base_search_config_path),
            "search_config_path": str(search_config_path.relative_to(run_dir)),
            "search_config_sha256": search_config_hash,
            "search_scope_manifest_sha256": scope_manifest_sha256,
            "execution_protocol_sha256": execution_protocol_sha256,
            "measurements_path": str(measurements_path),
            "truth_path": str(truth_path),
            "support_path": str(support_path),
            "truth_crs_path": str(truth_crs_path),
            "estimate_path": str(estimate_path.relative_to(run_dir)),
            "telemetry_path": str(telemetry_path.relative_to(run_dir)),
            "evaluator_estimate_path": str(evaluator_estimate_path.relative_to(run_dir)),
            "evaluation_path": str(evaluation_path.relative_to(run_dir)),
            "coordinate_provenance_path": str(provenance_path.relative_to(run_dir)),
            "measurements_sha256": trial_key_seed["measurements_sha256"],
            "truth_sha256": trial_key_seed["truth_sha256"],
            "support_sha256": trial_key_seed["support_sha256"],
            "truth_crs_sha256": trial_key_seed["truth_crs_sha256"],
            "physical_identity_audit_sha256": sha256_file(audit_path)
            if audit_path.is_file()
            else "",
            "diagnostics_sha256": sha256_file(diagnostics_path)
            if diagnostics_path.is_file()
            else "",
            "difficulty_sha256": sha256_file(input_trial_dir / "difficulty.json")
            if (input_trial_dir / "difficulty.json").is_file()
            else "",
            "estimate_sha256": "",
            "telemetry_sha256": "",
            "evaluator_estimate_sha256": "",
            "evaluation_sha256": "",
            "coordinate_provenance_sha256": "",
        }
        prior = current.get(trial_id)
        if prior:
            same_inputs = all(prior.get(name) == base.get(name) for name in hash_names)
            output_hashes = {
                ("estimate_sha256", "estimate_path"),
                ("evaluator_estimate_sha256", "evaluator_estimate_path"),
                ("evaluation_sha256", "evaluation_path"),
                ("coordinate_provenance_sha256", "coordinate_provenance_path"),
            }
            if formal_snapshot is not None:
                output_hashes.add(("telemetry_sha256", "telemetry_path"))
            current_output_hashes = {
                hash_name: (
                    sha256_file(run_dir / str(prior[path_name]))
                    if prior.get(path_name) and (run_dir / str(prior[path_name])).is_file()
                    else ""
                )
                for hash_name, path_name in output_hashes
            }
            prior_outputs_valid = all(
                prior.get(hash_name) and current_output_hashes[hash_name] == prior[hash_name]
                for hash_name, _ in output_hashes
            )
            if same_inputs and prior.get("status") == "completed" and prior_outputs_valid:
                continue
            if same_inputs and prior.get("status") != "completed" and not retry_failures:
                continue
            if not same_inputs or (prior.get("status") == "completed" and not prior_outputs_valid):
                attempts.append(
                    {
                        "attempt_id": f"{trial_id}#resume-{len(attempts) + 1}",
                        "trial_id": trial_id,
                        "stage": "resume_validation",
                        "status": "hash_mismatch",
                        "mismatched_input_hashes_json": json.dumps(
                            {
                                name: {
                                    "previous": prior.get(name, ""),
                                    "current": base.get(name, ""),
                                }
                                for name in hash_names
                                if prior.get(name, "") != base.get(name, "")
                            },
                            sort_keys=True,
                        ),
                        "previous_output_hashes_json": json.dumps(
                            {name: prior.get(name, "") for name, _ in output_hashes},
                            sort_keys=True,
                        ),
                        "current_output_hashes_json": json.dumps(
                            current_output_hashes, sort_keys=True
                        ),
                    }
                )
        required_inputs = (
            measurements_path,
            truth_path,
            support_path,
            truth_crs_path,
            audit_path,
        )
        if not all(path.is_file() for path in required_inputs):
            current[trial_id] = {
                **base,
                "status": "missing_inputs",
                "failure": "simulator_input_missing",
            }
            missing_input_updates += 1
            if formal_snapshot is None or missing_input_updates % 64 == 0:
                flush_state()
            continue
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        if audit.get("catalog_sha256") != base["search_catalog_sha256"]:
            current[trial_id] = {
                **base,
                "status": "catalog_mismatch",
                "failure": "simulator_and_search_catalog_hash_mismatch",
            }
            flush_state()
            continue
        truth_crs_document = json.loads(truth_crs_path.read_text(encoding="utf-8"))
        if (
            truth_crs_document.get("schema_version") != 1
            or truth_crs_document.get("trial_id") != trial_id
            or truth_crs_document.get("reference_map_sha256")
            != sha256_file(Path(str(truth_crs_document.get("reference_map_path", ""))))
        ):
            current[trial_id] = {
                **base,
                "status": "invalid_truth_crs_metadata",
                "failure": "truth_crs_metadata_provenance_mismatch",
            }
            flush_state()
            continue
        truth_coordinate_crs = CRS.from_user_input(
            str(truth_crs_document["coordinate_reference_system"])
        ).to_string()
        artifact_dir.mkdir(parents=True, exist_ok=True)
        for output_path in (
            estimate_path,
            telemetry_path,
            evaluator_estimate_path,
            evaluation_path,
            provenance_path,
        ):
            output_path.unlink(missing_ok=True)
        values: dict[str, Any] = {
            "trial_id": trial_id,
            "measurements_path": measurements_path,
            "truth_path": truth_path,
            "truth_crs_path": truth_crs_path,
            "truth_coordinate_crs": truth_coordinate_crs,
            "diagnostics_path": diagnostics_path,
            "support_path": support_path,
            "search_catalog_path": catalog_path,
            "search_map_path": search_map_path,
            "search_config_path": search_config_path,
            "search_scope_manifest_path": scope_manifest_path,
            "projected_crs": projected_crs,
            "expansion_request_timeout_s": timeout_s,
            "estimate_path": estimate_path,
            "telemetry_path": telemetry_path,
            "evaluator_estimate_path": evaluator_estimate_path,
            "evaluation_path": evaluation_path,
            "coordinate_provenance_path": provenance_path,
        }
        search_argv = _format_search_argv(
            command_config["search_argv"],
            values,
            algorithm=str(base_search_config.get("algorithm", "grid")),
            timeout_s=timeout_s,
        )
        command_sets = (("search", search_argv),)
        status = "completed"
        failure = ""
        trial_attempts: list[dict[str, Any]] = []
        for stage, argv in command_sets:
            outcome = _run_process(argv, cwd=ROOT, timeout_s=timeout_s)
            attempt_id = f"{trial_id}#{len(attempts) + len(trial_attempts) + 1}"
            log_dir = run_dir / "logs" / hashlib.sha256(attempt_id.encode()).hexdigest()[:20]
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "stdout.txt").write_text(str(outcome.pop("stdout")), encoding="utf-8")
            (log_dir / "stderr.txt").write_text(str(outcome.pop("stderr")), encoding="utf-8")
            trial_attempts.append(
                {
                    "attempt_id": attempt_id,
                    "trial_id": trial_id,
                    "stage": stage,
                    "argv_json": json.dumps(argv),
                    "status": outcome["status"],
                    "returncode": outcome["returncode"],
                    "elapsed_s": outcome["elapsed_s"],
                    "process_group_terminated": bool(
                        outcome.get("process_group_terminated", False)
                    ),
                    "log_directory": str(log_dir.relative_to(run_dir)),
                }
            )
            if outcome["status"] != "completed":
                status = f"{stage}_{outcome['status']}"
                failure = f"{stage}_command_{outcome['status']}"
                break
        if status == "completed" and not estimate_path.is_file():
            status, failure = "missing_search_output", "estimate_missing"
        if status == "completed":
            try:
                _write_evaluator_estimate(
                    estimate_path,
                    truth_path,
                    truth_crs_path,
                    evaluator_estimate_path,
                    provenance_path,
                    trial_id=trial_id,
                    estimate_crs=projected_crs,
                )
            except (OSError, TypeError, ValueError) as exc:
                status, failure = "estimate_transform_failed", f"{type(exc).__name__}: {exc}"
        if status == "completed":
            argv = _format_argv(command_config["evaluator_argv"], values)
            outcome = _run_process(argv, cwd=ROOT, timeout_s=timeout_s)
            attempt_id = f"{trial_id}#{len(attempts) + len(trial_attempts) + 1}"
            log_dir = run_dir / "logs" / hashlib.sha256(attempt_id.encode()).hexdigest()[:20]
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "stdout.txt").write_text(str(outcome.pop("stdout")), encoding="utf-8")
            (log_dir / "stderr.txt").write_text(str(outcome.pop("stderr")), encoding="utf-8")
            trial_attempts.append(
                {
                    "attempt_id": attempt_id,
                    "trial_id": trial_id,
                    "stage": "evaluator",
                    "argv_json": json.dumps(argv),
                    "status": outcome["status"],
                    "returncode": outcome["returncode"],
                    "elapsed_s": outcome["elapsed_s"],
                    "log_directory": str(log_dir.relative_to(run_dir)),
                }
            )
            if outcome["status"] != "completed":
                status = f"evaluator_{outcome['status']}"
                failure = f"evaluator_command_{outcome['status']}"
            elif not evaluation_path.is_file():
                status, failure = "missing_evaluator_output", "evaluation_missing"
        attempts.extend(trial_attempts)
        row_result = {
            **base,
            "status": status,
            "failure": failure,
            "truth_coordinate_crs": truth_coordinate_crs,
            "estimate_sha256": sha256_file(estimate_path) if estimate_path.is_file() else "",
            "telemetry_sha256": sha256_file(telemetry_path) if telemetry_path.is_file() else "",
            "evaluator_estimate_sha256": (
                sha256_file(evaluator_estimate_path) if evaluator_estimate_path.is_file() else ""
            ),
            "evaluation_sha256": sha256_file(evaluation_path) if evaluation_path.is_file() else "",
            "coordinate_provenance_sha256": (
                sha256_file(provenance_path) if provenance_path.is_file() else ""
            ),
            "attempt_count": len(trial_attempts),
        }
        current[trial_id] = row_result
        flush_state()
    flush_state()
    return state_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    split_parser = subparsers.add_parser("splits", help="write the port split manifest")
    split_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT / "splits.json")
    split_parser.add_argument("--ports", type=Path, default=PORTS_CSV)
    plan_parser = subparsers.add_parser("plan", help="freeze the paired physical trial table")
    plan_parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    plan_parser.add_argument("--split-manifest", type=Path)
    plan_parser.add_argument("--placements-per-port", type=int, default=INITIAL_PLACEMENTS)
    prep_parser = subparsers.add_parser("prepare", help="generate A15 measurements and A25 support")
    prep_parser.add_argument("--plan", type=Path, default=DEFAULT_OUTPUT / "config.json")
    prep_parser.add_argument(
        "--catalog", type=Path, required=True, help="full-coast CatalogV2 JSON"
    )
    prep_parser.add_argument("--map-cache", type=Path, default=DEFAULT_MAP_CACHE)
    prep_parser.add_argument("--input-root", type=Path, default=DEFAULT_OUTPUT / "inputs")
    prep_parser.add_argument(
        "--split", choices=("development", "validation"), default="development"
    )
    prep_parser.add_argument("--placements-per-port", type=int, default=INITIAL_PLACEMENTS)
    run_parser = subparsers.add_parser(
        "run", help="run configured independent search and evaluator programs"
    )
    run_parser.add_argument("--plan", type=Path, default=DEFAULT_OUTPUT / "config.json")
    run_parser.add_argument(
        "--commands",
        type=Path,
        required=True,
        help="explicit argv templates; see benchmarks/accuracy/README.md",
    )
    run_parser.add_argument("--input-root", type=Path, default=DEFAULT_OUTPUT / "inputs")
    run_parser.add_argument("--run-root", type=Path, default=DEFAULT_OUTPUT / "runs")
    run_parser.add_argument(
        "--split", choices=("development", "validation", "heldout"), required=True
    )
    run_parser.add_argument("--timeout-s", type=float, default=1800.0)
    run_parser.add_argument("--retry-failures", action="store_true")
    run_parser.add_argument(
        "--eligibility-manifest",
        type=Path,
        required=True,
        help="A42 pre-search eligibility snapshot verified against the full plan and support root",
    )
    run_parser.add_argument("--frozen-release-manifest", type=Path)
    run_parser.add_argument("--authorize-a38-release", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "splits":
            _atomic_json(args.output, build_split_manifest(args.ports))
        elif args.command == "plan":
            config_path, trials_path = create_plan(
                args.output_dir,
                split_manifest_path=args.split_manifest,
                placements_per_port=args.placements_per_port,
            )
            print(f"plan={config_path}\ntrials={trials_path}")
        elif args.command == "prepare":
            prepare_inputs(
                args.plan,
                catalog_path=args.catalog,
                map_cache=args.map_cache,
                input_root=args.input_root,
                split=args.split,
                placements_per_port=args.placements_per_port,
            )
        else:
            result = run_trials(
                args.plan,
                command_config_path=args.commands,
                input_root=args.input_root,
                run_root=args.run_root,
                split=args.split,
                timeout_s=args.timeout_s,
                heldout_manifest=args.frozen_release_manifest,
                authorize_a38_release=args.authorize_a38_release,
                retry_failures=args.retry_failures,
                eligibility_manifest_path=args.eligibility_manifest,
            )
            print(result)
    except (OSError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
