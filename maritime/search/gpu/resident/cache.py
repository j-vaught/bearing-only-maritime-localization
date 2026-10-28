"""Persistent preparation cache. GPU buffers are uploaded once per process.

This module is used before the search loop. A cache hit never constructs a
VectorMap or calls catalog projection/tree preparation. Measurement files and
search resolution deliberately do not participate in the cache identity.
"""

from __future__ import annotations

import fcntl
import hashlib
import inspect
import json
import os
from collections.abc import Callable
from dataclasses import fields
from importlib.metadata import version
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np

from maritime.geometry.vector_map import VectorMap
from maritime.search.deterministic_marker_scoring import _effective_position_uncertainty
from maritime.search.prediction import _visible_feature_offset

from .preparation import PackedTree, PreparedWorld, prepare_world

FORMAT = "ResidentPreparedWorldV1"


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def preparation_identity(map_path: Path, catalog_path: Path, options: dict[str, Any]) -> str:
    sources = set()
    for obj in (prepare_world, VectorMap, _effective_position_uncertainty, _visible_feature_offset):
        source = inspect.getsourcefile(obj)
        if source is None:
            raise RuntimeError("Cannot fingerprint preparation implementation.")
        sources.add(Path(source))
    identity = {
        "format": FORMAT,
        "map_sha256": file_digest(map_path),
        "catalog_sha256": file_digest(catalog_path),
        "preparation_options": options,
        "implementation_sha256": sorted(file_digest(p) for p in sources),
        "versions": {name: version(name) for name in ("numpy", "shapely", "pyproj")},
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _save(path: Path, key: str, world: PreparedWorld) -> None:
    arrays: dict[str, Any] = {
        "objects": world.objects,
        "barriers": world.barriers,
        "object_bounds": world.object_tree.bounds,
        "object_children": world.object_tree.children,
    }
    for index, layer in enumerate(world.layers):
        arrays[f"layer_{index}_bounds"] = layer.bounds
        arrays[f"layer_{index}_children"] = layer.children
        arrays[f"layer_{index}_items"] = layer.items
    scalar_fields = {
        f.name: getattr(world, f.name)
        for f in fields(world)
        if f.name not in {"layers", "objects", "object_tree", "barriers", "object_keys"}
    }
    header = {
        "format": FORMAT,
        "key": key,
        "object_keys": world.object_keys,
        "scalars": scalar_fields,
        "layer_count": len(world.layers),
    }
    encoded = json.dumps(header, allow_nan=False).encode()
    arrays["header"] = np.frombuffer(encoded, dtype=np.uint8)
    temporary = path.with_name(path.name + f".{uuid4().hex}.pending")
    try:
        with temporary.open("xb") as stream:
            np.savez(stream, allow_pickle=False, **arrays)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _array(data: Any, name: str, width: int, dtype: Any) -> np.ndarray:
    result = data[name]
    if result.ndim != 2 or result.shape[1] != width or result.dtype != np.dtype(dtype):
        raise ValueError(f"Invalid cached array {name}.")
    return np.ascontiguousarray(result)


def _load(path: Path, key: str) -> PreparedWorld:
    with np.load(path, allow_pickle=False) as data:
        header = json.loads(data["header"].tobytes().decode())
        if (
            header.get("format") != FORMAT
            or header.get("key") != key
            or header.get("layer_count") != 4
        ):
            raise ValueError("Preparation cache identity or format mismatch.")
        layers = tuple(
            PackedTree(
                _array(data, f"layer_{i}_bounds", 4, np.float64),
                _array(data, f"layer_{i}_children", 2, np.int32),
                _array(data, f"layer_{i}_items", 4, np.float64),
            )
            for i in range(4)
        )
        objects = _array(data, "objects", 8, np.float64)
        object_tree = PackedTree(
            _array(data, "object_bounds", 4, np.float64),
            _array(data, "object_children", 2, np.int32),
            objects,
        )
        if len(header["object_keys"]) != len(objects):
            raise ValueError("Cached catalog row count mismatch.")
        for tree in (*layers, object_tree):
            if len(tree.bounds) != len(tree.children):
                raise ValueError("Cached tree node count mismatch.")
        return PreparedWorld(
            layers=layers,
            objects=objects,
            object_keys=tuple(header["object_keys"]),
            object_tree=object_tree,
            barriers=_array(data, "barriers", 4, np.float64),
            **header["scalars"],
        )


def load_or_prepare(
    cache_dir: Path, key: str, builder: Callable[[], PreparedWorld]
) -> tuple[PreparedWorld, bool]:
    """A matching cache skips the builder; a mismatched key builds a new entry.

    The lock prevents duplicate preparation by concurrent processes. Corrupt
    entries fail visibly instead of silently starting another long preparation.
    """
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("Invalid preparation cache key.")
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{key}.npz"
    with (cache_dir / f"{key}.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if path.exists():
            return _load(path, key), True
        world = builder()
        _save(path, key, world)
        return world, False
