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

# Exact index-only correction in VectorMap. Recursive polygon extraction and
# prepared point predicates changed; land_geometry, coverage, unknown layers,
# object projection and every prepared array are unchanged. Preserve the old
# identity only for this reviewed full-file revision. All other digests still
# invalidate normally.
_FIXED_VECTOR_MAP_SHA256 = "9625f17bd95ea6a40096d90eb32c9f12bbf151e69f114fd5cb352d106c5f6e22"
_LEGACY_VECTOR_MAP_SHA256 = "f597429ddca266502f5233feb9d856fd3c70e217a608c085eb6ddb4e0c26809c"
_VECTOR_MAP_PREPARATION_COMPATIBILITY = {_FIXED_VECTOR_MAP_SHA256: _LEGACY_VECTOR_MAP_SHA256}

# These exact runtime-only revisions add canonicalization to CPU prediction and
# scoring. The preparation primitives imported from these files are unchanged.
# The RAW base cache still contains its original objects. Canonical object views
# use a separate policy/source/catalog key in aid_view.py, never this base key.
_PREDICTION_RUNTIME_SHA256 = "af5df997e3dbc19dc739c7a75f4daab1ef3c61761aa14ff78251d1f1f9eef1ea"
_PREDICTION_BASE_SHA256 = "3024277611a6fea1f0e53f4ec4aadeb9e7493e5832c7bc4fb0386c92353982f8"
_SCORING_RUNTIME_SHA256 = "a21c66a28eb7f3e5d924ddb82c6c923635611e39444581d5f803c949fa217f4a"
_SCORING_BASE_SHA256 = "cabeb3aa65ec38edf8ec5b1fdefe31f9077f83bcddda51b7b0302a609f305511"


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _preparation_source_digest(path: Path) -> str:
    digest = file_digest(path)
    vector_source = inspect.getsourcefile(VectorMap)
    if vector_source is not None and path.resolve() == Path(vector_source).resolve():
        return _VECTOR_MAP_PREPARATION_COMPATIBILITY.get(digest, digest)
    for primitive, current, base in (
        (_visible_feature_offset, _PREDICTION_RUNTIME_SHA256, _PREDICTION_BASE_SHA256),
        (_effective_position_uncertainty, _SCORING_RUNTIME_SHA256, _SCORING_BASE_SHA256),
    ):
        source = inspect.getsourcefile(primitive)
        if source is not None and path.resolve() == Path(source).resolve():
            return base if digest == current else digest
    return digest


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
        "implementation_sha256": sorted(_preparation_source_digest(p) for p in sources),
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
