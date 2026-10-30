"""Prepare, simulate, and search the eastern seaboard. J.C. Vaught."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from maritime.coast_data import load_world, prepare_inputs, read_json, save_json, sites
from maritime.coast_simulation import simulate
from maritime.evaluation.resident import evaluate_position
from maritime.schemas.inputs_v2 import CatalogV2, MapV2, MeasurementSetV2
from maritime.schemas.object_types import normalize_object_type
from maritime.search.gpu.resident.cache import file_digest
from maritime.search.gpu.resident.engine import ResidentConfig
from maritime.search.gpu.resident.selection import select_position
from maritime.search.gpu.resident.service import PersistentSearchService
from maritime.serialization import read_contract


def device_ids(value: str) -> tuple[int, ...]:
    try:
        devices = tuple(int(part) for part in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use comma-separated GPU indices") from error
    if not devices or min(devices) < 0 or len(set(devices)) != len(devices):
        raise argparse.ArgumentTypeError("GPU indices must be distinct and nonnegative")
    return devices


def ensure_idle(devices: tuple[int, ...]) -> None:
    """Never start a new owner on GPUs with another compute process."""
    index = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader,nounits"], text=True
    )
    mapping = {int(a.strip()): b.strip() for a, b in csv.reader(index.splitlines())}
    if any(device not in mapping for device in devices):
        raise ValueError("A requested GPU does not exist")
    processes = subprocess.check_output(
        ["nvidia-smi", "--query-compute-apps=gpu_uuid,pid", "--format=csv,noheader,nounits"],
        text=True,
    )
    busy = {row[0].strip() for row in csv.reader(processes.splitlines()) if row}
    occupied = [d for d in devices if mapping[d] in busy]
    if occupied:
        raise RuntimeError(f"Requested GPUs already have compute processes: {occupied}")


def marker_bearings(path: Path) -> list[float]:
    measurements = read_contract(path, MeasurementSetV2)
    if any(
        value is not None
        for value in (
            measurements.calibrated_common_bias_mean_deg,
            measurements.calibrated_common_bias_sigma_deg,
            measurements.covariance_deg2,
        )
    ):
        raise ValueError("Correlated or biased measurements are unsupported")
    bearings = [
        m.bearing_deg
        for m in measurements.measurements
        if normalize_object_type(m.object_type) == "physical_aid"
    ]
    if not bearings:
        raise ValueError("At least one positive charting-aid bearing is required")
    return bearings


def simulation_inputs(paths: dict[str, Path]):
    from shapely import from_geojson

    from maritime.simulator.mismatch import PhysicalSceneV2

    map_input = read_contract(paths["map"], MapV2)
    if map_input.crs != "EPSG:32618":
        raise ValueError("The bundled simulator map must use EPSG:32618")
    layers: dict[str, Any] = {"crs": map_input.crs}
    for name in ("land", "coverage", "unknown"):
        encoded = getattr(map_input, name + "_geometry_geojson")
        layers[name] = from_geojson(encoded) if encoded else None
    return layers, PhysicalSceneV2(read_contract(paths["catalog"], CatalogV2))


def finish_search(directory: Path, device: int, truth: Path | None = None):
    result = select_position(directory / "search", directory / "estimate.json", device)
    save_json(
        directory / "estimate-freeze.json",
        {
            "frozen_utc": datetime.now(UTC).isoformat(),
            "estimate_sha256": file_digest(directory / "estimate.json"),
        },
    )
    accuracy = None
    if truth is not None:
        accuracy = evaluate_position(
            directory / "estimate.json",
            directory / "estimate-freeze.json",
            truth,
            directory / "accuracy.json",
        )
    return result, accuracy


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "sites", "simulate", "run", "search"):
        command = commands.add_parser(name)
        command.add_argument("--data-dir", type=Path, default=Path("data"))
        command.add_argument("--config", type=Path, default=Path("config/search.json"))
        command.add_argument(
            "--storage",
            type=Path,
            default=Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
            / "bearing-localization",
        )
        if name in ("run", "simulate"):
            group = command.add_mutually_exclusive_group(required=True)
            group.add_argument("--site")
            group.add_argument("--all", action="store_true")
            command.add_argument("--sensor-config", type=Path, default=Path("config/sensor.json"))
        if name in ("run", "search"):
            command.add_argument("--devices", type=device_ids)
            command.add_argument("--batch-cells", type=int)
            command.add_argument("--timeout", type=float, default=1800)
            command.add_argument("--fixed", action="store_true")
        if name in ("simulate", "run", "search"):
            command.add_argument("--output", type=Path)
        if name == "run":
            command.add_argument("--repeats", type=int, default=1)
        if name == "search":
            command.add_argument("--measurements", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "sites":
        print(json.dumps(sites(args.data_dir), indent=2))
        return
    settings = read_json(args.config)
    if set(settings) != {"map", "catalog", "search", "preparation", "marker_tolerance_deg"}:
        raise ValueError("Search configuration contains missing or unsupported fields")
    if args.command == "prepare":
        _, _, metadata = load_world(args.data_dir, args.storage, settings, build=True)
        print(json.dumps(metadata, indent=2))
        return
    rows = []
    if args.command in ("run", "simulate"):
        rows = sites(args.data_dir)
        if args.site:
            rows = [row for row in rows if row["site_id"] == args.site]
            if not rows:
                parser.error("Select an existing site from B1 through B20")
    if args.command == "run" and args.repeats < 1:
        parser.error("--repeats must be positive")
    search = dict(settings["search"])
    if args.command in ("run", "search"):
        if args.devices is not None:
            search["devices"] = args.devices
        if args.batch_cells is not None:
            search["batch_cells"] = args.batch_cells
        search["share_terminal_work"] = not args.fixed
        config = ResidentConfig(**search)
        ensure_idle(config.devices)
    output = args.output or args.storage / "runs" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    if args.command == "simulate":
        paths = prepare_inputs(args.data_dir, args.storage)
        metadata = {}
    else:
        world, paths, metadata = load_world(args.data_dir, args.storage, settings, build=False)
    save_json(output / "preparation.json", metadata)
    if args.command in ("simulate", "run"):
        layers, scene = simulation_inputs(paths)
        sensor = read_json(args.sensor_config)
    if args.command == "simulate":
        for row in rows:
            directory = output / row["site_id"]
            directory.mkdir()
            markers, _ = simulate(row, layers, scene, sensor, directory)
            print(json.dumps({"site": row["site_id"], "aids": len(markers)}), flush=True)
        return
    service = PersistentSearchService(world, asdict(config), output)
    records = []
    try:
        preload = output / "gpu-preload"
        preload.mkdir()
        service.prepare(preload)
        if args.command == "search":
            directory = output / "observation"
            directory.mkdir()
            response = service.search(
                marker_bearings(args.measurements),
                settings["marker_tolerance_deg"],
                directory,
                timeout_s=args.timeout,
                sharing=config.share_terminal_work,
            )
            estimate, _ = finish_search(directory, config.devices[0])
            save_json(
                output / "result.json", {"summary": response["summary"], "estimate": estimate}
            )
            print(json.dumps(estimate["estimate"], indent=2))
            return
        for row in rows:
            for repeat in range(1, args.repeats + 1):
                directory = output / f"{row['site_id']}-{repeat}"
                directory.mkdir()
                markers, _ = simulate(row, layers, scene, sensor, directory)
                if len(markers) < 16:
                    raise ValueError(f"{row['site_id']} has fewer than 16 detected charting aids")
                response = service.search(
                    markers,
                    settings["marker_tolerance_deg"],
                    directory,
                    timeout_s=args.timeout,
                    sharing=config.share_terminal_work,
                )
                estimate, accuracy = finish_search(
                    directory, config.devices[0], directory / "truth.json"
                )
                record = {
                    "site": row["site_id"],
                    "port": row["port"],
                    "repeat": repeat,
                    "aids": len(markers),
                    "search_s": response["summary"]["elapsed_s"],
                    "position_error_m": accuracy["position_error_m"],
                    "gpu_buffers_reused": response["metadata"]["gpu_buffers_reused"],
                    "estimate": estimate["estimate"],
                }
                records.append(record)
                save_json(output / "results.json", records)
                print(json.dumps({k: v for k, v in record.items() if k != "estimate"}), flush=True)
        columns = ("site", "port", "repeat", "aids", "search_s", "position_error_m")
        with (output / "results.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(records)
    finally:
        service.close()


if __name__ == "__main__":
    main()
