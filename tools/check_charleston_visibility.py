"""Bounded Charleston visibility and corrected-observation consistency check.

The saved coast cache and existing search are read-only. Source-map cropping,
CPU reference queries and simulator generation are explicit evaluation setup;
GPU ray classification and candidate scoring use physical GPU3 only. No search runs.
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
from pyproj import Geod, Transformer
from shapely import clip_by_rect, from_geojson, make_valid
from shapely.geometry import GeometryCollection, LineString, Point

from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import CatalogV2
from maritime.schemas.object_types import normalize_object_type
from maritime.search.gpu.resident.cache import (
    file_digest,
    load_or_prepare,
    preparation_identity,
)
from maritime.search.gpu.resident.engine import ResidentConfig
from maritime.search.gpu.resident.optimized import OptimizedResidentDevice
from maritime.serialization import read_contract, write_contract
from maritime.simulator.mismatch import (
    KnownWaterObserver,
    PhysicalSceneV2,
    SensorConfigurationV2,
)
from maritime.simulator.runner import _offset_position, run_simulator_v2

ROOT = Path(__file__).resolve().parents[1]
JOB = ROOT / "runs/resident-charleston-refining-20261028-job"
SNAPSHOT = ROOT / "docs/verification/charleston-land-before.json"


def hardware() -> str:
    return subprocess.run(
        ["nvidia-smi", "--query-gpu=index,utilization.gpu,memory.used", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def running(pid: int) -> dict[str, Any]:
    progress = ROOT / "runs/resident-charleston-refining-20261028/progress.jsonl"
    with progress.open("rb") as stream:
        stream.seek(max(0, progress.stat().st_size - 65536))
        last = stream.read().splitlines()[-1]
    return {"pid": pid, "alive": Path(f"/proc/{pid}").exists(), "progress": json.loads(last)}


def annotate_verification(report: dict[str, Any], snapshot: dict[str, Any]) -> None:
    """Separate finished execution, evidence agreement and unresolved certification."""
    rays = report.get("rays", [])
    scores = report.get("candidate_scores", [])
    first = scores[0] if scores else {}
    verified = {
        "saved_preparation_identity_reused": report.get("computed_preparation_identity")
        == snapshot["cache_key"]
        and report.get("cache_hit") is True,
        "all_425_indexed_unindexed_gpu_rays_agree": len(rays) == 425
        and all(row["cpu_indexed"] == row["cpu_unindexed"] == row["gpu"] for row in rays),
        "true_point_all_positive_markers_injective": first.get(
            "all_positive_markers_have_injective_matches"
        )
        is True,
        "saved_cache_preserved": report.get("cache_preserved") is True,
        "baseline_sources_preserved": report.get("baseline_sources_preserved") is True,
        "existing_search_alive_and_progressed": report.get("existing_run_after", {}).get("alive")
        is True
        and report.get("existing_run_progress_advanced") is True,
    }
    report["verification"] = verified
    report["geometry_marker_and_preservation_checks_passed"] = all(verified.values())
    report["visibility_fix_verified"] = all(report["verification"].values())
    report["true_point_numerically_certified"] = (
        first.get("status") == 0 and first.get("flags") == 0
    )
    report["verification_passed"] = (
        report.get("completed") is True
        and all(verified.values())
        and report["true_point_numerically_certified"]
    )
    report["unresolved_verification_reasons"] = (
        []
        if report["true_point_numerically_certified"]
        else [
            "True-point GPU status/flags remain unresolved; injective matching alone "
            "does not certify numerical consistency"
        ]
    )
    report["concrete_previously_missed_land_ray"] = next(
        (row for row in rays if row["object_key"] == "enc-physical:6feaae25013b37441fa2"), None
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs/verification/charleston-visibility-fixed-small.json",
    )
    args = parser.parse_args()
    before = json.loads(SNAPSHOT.read_text())
    cache_file = Path(before["cache_path"])
    settings = json.loads((JOB / "settings.json").read_text())
    simulation_settings_file = (ROOT / settings["measurements"]).with_name(
        "simulation-settings.json"
    )
    simulation_settings = json.loads(simulation_settings_file.read_text())
    source_paths = [
        "maritime/search/gpu/resident/engine.py",
        "maritime/search/gpu/resident/kernels.cu",
        "maritime/search/gpu/resident/preparation.py",
        "maritime/search/gpu/batch_assignment.py",
    ]
    report: dict[str, Any] = {
        "scope": "All in-range Charleston rays and four fixed candidates; no localization search",
        "device": 3,
        "true_position_role": "Evaluation only; never supplied to a search algorithm",
        "cpu_reference_scope": "Input preparation, local simulator and independent reference only",
        "hard_bearing_tolerance_deg": settings["marker_tolerance_deg"],
        "simulation_settings_file": str(simulation_settings_file.relative_to(ROOT)),
        "simulation_settings_sha256": file_digest(simulation_settings_file),
        "seed": simulation_settings["seed"],
        "gpu_flag_policy": "Preserve every unknown, capacity and numerical flag",
        "hardware_before": hardware(),
        "existing_run_parent_snapshot": before["progress"],
        "existing_run_before": running(before["run_pid"]),
        "cache_parent_snapshot": {k: before[k] for k in ("cache_sha256", "cache_stat")},
        "source_hashes_before": {p: file_digest(ROOT / p) for p in source_paths},
        "vector_map_source_sha256": file_digest(ROOT / "maritime/geometry/vector_map.py"),
    }
    start = perf_counter()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save() -> None:
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")

    def stage(name: str) -> None:
        report["stage"] = name
        print(json.dumps({"stage": name, "elapsed_s": perf_counter() - start}), flush=True)
        save()

    def forbidden():
        raise RuntimeError("This check must not rebuild the saved coast cache")

    def timeout_handler(signum: int, frame: Any) -> None:
        raise TimeoutError("Bounded Charleston check exceeded 165 seconds")

    signal.signal(signal.SIGALRM, timeout_handler)
    signal.signal(signal.SIGTERM, timeout_handler)
    signal.alarm(165)
    try:
        stage("verify_saved_cache_identity")
        report["computed_preparation_identity"] = preparation_identity(
            ROOT / settings["map"], ROOT / settings["catalog"], settings["preparation"]
        )
        assert report["computed_preparation_identity"] == before["cache_key"]
        report["cache_sha256_before"] = file_digest(cache_file)
        assert report["cache_sha256_before"] == before["cache_sha256"]
        report["cache_stat_before"] = [cache_file.stat().st_size, cache_file.stat().st_mtime_ns]
        assert report["cache_stat_before"] == before["cache_stat"]
        assert report["source_hashes_before"] == {
            p: before["source_hashes"][p] for p in source_paths
        }
        world, hit = load_or_prepare(cache_file.parent, before["cache_key"], forbidden)
        report["cache_hit"] = hit
        assert hit
        assert world.crs == "EPSG:32618"
        observer = KnownWaterObserver(
            simulation_settings["observer_longitude_deg"],
            simulation_settings["observer_latitude_deg"],
        )
        geod = Geod(ellps="WGS84")
        forward = Transformer.from_crs("EPSG:4326", world.crs, always_xy=True)
        x, y = forward.transform(observer.longitude_deg, observer.latitude_deg)
        _, _, distances = geod.inv(
            np.full(len(world.objects), observer.longitude_deg),
            np.full(len(world.objects), observer.latitude_deg),
            world.objects[:, 2],
            world.objects[:, 3],
        )
        indices = np.flatnonzero((distances > 0) & (distances <= settings["search"]["radius_m"]))
        targets = world.objects[indices]
        report["in_range_cached_objects"] = len(indices)
        assert len(indices) == 425
        points = np.vstack(([[x, y]], targets[:, :2]))
        bounds = (*np.min(points, axis=0) - 1.0, *np.max(points, axis=0) + 1.0)
        report["local_crop_bounds_en_m"] = list(bounds)
        report["local_crop_positive_margin_m"] = 1.0
        stage("clip_original_layers_before_map_overlays")
        payload = json.loads((ROOT / settings["map"]).read_text())
        assert payload["crs"] == world.crs
        clipped = {}
        for name in ("land", "coverage", "unknown"):
            encoded = payload.get(name + "_geometry_geojson")
            geometry = clip_by_rect(from_geojson(encoded), *bounds) if encoded else None
            if geometry is not None and not geometry.is_valid:
                geometry = make_valid(geometry)
            clipped[name] = geometry if geometry is not None and not geometry.is_empty else None
        del payload
        local = VectorMap(
            clipped["land"],
            crs=world.crs,
            coverage_geometry=clipped["coverage"],
            unknown_geometry=clipped["unknown"],
        )
        report["local_observer_status"] = local.point_water_status((x, y))
        report["local_land_polygon_parts"] = len(local._land_parts)
        assert report["local_observer_status"] == "water"
        stage("verify_original_catalog_subset_against_cached_targets")
        catalog = read_contract(ROOT / settings["catalog"], CatalogV2)
        source_by_key = {row.key: row for row in catalog.records}
        subset = tuple(source_by_key[world.object_keys[i]] for i in indices)
        max_error = 0.0
        for record, target in zip(subset, targets, strict=True):
            lon, lat = _offset_position(
                record.longitude_deg, record.latitude_deg, dict(record.metadata)
            )
            east, north = forward.transform(lon, lat)
            error = float(np.hypot(east - target[0], north - target[1]))
            max_error = max(max_error, error)
            assert error <= 1e-6, (record.key, error)
            kind = normalize_object_type(record.normalized_type or record.raw_type)
            assert (1 if kind == "physical_aid" else 2 if kind == "vessel" else 0) == target[6]
            assert float(dict(record.metadata).get("angular_half_width_deg", 0.0)) == target[5]
        report["source_subset_equivalence"] = {
            "contract": "Original nominal catalog records, positions, offsets, types and extents",
            "records": len(subset),
            "maximum_prepared_target_position_difference_m": max_error,
            "types_and_extents_equal": True,
        }
        subset_catalog = replace(
            catalog, records=subset, catalog_id=catalog.catalog_id + "-charleston-local"
        )
        del catalog, source_by_key
        stage("cpu_indexed_and_unindexed_reference_rays")
        rays: list[dict[str, Any]] = []
        for i, target in zip(indices, targets, strict=True):
            endpoint = (float(target[0]), float(target[1]))
            ray = LineString(((x, y), endpoint))
            indexed = local.line_visibility((x, y), endpoint, indexed=True)
            unindexed = local.line_visibility((x, y), endpoint, indexed=False)
            raw_land = clipped["land"] or GeometryCollection()
            coverage = clipped["coverage"]
            unknown = clipped["unknown"]
            rays.append(
                {
                    "object_key": world.object_keys[i],
                    "cached_row": int(i),
                    "kind": int(target[6]),
                    "distance_m": float(distances[i]),
                    "target_en_m": list(endpoint),
                    "target_water_status": local.point_water_status(endpoint),
                    "cpu_indexed": indexed,
                    "cpu_unindexed": unindexed,
                    "source_land_intersection_length_m": float(ray.intersection(raw_land).length),
                    "effective_land_intersection_length_m": float(
                        ray.intersection(local.land_geometry).length
                    ),
                    "target_land_boundary_distance_m": float(
                        Point(endpoint).distance(raw_land.boundary)
                    ),
                    "coverage_contains_entire_ray": bool(
                        coverage is not None and coverage.covers(ray)
                    ),
                    "unknown_intersection_length_m": float(ray.intersection(unknown).length)
                    if unknown is not None
                    else 0.0,
                }
            )
        report["cpu_indexed_unindexed_mismatches"] = [
            row for row in rays if row["cpu_indexed"] != row["cpu_unindexed"]
        ]
        import cupy as cp

        config = replace(ResidentConfig(**settings["search"]), devices=(3,), batch_cells=4)
        stage("gpu3_ray_query_against_immutable_global_cache")
        setup = perf_counter()
        resident = OptimizedResidentDevice(world, config, 3)
        report["gpu_setup_s"] = perf_counter() - setup
        with cp.cuda.Device(3), resident.stream:
            ray_inputs = cp.asarray(
                np.column_stack(
                    (np.full(len(targets), x), np.full(len(targets), y), targets[:, :2])
                )
            )
            codes = cp.empty(len(targets), dtype=cp.int32)
            gpu_start, gpu_end = cp.cuda.Event(), cp.cuda.Event()
            gpu_start.record()
            resident._launch(
                "query_rays",
                len(targets),
                (ray_inputs, np.int32(len(targets)), *resident.map_args, codes),
            )
            gpu_end.record()
            gpu_end.synchronize()
            host_codes = cp.asnumpy(codes)
            report["gpu_ray_stream_span_ms_including_first_compilation"] = float(
                cp.cuda.get_elapsed_time(gpu_start, gpu_end)
            )
        names = {0: "clear", 1: "blocked", 2: "unknown"}
        for row, code in zip(rays, host_codes, strict=True):
            row["gpu"] = names[int(code)]
        report["rays"] = rays
        report["ray_counts"] = {
            key: dict(Counter(row[key] for row in rays))
            for key in ("cpu_indexed", "cpu_unindexed", "gpu")
        }
        report["gpu_cpu_ray_mismatches"] = [row for row in rays if row["gpu"] != row["cpu_indexed"]]
        report["unknown_or_boundary_rays"] = [
            row
            for row in rays
            if row["gpu"] == "unknown"
            or row["cpu_indexed"] == "unknown"
            or row["target_land_boundary_distance_m"] <= 1e-6
        ]
        report["first_source_land_crossing"] = next(
            (row for row in rays if row["source_land_intersection_length_m"] > 1000), None
        )
        stage("actual_seed11_simulator_with_corrected_local_map")
        sensor = SensorConfigurationV2(**simulation_settings["sensor"])
        simulation = run_simulator_v2(
            PhysicalSceneV2(subset_catalog),
            observer,
            local,
            sensor,
            seed=simulation_settings["seed"],
            trial_id="charleston-corrected-local-visibility-seed11",
        )
        measurement_file = args.output.with_name(
            "charleston-corrected-local-measurements-seed11.json"
        )
        write_contract(measurement_file, simulation.measurements)
        report["corrected_measurements_file"] = str(measurement_file.relative_to(ROOT))
        report["sensor"] = asdict(sensor)
        report["simulation_diagnostics"] = asdict(simulation.diagnostics)
        markers = [
            m.bearing_deg
            for m in simulation.measurements.measurements
            if normalize_object_type(m.object_type) == "physical_aid"
        ]
        report["corrected_total_reports"] = len(simulation.measurements.measurements)
        report["corrected_positive_marker_reports"] = len(markers)
        stage("gpu3_four_candidate_marker_consistency")
        offsets = ((0.0, 0.0), (5.0, 0.0), (0.0, 5.0), (20.0, 20.0))
        with cp.cuda.Device(3), resident.stream:
            cells = cp.asarray(
                [[x + dx - 0.05, y + dy - 0.05, x + dx + 0.05, y + dy + 0.05] for dx, dy in offsets]
            )
            observations = cp.asarray(markers, dtype=cp.float64)
            tolerances = cp.full(len(markers), settings["marker_tolerance_deg"], dtype=cp.float64)
            gpu_start.record()
            result = resident.score(cells, observations, tolerances)
            gpu_end.record()
            gpu_end.synchronize()
            report["gpu_score_stream_span_ms_including_first_compilation"] = float(
                cp.cuda.get_elapsed_time(gpu_start, gpu_end)
            )
            host = {key: cp.asnumpy(value) for key, value in result.items()}
        scores = []
        for j, offset in enumerate(offsets):
            matched = host["matched_records"][j]
            records = matched[matched >= 0]
            residual = float(host["residual_deg"][j])
            scores.append(
                {
                    "offset_east_north_m": list(offset),
                    "status": int(host["status"][j]),
                    "flags": int(host["flags"][j]),
                    "matched_positive_markers": len(records),
                    "positive_marker_reports": len(markers),
                    "injective_matched_records": len(np.unique(records)) == len(records),
                    "all_positive_markers_have_injective_matches": len(records) == len(markers)
                    and len(np.unique(records)) == len(records),
                    "residual_deg": residual if np.isfinite(residual) else None,
                    "matched_object_keys": [
                        world.object_keys[int(row)] if row >= 0 else None for row in matched
                    ],
                }
            )
        report["candidate_scores"] = scores
        report["status_codes"] = {
            "0": "consistent",
            "1": "inconsistent_center_only",
            "2": "unresolved",
            "3": "land_center_only",
        }
        report["flag_bits"] = {
            "1": "unknown geometry or support",
            "2": "capacity",
            "4": "numerical failure",
            "8": "boundary, bearing tie or near-zero ambiguity",
        }
        report["localization_accuracy_established"] = False
        report["completed"] = True
        stage("complete")
    except BaseException as error:
        report["completed"] = False
        report["error"] = repr(error)
        save()
        raise
    finally:
        signal.alarm(0)
        report["cache_sha256_after"] = file_digest(cache_file)
        report["cache_stat_after"] = [cache_file.stat().st_size, cache_file.stat().st_mtime_ns]
        report["cache_preserved"] = (
            report["cache_sha256_after"] == before["cache_sha256"]
            and report["cache_stat_after"] == before["cache_stat"]
        )
        report["source_hashes_after"] = {p: file_digest(ROOT / p) for p in source_paths}
        report["baseline_sources_preserved"] = report["source_hashes_after"] == {
            p: before["source_hashes"][p] for p in source_paths
        }
        report["existing_run_after"] = running(before["run_pid"])
        report["existing_run_progress_advanced"] = (
            report["existing_run_after"]["progress"]["evaluated"]
            > report["existing_run_before"]["progress"]["evaluated"]
        )
        report["hardware_after"] = hardware()
        report["total_wall_s"] = perf_counter() - start
        annotate_verification(report, before)
        save()


if __name__ == "__main__":
    main()
