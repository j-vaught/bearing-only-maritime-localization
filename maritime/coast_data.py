"""Load and prepare the frozen coast-wide dataset. J.C. Vaught."""

from __future__ import annotations

import csv
import json
import lzma
import os
import tempfile
from pathlib import Path
from typing import Any

from shapely.geometry import shape

from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import CatalogV2, MapV2
from maritime.search.gpu.resident.aid_view import load_or_canonicalize
from maritime.search.gpu.resident.cache import file_digest, load_or_prepare, preparation_identity
from maritime.search.gpu.resident.preparation import prepare_world
from maritime.serialization import read_contract


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def unpack(archive: Path, destination: Path, archive_hash: str, raw_hash: str) -> None:
    """Verify both compressed and expanded bytes; never replace different input."""
    if file_digest(archive) != archive_hash:
        raise ValueError(f"Archive checksum mismatch: {archive}")
    if destination.exists():
        if file_digest(destination) != raw_hash:
            raise FileExistsError(f"Different input already exists: {destination}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=destination.name + ".", dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as target, lzma.open(archive, "rb") as source:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)
        if file_digest(temporary) != raw_hash:
            raise ValueError(f"Expanded checksum mismatch: {archive}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_inputs(data_dir: Path, storage: Path) -> dict[str, Path]:
    manifest = read_json(data_dir / "sources.json")
    paths = {}
    for kind in ("map", "catalog"):
        part = manifest[kind]
        archive = data_dir / part["archive_filename"]
        if "archive_parts" in part:
            archive = storage / "archives" / part["archive_filename"]
            for segment in part["archive_parts"]:
                if file_digest(data_dir / segment["filename"]) != segment["sha256"]:
                    raise ValueError(f"Archive part checksum mismatch: {segment['filename']}")
            if not archive.exists():
                archive.parent.mkdir(parents=True, exist_ok=True)
                descriptor, name = tempfile.mkstemp(prefix=archive.name + ".", dir=archive.parent)
                temporary = Path(name)
                try:
                    with os.fdopen(descriptor, "wb") as target:
                        for segment in part["archive_parts"]:
                            with (data_dir / segment["filename"]).open("rb") as source:
                                while chunk := source.read(1024 * 1024):
                                    target.write(chunk)
                    if file_digest(temporary) != part["archive_sha256"]:
                        raise ValueError("Assembled archive checksum mismatch")
                    temporary.replace(archive)
                finally:
                    temporary.unlink(missing_ok=True)
        destination = storage / "inputs" / part["raw_filename"]
        unpack(
            archive,
            destination,
            part["archive_sha256"],
            part["raw_sha256"],
        )
        paths[kind] = destination
    return paths


def load_world(data_dir: Path, storage: Path, settings: dict[str, Any], *, build: bool):
    """Map preparation is explicit and separate from GPU search timing."""
    paths = prepare_inputs(data_dir, storage)
    options = settings["preparation"]
    key = preparation_identity(paths["map"], paths["catalog"], options)

    def builder():
        if not build:
            raise RuntimeError("Prepared index is missing. Run 'bearing-coast prepare' once.")
        catalog = read_contract(paths["catalog"], CatalogV2)
        map_input = read_contract(paths["map"], MapV2)
        vector_map = VectorMap.from_map_v2(map_input, target_crs="EPSG:32618")
        preparation = dict(options)
        if "chart_geometry_geojson" in preparation:
            preparation["chart_geometry"] = shape(preparation.pop("chart_geometry_geojson"))
        return prepare_world(vector_map, catalog, **preparation)

    world, hit = load_or_prepare(storage / "prepared-cache", key, builder)
    catalog = read_contract(paths["catalog"], CatalogV2)
    world, object_hit, object_key = load_or_canonicalize(
        storage / "resident-aid-objects", key, world, catalog
    )
    metadata = {
        "base_cache_hit": hit,
        "object_cache_hit": object_hit,
        "base_cache_key": key,
        "object_cache_key": object_key,
        "cache_paths": [
            str(storage / "prepared-cache" / f"{key}.npz"),
            str(storage / "resident-aid-objects" / f"aid-view-{object_key}.npz"),
        ],
    }
    return world, paths, metadata


def sites(data_dir: Path) -> list[dict[str, str]]:
    manifest = read_json(data_dir / "sources.json")["observers"]
    path = data_dir / manifest["filename"]
    if file_digest(path) != manifest["sha256"]:
        raise ValueError("Observer table checksum differs from sources.json")
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 20 or [r["site_id"] for r in rows] != [f"B{i}" for i in range(1, 21)]:
        raise ValueError("Expected the frozen eligible sites B1 through B20")
    if any(int(r["detected_charting_aids"]) < 16 for r in rows):
        raise ValueError("Every retained site must have at least 16 detected charting aids")
    return rows
