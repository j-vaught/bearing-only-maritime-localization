"""Sequential, cache-only B1–B24 evaluation. Author J.C. Vaught."""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import traceback
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
REFERENCE = REPO / "docs/verification/charleston-coastwide-distinct-result.json"
SIM_SETTINGS = REPO / "runs/charleston-coastwise/bounded-vessel-simulation/simulation-settings.json"
OBSERVERS = REPO / "data/observer_locations.csv"
FIELDS = [
    "site_id",
    "port",
    "status",
    "reason",
    "started_utc",
    "ended_utc",
    "simulation_s",
    "search_s",
    "search_process_s",
    "worker_startup_s",
    "search_api_wall_s",
    "profile_finish_s",
    "gpu_buffers_reused",
    "search_worker_pid",
    "selection_s",
    "accuracy_s",
    "total_s",
    "shared_preparation_s",
    "positive_marker_reports",
    "total_reports",
    "matched_markers",
    "evaluated",
    "pruned",
    "expanded",
    "terminal",
    "consistent",
    "inconsistent",
    "unresolved",
    "land",
    "estimated_latitude_deg",
    "estimated_longitude_deg",
    "latitude_deg",
    "longitude_deg",
    "position_error_m",
    "base_cache_hit",
    "object_cache_hit",
    "prepared_world_reused",
    "gpus",
    "provenance_sha256",
    "attempt_directory",
]


def now():
    return datetime.now(UTC).isoformat()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".pending")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read(path):
    return json.loads(Path(path).read_text())


def sites():
    with OBSERVERS.open() as stream:
        rows = list(csv.DictReader(stream))
    if [row["site_id"] for row in rows] != [f"B{i}" for i in range(1, 25)]:
        raise ValueError("Authoritative observer CSV must contain B1 through B24 in order")
    return rows


def resume_matches(result, provenance):
    return result.get("status") == "completed" and result.get("provenance_sha256") == provenance


def eligible_aid_count(count, minimum=16):
    return count >= minimum


def command(module, *arguments):
    return [sys.executable, "-m", module, *map(str, arguments)]


def group_members(group):
    output = subprocess.check_output(["ps", "-eo", "pid=,pgid=,stat="], text=True)
    return [
        line.split()
        for line in output.splitlines()
        if int(line.split()[1]) == group and not line.split()[2].startswith("Z")
    ]


def clear_group(group):
    for sig, wait_s in ((signal.SIGTERM, 5), (signal.SIGKILL, 10)):
        if not group_members(group):
            return
        os.killpg(group, sig)
        deadline = time.perf_counter() + wait_s
        while group_members(group) and time.perf_counter() < deadline:
            time.sleep(0.1)
    if group_members(group):
        raise RuntimeError("Search process group still has live descendants after SIGKILL")


def stop_process(process):
    if process.is_alive():
        os.killpg(process.pid, signal.SIGTERM)
        process.join(5)
    if process.is_alive():
        os.killpg(process.pid, signal.SIGKILL)
        process.join(10)
    if process.is_alive():
        raise RuntimeError("Timed-out search child could not be stopped")
    clear_group(process.pid)


def bounded_command(argv, directory, name, timeout):
    start = time.perf_counter()
    save(directory / f"{name}-argv.json", argv)
    with (directory / f"{name}.log").open("wb") as log:
        process = subprocess.Popen(argv, cwd=REPO, stdout=log, stderr=log, start_new_session=True)
        (directory / f"{name}.pid").write_text(f"{process.pid}\n")
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            clear_group(process.pid)
            raise TimeoutError(f"{name} exceeded {timeout} seconds") from None
    clear_group(process.pid)
    if code:
        raise RuntimeError(f"{name} exited with code {code}; see {name}.log")
    return time.perf_counter() - start


def gpu_snapshot():
    return subprocess.check_output(
        [
            "nvidia-smi",
            "--query-gpu=index,name,memory.total,memory.used,utilization.gpu",
            "--format=csv,noheader,nounits",
        ],
        text=True,
    ).strip()


def resource_guard(owned_worker_pid=None):
    snapshot = gpu_snapshot()
    indexed = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader"], text=True
    )
    compute = subprocess.check_output(
        ["nvidia-smi", "--query-compute-apps=gpu_uuid,pid", "--format=csv,noheader"], text=True
    )
    if busy_devices(snapshot, indexed, compute, owned_worker_pid=owned_worker_pid):
        raise RuntimeError(
            f"Required GPU has an active compute context or over1GiB allocation. {snapshot}"
        )
    free = os.statvfs(REPO / "runs")
    if free.f_bavail * free.f_frsize < 20 * 1024**3:
        raise RuntimeError("Fewer than 20 GiB free on output storage")
    return {
        "gpu_snapshot": snapshot,
        "compute_contexts": compute,
        "guard": ("Only the owned worker may exceed1GiB; foreign contexts forbidden"),
        "owned_worker_pid": owned_worker_pid,
    }


def busy_devices(snapshot, indexed, compute, *, owned_worker_pid=None):
    index_uuids = {
        int(line.split(",")[0]): line.split(",")[1].strip() for line in indexed.splitlines()
    }
    contexts = {}
    for line in compute.splitlines():
        if not line.strip():
            continue
        uuid, pid = [value.strip() for value in line.split(",")]
        contexts.setdefault(uuid, set()).add(int(pid))
    busy = []
    for line in snapshot.splitlines():
        fields = [value.strip() for value in line.split(",")]
        index = int(fields[0])
        active = contexts.get(index_uuids[index], set())
        owned_only = owned_worker_pid is not None and active == {owned_worker_pid}
        if index in (0, 1, 2) and (
            active - ({owned_worker_pid} if owned_worker_pid is not None else set())
            or (int(fields[3]) > 1024 and not owned_only)
        ):
            busy.append(index)
    return busy


def forbid_build(*args, **kwargs) -> NoReturn:
    raise RuntimeError("Saved cache missing; rebuilding is forbidden")


def prepare(settings, *, refresh_aid_view=False):
    from shapely import from_geojson

    from maritime.schemas.inputs_v2 import CatalogV2, MapV2
    from maritime.search.gpu.resident import aid_view
    from maritime.search.gpu.resident.cache import load_or_prepare, preparation_identity
    from maritime.serialization import read_contract
    from maritime.simulator.mismatch import PhysicalSceneV2

    started = time.perf_counter()
    print(json.dumps({"stage": "saved_prepared_world_loading", "utc": now()}), flush=True)
    key = preparation_identity(
        Path(settings["map"]), Path(settings["catalog"]), settings["preparation"]
    )
    base_dir = (REPO / ".cache/resident-world").resolve()
    object_dir = base_dir.parent / "resident-aid-objects"
    world, hit = load_or_prepare(base_dir, key, forbid_build)
    source_catalog = read_contract(Path(settings["catalog"]), CatalogV2)
    print(json.dumps({"stage": "canonical_object_cache_loading", "utc": now()}), flush=True)
    original = aid_view._build_view
    try:
        if not refresh_aid_view:
            setattr(aid_view, "_build_view", forbid_build)
        world, object_hit, object_key = aid_view.load_or_canonicalize(
            object_dir, key, world, source_catalog
        )
    finally:
        setattr(aid_view, "_build_view", original)
    cache_load_s = time.perf_counter() - started
    map_start = time.perf_counter()
    print(json.dumps({"stage": "simulator_raw_layers_loading", "utc": now()}), flush=True)
    map_input = read_contract(Path(settings["map"]), MapV2)
    if map_input.crs != world.crs:
        raise ValueError("Simulator layer CRS differs from prepared search world")
    layers = {
        name: from_geojson(encoded) if encoded else None
        for name in ("land", "coverage", "unknown")
        if (encoded := getattr(map_input, name + "_geometry_geojson")) is not None
    }
    layers["crs"] = world.crs
    print(json.dumps({"stage": "canonical_simulator_scene_loading", "utc": now()}), flush=True)
    scene = PhysicalSceneV2(source_catalog)
    metadata = {
        "shared_preparation_s": time.perf_counter() - started,
        "cache_load_s": cache_load_s,
        "simulator_map_scene_load_s": time.perf_counter() - map_start,
        "base_cache_hit": hit,
        "object_cache_hit": object_hit,
        "object_refresh_allowed": refresh_aid_view,
        "base_cache_key": key,
        "object_cache_key": object_key,
        "catalog_records": len(source_catalog.records),
        "canonical_simulator_records": len(scene.catalog.records),
        "canonical_search_objects": len(world.objects),
        "cache_paths": [
            str(base_dir / f"{key}.npz"),
            str(object_dir / f"aid-view-{object_key}.npz"),
        ],
        "search_world_loading": (
            "Loaded once in supervisor, inherited read-only by one persistent search child"
        ),
        "device_upload": (
            "GPU map buffers survive sequential requests in one child; first search includes upload"
        ),
        "cpu_roles": [
            "simulation",
            "input_loading",
            "kernel_launch",
            "root_dispatch",
            "scalar_progress",
            "file_io",
            "posthoc_geodesic",
        ],
    }
    if "cupy" in sys.modules:
        raise RuntimeError("Supervisor must not initialize CUDA before forking")
    return world, layers, scene, metadata


def normalized_catalog_alignment(world, scene):
    """Verify the updated object catalog directly, without a reference-frame search."""
    import numpy as np
    from pyproj import Transformer

    from maritime.catalog_identity import COORDINATE_MERGE_RADIUS_M, RULE
    from maritime.schemas.object_types import is_visual_entity, normalize_object_type
    from maritime.simulator.runner import _offset_position

    records = {
        r.key: r for r in scene.catalog.records if is_visual_entity(r.normalized_type or r.raw_type)
    }
    if set(records) != set(world.object_keys):
        raise ValueError("Simulator/search normalized object keys differ")
    forward = Transformer.from_crs("EPSG:4326", world.crs, always_xy=True)
    max_error = 0.0
    for key, row in zip(world.object_keys, world.objects, strict=True):
        record = records[key]
        lon, lat = _offset_position(
            record.longitude_deg, record.latitude_deg, dict(record.metadata)
        )
        x, y = forward.transform(lon, lat)
        expected = np.asarray((x, y, lon, lat))
        error = float(np.max(np.abs(row[:2] - expected[:2])))
        if error > 1e-6 or not np.allclose(row[2:4], expected[2:4], rtol=0, atol=1e-12):
            raise ValueError(f"Simulator/search normalized position differs for {key}")
        kind = normalize_object_type(record.normalized_type or record.raw_type)
        if row[6] != (1 if kind == "physical_aid" else 2 if kind == "vessel" else 0):
            raise ValueError(f"Simulator/search normalized type differs for {key}")
        max_error = max(max_error, error)
    return {
        "passed": True,
        "rule": RULE,
        "merge_radius_m": COORDINATE_MERGE_RADIUS_M,
        "objects_checked": len(records),
        "max_projected_coordinate_error_m": max_error,
        "reference_frame_replayed": False,
        "search_executed": False,
    }


def simulate(row, layers, scene, simulation_settings, directory):
    from dataclasses import asdict

    import numpy as np
    from pyproj import Geod, Transformer
    from shapely import clip_by_rect, make_valid

    from maritime.geometry.vector_map import VectorMap
    from maritime.serialization import write_contract
    from maritime.simulator.mismatch import KnownWaterObserver, SensorConfigurationV2
    from maritime.simulator.runner import _offset_position, run_simulator_v2

    sensor = SensorConfigurationV2(**simulation_settings["sensor"])
    observer = KnownWaterObserver(float(row["longitude_deg"]), float(row["latitude_deg"]), 0.0)
    # Simulator-only crop follows the saved corrected Charleston generation.
    # All physical rays lie strictly inside these bounds; search remains global.
    forward = Transformer.from_crs("EPSG:4326", layers["crs"], always_xy=True)
    physical = [
        _offset_position(record.longitude_deg, record.latitude_deg, dict(record.metadata))
        for record in scene.catalog.records
    ]
    longitude, latitude = np.asarray(physical).T
    _, _, distances = Geod(ellps="WGS84").inv(
        np.full(len(longitude), observer.longitude_deg),
        np.full(len(latitude), observer.latitude_deg),
        longitude,
        latitude,
    )
    in_range = (distances > 0) & (distances <= sensor.visibility_radius_m)
    x, y = forward.transform(longitude[in_range], latitude[in_range])
    observer_xy = forward.transform(observer.longitude_deg, observer.latitude_deg)
    points = np.vstack((np.column_stack((x, y)), observer_xy))
    bounds = (*points.min(axis=0) - 1.0, *points.max(axis=0) + 1.0)
    clipped = {}
    for name in ("land", "coverage", "unknown"):
        source = layers.get(name)
        geometry = clip_by_rect(source, *bounds) if source is not None else None
        if geometry is not None and not geometry.is_valid:
            geometry = make_valid(geometry)
        clipped[name] = geometry if geometry is not None and not geometry.is_empty else None
    vector_map = VectorMap(
        clipped["land"],
        crs=layers["crs"],
        coverage_geometry=clipped["coverage"],
        unknown_geometry=clipped["unknown"],
    )
    save(
        directory / "simulation-map-crop.json",
        {
            "bounds_en_m": list(bounds),
            "positive_margin_m": 1.0,
            "physical_targets_in_range": int(np.count_nonzero(in_range)),
            "scope": "Simulator LOS only. Full search world and bounds unchanged",
        },
    )
    run = run_simulator_v2(
        scene,
        observer,
        vector_map,
        sensor,
        seed=simulation_settings["seed"],
        trial_id=f"{row['site_id']}-seed11",
    )
    write_contract(directory / "measurements.json", run.measurements)
    save(directory / "simulation-diagnostics.json", asdict(run.diagnostics))
    save(
        directory / "truth.json",
        {"latitude_deg": observer.latitude_deg, "longitude_deg": observer.longitude_deg},
    )
    markers = [
        m.bearing_deg for m in run.measurements.measurements if m.object_type == "physical_aid"
    ]
    return markers, len(run.measurements.measurements)


def check_search_resource_ceiling(directory):
    progress = directory / "search/progress.jsonl"
    if not progress.exists():
        return
    with progress.open("rb") as stream:
        stream.seek(max(0, progress.stat().st_size - 65536))
        lines = stream.read().splitlines()
    if not lines:
        return
    try:
        last = json.loads(lines[-1])
    except json.JSONDecodeError:
        return
    if last["terminal"] > 2_000_000 or last["evaluated"] > 50_000_000:
        raise RuntimeError(
            "Resource ceiling reached. 2 million terminal or 50 million evaluated cells"
        )


def geodesic_after_freeze(estimate_path, freeze_path, truth_path):
    from pyproj import Geod

    frozen = read(freeze_path)
    if digest(estimate_path) != frozen["estimate_sha256"]:
        raise ValueError("Estimate changed after freeze")
    estimate = read(estimate_path)
    if estimate["truth_used_for_selection"] or not estimate["full_region_search_complete"]:
        raise ValueError("Completed truth-independent estimate required")
    selected = estimate["estimate"]
    if selected is None or selected["status"] != 0 or selected["flags"] != 0:
        raise ValueError("No numerically consistent estimate")
    truth = read(truth_path)
    _, _, error = Geod(ellps="WGS84").inv(
        truth["longitude_deg"],
        truth["latitude_deg"],
        selected["longitude_deg"],
        selected["latitude_deg"],
    )
    return {
        "position_error_m": error,
        "method": "WGS84 ellipsoidal geodesic, no fitted alignment",
        "evaluated_utc": now(),
        "estimate_sha256": digest(estimate_path),
        "truth_used_to_select_or_refine": False,
    }


def summarize(output, records, expected_sites=24):
    save(output / "results.json", records)
    with (output / "results.csv.pending").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
        stream.flush()
        os.fsync(stream.fileno())
    (output / "results.csv.pending").replace(output / "results.csv")
    completed = [r for r in records if r["status"] == "completed"]
    save(
        output / "aggregate.json",
        {
            "updated_utc": now(),
            "recorded_sites": len(records),
            "expected_sites": expected_sites,
            "all_selected_sites_recorded": len(records) == expected_sites,
            "all24_recorded": len(records) == 24,
            "status_counts": dict(Counter(r["status"] for r in records)),
            "completed_count": len(completed),
            "mean_search_s_completed": sum(r["search_s"] for r in completed) / len(completed)
            if completed
            else None,
            "mean_error_m_completed": sum(r["position_error_m"] for r in completed) / len(completed)
            if completed
            else None,
        },
    )


def run_suite(args):
    os.chdir(REPO)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    lock = (output / "suite.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    (output / "supervisor.pid").write_text(f"{os.getpid()}\n")
    settings = read(REFERENCE)["settings"]
    settings.pop("measurements")
    settings["search"]["roots_per_task"] = 128
    settings["search"]["batch_cells"] = max(settings["search"]["batch_cells"], 128)
    simulation_settings = read(SIM_SETTINGS)
    all_rows = sites()
    selected = (
        set(args.site_ids.split(",")) if args.site_ids else {row["site_id"] for row in all_rows}
    )
    if not selected.issubset({row["site_id"] for row in all_rows}):
        raise ValueError("Unknown selected observer site")
    rows = [row for row in all_rows if row["site_id"] in selected]
    sources = sorted(
        set(
            [
                *REPO.glob("maritime/**/*.py"),
                *REPO.glob("maritime/**/*.cu"),
                REPO / "tools/select_resident_result.py",
                Path(__file__),
            ]
        )
    )
    provenance = {
        "settings": settings,
        "simulation": {
            "sensor": simulation_settings["sensor"],
            "seed": simulation_settings["seed"],
        },
        "seed_policy": "Fixed existing seed 11 for every site; keyed simulator streams unchanged",
        "observers_sha256": digest(OBSERVERS),
        "map_sha256": digest(Path(settings["map"])),
        "catalog_sha256": digest(Path(settings["catalog"])),
        "source_hashes": {str(p.relative_to(REPO)): digest(p) for p in sources},
        "search_timeout_s": args.search_timeout,
        "minimum_positive_aids": args.minimum_positive_aids,
        "object_refresh_allowed": args.refresh_aid_view,
        "catalog_validation": "normalized_catalog_alignment"
        if args.normalized_catalog_check
        else "saved_charleston_frame",
        "selected_sites": [row["site_id"] for row in rows],
        "resource_guard": "Only owned persistent search worker contexts permitted after startup",
        "gpu_residency": "One map upload per device for sequential suite requests",
        "preload_gpus": getattr(args, "preload_gpus", False),
        "aid_filter": "Detected physical_aid reports only; vessels and unknown reports excluded",
        "selection_timeout_s": 120,
        "terminal_ceiling": 2_000_000,
        "evaluated_ceiling": 50_000_000,
    }
    provenance_hash = identity(provenance)
    save(
        output / "manifest.json",
        {
            "created_utc": now(),
            "author": "J.C. Vaught",
            "source_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            "provenance_sha256": provenance_hash,
            "provenance": provenance,
            "hardware": {
                "gpus": gpu_snapshot(),
                "cpu": subprocess.check_output(["lscpu"], text=True),
            },
            "b24_coordinate_note": (
                "Authoritative CSV stores latitude 25.76, longitude -80.175; "
                "earlier 35.76 is not substituted"
            ),
            "suite_order": [r["site_id"] for r in rows],
            "truth_isolation": (
                "Search child receives prepared world, measured aid bearings and settings only; "
                "selector receives saved search only; truth read for error after hash freeze"
            ),
        },
    )
    save(output / "provenance" / f"{provenance_hash}.json", read(output / "manifest.json"))
    if args.dry_run:
        save(
            output / "dry-run.json",
            {
                "passed": True,
                "settings": settings,
                "simulator": simulation_settings,
                "order": [r["site_id"] for r in rows],
                "truth_fields_in_search_settings": False,
            },
        )
        return
    resource_guard()
    save(output / "status.json", {"stage": "shared_preparation", "updated_utc": now()})
    world, vector_map, scene, shared = prepare(settings, refresh_aid_view=args.refresh_aid_view)
    before = {
        p: {
            "sha256": digest(p),
            "size": Path(p).stat().st_size,
            "mtime_ns": Path(p).stat().st_mtime_ns,
        }
        for p in shared["cache_paths"]
    }
    save(output / "shared-preparation.json", shared)
    save(output / "cache-before.json", before)
    if args.normalized_catalog_check:
        save(
            output / "normalized-catalog-alignment.json", normalized_catalog_alignment(world, scene)
        )
    else:
        parity = output / "charleston-simulation-parity"
        parity.mkdir(exist_ok=True)
        regenerated, _ = simulate(all_rows[16], vector_map, scene, simulation_settings, parity)
        saved = read(REPO / "docs/verification/charleston-blind-measurements.json")
        prior = [
            m["bearing_deg"] for m in saved["measurements"] if m["object_type"] == "physical_aid"
        ]
        # Trial IDs differ, while physical reports and all noise draws must agree.
        parity_result = {
            "passed": regenerated == prior,
            "regenerated_markers": len(regenerated),
            "saved_markers": len(prior),
            "saved_measurements_sha256": digest(
                REPO / "docs/verification/charleston-blind-measurements.json"
            ),
        }
        save(output / "charleston-simulation-parity.json", parity_result)
        if not parity_result["passed"]:
            raise RuntimeError("Charleston simulation parity failed; no suite search launched")
    if args.preflight_only:
        save(output / "status.json", {"stage": "preflight_passed", "updated_utc": now()})
        return
    records = []
    aid_counts = []
    from maritime.search.gpu.resident.service import PersistentSearchService

    service = PersistentSearchService(world, settings["search"], output)
    preloaded = False
    try:
        for row in rows:
            site = row["site_id"]
            site_dir = output / site
            site_dir.mkdir(exist_ok=True)
            result_path = site_dir / "result.json"
            if result_path.exists() and resume_matches(read(result_path), provenance_hash):
                records.append(read(result_path))
                summarize(output, records, len(rows))
                continue
            directory = site_dir / datetime.now(UTC).strftime("attempt-%Y%m%dT%H%M%S%fZ")
            directory.mkdir()
            record = {
                **row,
                "status": "running",
                "started_utc": now(),
                "provenance_sha256": provenance_hash,
                "gpus": "0,1,2",
                "attempt_directory": str(directory),
                "shared_preparation_s": shared["shared_preparation_s"],
                "base_cache_hit": shared["base_cache_hit"],
                "object_cache_hit": shared["object_cache_hit"],
                "prepared_world_reused": True,
            }
            start = time.perf_counter()
            search_start = None
            save(output / "status.json", {"stage": "site", "site_id": site, "updated_utc": now()})
            save(result_path, record)
            try:
                save(
                    directory / "gpu-before.json",
                    {"snapshot": resource_guard(owned_worker_pid=service.pid)},
                )
                save(
                    output / "status.json",
                    {"stage": "simulation", "site_id": site, "updated_utc": now()},
                )
                print(
                    json.dumps({"stage": "simulation", "site_id": site, "utc": now()}), flush=True
                )
                sim_start = time.perf_counter()
                markers, total_reports = simulate(
                    row, vector_map, scene, simulation_settings, directory
                )
                record.update(
                    simulation_s=time.perf_counter() - sim_start,
                    positive_marker_reports=len(markers),
                    total_reports=total_reports,
                )
                aid_counts.append(
                    {
                        "site_id": site,
                        "positive_marker_reports": len(markers),
                        "eligible": eligible_aid_count(len(markers), args.minimum_positive_aids),
                    }
                )
                save(
                    output / "aid-counts-and-eligibility.json",
                    {
                        "minimum_positive_aids": args.minimum_positive_aids,
                        "counted_sites": aid_counts,
                    },
                )
                print(json.dumps({"stage": "aid_count_filter", **aid_counts[-1]}), flush=True)
                if not eligible_aid_count(len(markers), args.minimum_positive_aids):
                    record.update(
                        status="skipped_aid_count",
                        reason=(
                            f"Detected {len(markers)} positive aid reports; "
                            f"minimum is {args.minimum_positive_aids}"
                        ),
                    )
                    continue
                if not markers:
                    raise ValueError(
                        "Precondition failed. Positive chart-aid observations are required"
                    )
                if len(markers) > 256:
                    raise ValueError("Precondition failed. Solver accepts at most 256 aid reports")
                search_settings = {**settings, "measurements": str(directory / "measurements.json")}
                save(directory / "settings.json", search_settings)
                save(
                    output / "status.json",
                    {"stage": "full_coast_search", "site_id": site, "updated_utc": now()},
                )
                print(
                    json.dumps({"stage": "full_coast_search", "site_id": site, "utc": now()}),
                    flush=True,
                )
                service.start()
                if getattr(args, "preload_gpus", False) and not preloaded:
                    preload_dir = output / "gpu-preload"
                    preload_dir.mkdir()
                    save(output / "status.json", {"stage": "gpu_map_preload", "updated_utc": now()})
                    preload = service.prepare(preload_dir)
                    save(
                        output / "gpu-preload.json",
                        {**preload, "worker_startup_s": service.startup_s},
                    )
                    preloaded = True
                search_start = time.perf_counter()
                (directory / "search.pid").write_text(f"{service.pid}\n")
                response = service.search(
                    markers,
                    settings["marker_tolerance_deg"],
                    directory,
                    timeout_s=args.search_timeout,
                    progress_check=lambda: check_search_resource_ceiling(directory),
                )
                record["search_process_s"] = time.perf_counter() - search_start
                metadata = response["metadata"]
                record.update(
                    worker_startup_s=service.startup_s if metadata["cold_start"] else 0.0,
                    search_api_wall_s=metadata["api_wall_s"],
                    profile_finish_s=metadata["profile_finish_s"],
                    gpu_buffers_reused=metadata["gpu_buffers_reused"],
                    search_worker_pid=metadata["worker_pid"],
                )
                summary = response["summary"]
                record.update({key: summary[key] for key in ("evaluated", "pruned", "terminal")})
                record.update(
                    search_s=summary["elapsed_s"],
                    expanded=summary["evaluated"] - summary["pruned"] - summary["terminal"],
                )
                if not summary["complete"]:
                    raise RuntimeError("Full coast search incomplete")
                save(
                    output / "status.json",
                    {"stage": "selection", "site_id": site, "updated_utc": now()},
                )
                record["selection_s"] = bounded_command(
                    command(
                        "tools.select_resident_result",
                        directory / "search",
                        directory / "estimate.json",
                        "--device",
                        "0",
                    ),
                    directory,
                    "selection",
                    120,
                )
                freeze = {
                    "frozen_utc": now(),
                    "estimate_sha256": digest(directory / "estimate.json"),
                }
                save(directory / "estimate-freeze.json", freeze)
                estimate = read(directory / "estimate.json")
                counts = estimate["status_counts"]
                record.update(
                    consistent=counts["0"],
                    inconsistent=counts["1"],
                    unresolved=counts["2"],
                    land=counts["3"],
                )
                if estimate["estimate"] is None:
                    raise ValueError(
                        "No consistent center available after completed full coast search"
                    )
                selected = estimate["estimate"]
                record.update(
                    estimated_latitude_deg=selected["latitude_deg"],
                    estimated_longitude_deg=selected["longitude_deg"],
                    matched_markers=selected["matched_positive_markers"],
                )
                accuracy_start = time.perf_counter()
                accuracy = geodesic_after_freeze(
                    directory / "estimate.json",
                    directory / "estimate-freeze.json",
                    directory / "truth.json",
                )
                save(directory / "accuracy.json", accuracy)
                record.update(
                    position_error_m=accuracy["position_error_m"],
                    accuracy_s=time.perf_counter() - accuracy_start,
                    status="completed",
                )
            except TimeoutError as error:
                record.update(status="timeout", reason=str(error))
            except Exception as error:
                record.update(
                    status="invalid" if "Observer must lie" in str(error) else "failed",
                    reason=str(error),
                )
                (directory / "exception.txt").write_text(traceback.format_exc())
            finally:
                save(
                    directory / "worker-state.json",
                    {
                        "search_pid": service.pid,
                        "persistent": True,
                        "poisoned": service.poisoned,
                        "retained_between_sites": service.ready,
                        "verified_utc": now(),
                    },
                )
                if search_start is not None:
                    record.setdefault("search_process_s", time.perf_counter() - search_start)
                    progress = directory / "search/progress.jsonl"
                    if progress.exists():
                        with progress.open("rb") as stream:
                            stream.seek(max(0, progress.stat().st_size - 65536))
                            lines = stream.read().splitlines()
                        for line in reversed(lines):
                            try:
                                last = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            for key in ("evaluated", "pruned", "terminal"):
                                record.setdefault(key, last[key])
                            record.setdefault("search_s", last["elapsed_s"])
                            record.setdefault(
                                "expanded", last["evaluated"] - last["pruned"] - last["terminal"]
                            )
                            break
                record.update(ended_utc=now(), total_s=time.perf_counter() - start)
                save(result_path, record)
                save(directory / "result.json", record)
                records.append(record)
                summarize(output, records, len(rows))
                print(
                    json.dumps(
                        {
                            key: record.get(key)
                            for key in (
                                "site_id",
                                "status",
                                "search_s",
                                "position_error_m",
                                "reason",
                            )
                        }
                    ),
                    flush=True,
                )
            if service.poisoned:
                remaining = [pending["site_id"] for pending in rows[rows.index(row) + 1 :]]
                save(
                    output / "status.json",
                    {
                        "stage": "halted_worker_failure",
                        "site_id": site,
                        "reason": record.get("reason"),
                        "remaining_sites": remaining,
                        "updated_utc": now(),
                    },
                )
                break
    finally:
        try:
            service.close()
            if service.pid is not None:
                clear_group(service.pid)
            save(
                output / "worker-cleanup.json",
                {
                    "search_pid": service.pid,
                    "live_group_members": group_members(service.pid) if service.pid else [],
                    "verified_utc": now(),
                },
            )
        finally:
            after = {
                p: {
                    "sha256": digest(p),
                    "size": Path(p).stat().st_size,
                    "mtime_ns": Path(p).stat().st_mtime_ns,
                }
                for p in shared["cache_paths"]
            }
            save(output / "cache-after.json", after)
            if before != after:
                raise RuntimeError("Immutable cache fingerprints changed")
    if service.poisoned:
        return
    save(
        output / "status.json",
        {
            "stage": "finished",
            "updated_utc": now(),
            "all24_recorded": len(records) == 24,
            "cache_unchanged": True,
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--search-timeout", type=int, default=600)
    parser.add_argument("--minimum-positive-aids", type=int, default=16)
    parser.add_argument(
        "--site-ids", help="Comma-separated authoritative sites for an explicit retry"
    )
    parser.add_argument(
        "--refresh-aid-view",
        action="store_true",
        help="Allow a new object-only sidecar; base map rebuild remains forbidden",
    )
    parser.add_argument(
        "--normalized-catalog-check",
        action="store_true",
        help="Check current normalized simulator/search object agreement",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--preload-gpus",
        action="store_true",
        help="Upload GPU maps before timing the first site; no preliminary search",
    )
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    if args.search_timeout <= 0:
        parser.error("search timeout must be positive")
    if args.minimum_positive_aids < 16:
        parser.error("Approved policy requires more than15 detected positive aid reports")

    def terminate_supervisor(signum, frame):
        raise SystemExit(f"Supervisor received signal {signum}")

    signal.signal(signal.SIGTERM, terminate_supervisor)
    try:
        run_suite(args)
    except BaseException as error:
        if args.output.exists():
            save(
                args.output / "status.json",
                {"stage": "supervisor_failed", "error": str(error), "updated_utc": now()},
            )
        raise


if __name__ == "__main__":
    main()
