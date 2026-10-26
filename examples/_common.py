"""Shared orchestration for the small, editable full-dataset examples."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, cast

from _data import ROOT, prepare_data
from pyproj import Geod, Transformer

from bearing_localization.accuracy_cli import evaluate_v2_files
from bearing_localization.evaluation.difficulty import PostmergeDiagnostics, build_support_manifest
from bearing_localization.search.config import SearchConfig
from bearing_localization.search.config_serialization import write_search_config
from bearing_localization.simulator.mismatch import (
    KnownWaterObserver,
    PhysicalSceneV2,
    SensorConfigurationV2,
)
from bearing_localization.simulator.runner import run_simulator_v2
from maritime_localization_shared.contracts.inputs_v2 import CatalogV2, MapV2, MeasurementSetV2
from maritime_localization_shared.contracts.localization import LocalizationResult
from maritime_localization_shared.contracts.truth import EvaluationTruth
from maritime_localization_shared.geospatial.vector_map import VectorMap
from maritime_localization_shared.serialization import read_contract, write_contract

DATA = ROOT / "data"
SCOPE = DATA / "search-domain.json"
PROJECTED_CRS = "EPSG:32618"


@dataclass(frozen=True)
class Frame:
    """File handoff; truth and support are used only after search returns."""

    directory: Path
    measurements: Path
    truth: Path | None
    support: Path | None
    range_m: float
    sigma_deg: float
    seed: int


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def output_directory(name: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    directory = ROOT / "runs" / name / stamp
    directory.mkdir(parents=True, exist_ok=False)
    return directory


@lru_cache(maxsize=1)
def load_world() -> tuple[CatalogV2, VectorMap]:
    """Load the entire shared catalog and map; never crop around observer truth."""
    objects_path, map_path = prepare_data()
    print("Loading the full East Coast object catalog and vector map.", flush=True)
    catalog = read_contract(objects_path, CatalogV2)
    map_input = read_contract(map_path, MapV2)
    vector_map = VectorMap.from_map_v2(map_input, target_crs=PROJECTED_CRS)
    print(f"Loaded {len(catalog.records):,} catalog records.", flush=True)
    return catalog, vector_map


def simulate(
    directory: Path,
    *,
    observer_id: str = "B1",
    observer_table: str = "observer_locations.csv",
    range_m: float = 10_000.0,
    sigma_deg: float = 0.5,
    seed: int = 11,
) -> Frame:
    """Create one realization with range, land blocking, angular merging and noise."""
    catalog, vector_map = load_world()
    with (DATA / observer_table).open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    row = next((item for item in rows if item["site_id"] == observer_id), None)
    if row is None:
        raise ValueError(f"Unknown observer {observer_id!r} in {observer_table}.")
    observer = KnownWaterObserver(float(row["longitude_deg"]), float(row["latitude_deg"]))
    project = Transformer.from_crs("EPSG:4326", PROJECTED_CRS, always_xy=True)
    point = project.transform(observer.longitude_deg, observer.latitude_deg)
    status = vector_map.point_water_status(point)
    if status != "water":
        raise ValueError(
            f"{observer_id} is {status!r} on the current map. "
            "Choose a known-water point in data/observer_locations.csv."
        )
    sensor = SensorConfigurationV2(visibility_radius_m=range_m, bearing_sigma_deg=sigma_deg)
    print(f"Simulating {observer_id} at {range_m / 1000:g} km visibility.", flush=True)
    # The simulator rejects land/unknown observer positions. It never relocates them silently.
    simulation = run_simulator_v2(
        PhysicalSceneV2(catalog),
        observer,
        vector_map,
        sensor,
        seed=seed,
        trial_id=directory.name,
        timestamp_utc=catalog.timestamp_utc,
    )
    directory.mkdir(parents=True, exist_ok=True)
    measurements = directory / "measurements.json"
    truth = directory / "truth.json"
    support = directory / "support.json"
    write_contract(measurements, simulation.measurements)
    write_contract(truth, simulation.truth)
    save_json(directory / "simulation.json", asdict(simulation.diagnostics))
    save_json(
        directory / "simulation-settings.json",
        {
            "observer_table": observer_table,
            "observer_id": observer_id,
            "observer": asdict(observer),
            "sensor": asdict(sensor),
            "seed": seed,
            "projected_crs": PROJECTED_CRS,
        },
    )
    manifest = build_support_manifest(
        True,
        cast(PostmergeDiagnostics, simulation.diagnostics),
        port_id=observer_id,
        placement_id=directory.name,
    )
    save_json(support, asdict(manifest))
    print(f"Generated {len(simulation.measurements.measurements)} detections.", flush=True)
    print(f"Measurements and separate truth saved in {directory}", flush=True)
    return Frame(directory, measurements, truth, support, range_m, sigma_deg, seed)


def replay(directory: Path, *, range_m: float = 10_000.0) -> Frame:
    """Use the saved synthetic bearings; their old truth is not in the checkout."""
    prepare_data()
    measurements = directory / "measurements.json"
    write_contract(measurements, read_contract(DATA / "example-bearings.json", MeasurementSetV2))
    return Frame(directory, measurements, None, None, range_m, 0.5, 11)


def search(
    frame: Frame,
    *,
    name: str,
    algorithm: str,
    backend: Literal["cpu", "gpu"] = "cpu",
    use_types: bool = True,
    candidate_budget: int = 256,
    spacing_m: float = 25_000.0,
    proposal_cell_size_m: float = 250.0,
    proposal_source_budget: int = 333_340,
    refine: bool = True,
    device_ids: tuple[int, ...] = (0,),
) -> dict[str, Any]:
    """Start a separate estimator process with no truth or observer-location argument."""
    directory = frame.directory / name
    directory.mkdir(exist_ok=False)
    measurements = read_contract(frame.measurements, MeasurementSetV2)
    if not use_types:
        measurements = replace(
            measurements,
            measurements=tuple(replace(m, object_type=None) for m in measurements.measurements),
        )
    measurements_path = directory / "measurements.json"
    write_contract(measurements_path, measurements)
    scope = json.loads(SCOPE.read_text())
    bounds = cast(tuple[float, float, float, float], tuple(scope["bounds_xy_m"]))
    config = SearchConfig(
        algorithm=algorithm,
        backend=backend,
        bounds_xy=bounds,
        max_range_m=frame.range_m,
        use_types=use_types,
        generated_sigma_deg=frame.sigma_deg,
        candidate_budget=candidate_budget,
        proposal_source_pair_budget=proposal_source_budget,
        proposal_cell_budget=5_000_000,
    )
    config_path = directory / "search-config.json"
    write_search_config(config_path, config)
    result_path = directory / "result.json"
    command = [
        sys.executable,
        "-m",
        "bearing_localization.search_cli",
        "--schema-version",
        "2",
        "--measurements",
        str(measurements_path),
        "--catalog",
        str(DATA / "objects.json"),
        "--map",
        str(DATA / "map.json"),
        "--config",
        str(config_path),
        "--scope-manifest",
        str(SCOPE),
        "--projected-crs",
        PROJECTED_CRS,
        "--algorithm",
        algorithm,
        "--spacing-m",
        str(spacing_m),
        "--wedge-cell-size-m",
        str(proposal_cell_size_m),
        "--seed",
        str(frame.seed),
        "--output",
        str(result_path),
    ]
    if not refine:
        command.append("--no-refine")
    if backend == "gpu":
        for device in device_ids:
            command.extend(("--device-id", str(device)))
    print(
        f"Starting {name} over the full declared coast domain. Log: {directory / 'search.log'}",
        flush=True,
    )
    started = perf_counter()
    with (directory / "search.log").open("w") as log:
        process = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    process_ms = (perf_counter() - started) * 1000
    summary: dict[str, Any] = {
        "algorithm": algorithm,
        "backend": backend,
        "types_enabled": use_types,
        "range_m": frame.range_m,
        "detected_reports": len(measurements.measurements),
        "candidate_budget": candidate_budget,
        "search_process_ms": process_ms,
        "search_status": "failed",
        "decision_status": None,
        "best_hypothesis_error_m": None,
        "accepted_position_error_m": None,
        "result_file": str(result_path),
        "process_returncode": process.returncode,
    }
    if process.returncode != 0 or not result_path.exists():
        print((directory / "search.log").read_text()[-4000:], flush=True)
    else:
        result = read_contract(result_path, LocalizationResult)
        summary.update(
            search_status=result.search_status,
            decision_status=result.decision_status,
            certificate_scope=result.certificate_scope,
            solver_reported_ms=result.elapsed_s * 1000,
            hypotheses=len(result.hypotheses),
            budget_used=result.budget_used,
            budget_exhaustion_reason=result.budget_exhaustion_reason,
        )
        transformer = Transformer.from_crs(PROJECTED_CRS, "EPSG:4326", always_xy=True)
        if result.hypotheses:
            best = result.hypotheses[0]
            longitude, latitude = transformer.transform(best.east_m, best.north_m)
            summary.update(
                best_longitude_deg=longitude,
                best_latitude_deg=latitude,
                best_score=best.score,
                matched_reports=len(best.assignments),
            )
            if frame.truth is not None:
                truth = read_contract(frame.truth, EvaluationTruth)
                true_lon, true_lat = transformer.transform(
                    truth.observer_east_m,
                    truth.observer_north_m,
                )
                summary.update(
                    true_longitude_deg=true_lon,
                    true_latitude_deg=true_lat,
                    best_hypothesis_error_m=abs(
                        Geod(ellps="WGS84").inv(true_lon, true_lat, longitude, latitude)[2]
                    ),
                )
        if frame.truth is not None and frame.support is not None:
            evaluation = evaluate_v2_files(
                result_path,
                frame.truth,
                frame.support,
                directory / "evaluation.json",
                coordinate_crs=PROJECTED_CRS,
            )
            summary["accepted_position_error_m"] = evaluation["position_error_m"]
    save_json(directory / "summary.json", summary)
    print(json.dumps(summary, indent=2), flush=True)
    return summary


def comparison(directory: Path, rows: list[dict[str, Any]]) -> None:
    """Retain every outcome, including failed searches, in one inspectable table."""
    save_json(directory / "comparison.json", rows)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with (directory / "comparison.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Comparison saved in {directory}", flush=True)
    if any(row["search_status"] == "failed" for row in rows):
        raise SystemExit(1)
