"""Object-only normalized-coordinate chart-aid view over a saved resident world.

The base shoreline/coverage arrays and barriers are retained by reference. The
source cache is never rewritten. This view is changed object data with its own
identity and small sidecar, rather than an alias for a different source hash.
"""

from __future__ import annotations

import fcntl
import hashlib
import inspect
import json
import math
import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import numpy as np
from pyproj import CRS, Geod, Transformer

from maritime.catalog_identity import (
    canonical_aid_key,
    canonicalize_chart_aids,
    chart_coordinate_groups,
    is_chart_aid,
    normalize_aid_coordinate,
)
from maritime.schemas import object_types
from maritime.schemas.inputs_v2 import CatalogRecordV2, CatalogV2
from maritime.schemas.object_types import is_visual_entity, normalize_object_type
from maritime.search.deterministic_marker_scoring import _effective_position_uncertainty
from maritime.search.prediction import _visible_feature_offset
from maritime.serialization import contract_to_dict

from .preparation import PreparedWorld, pack_tree

POLICY_VERSION = "CoordinateRadius20cmAidObjectViewV2"


def _digest_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _catalog_digest(catalog: CatalogV2) -> str:
    payload = json.dumps(
        contract_to_dict(catalog), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return _digest_bytes(payload)


def _sidecar_identity(base_key: str, catalog: CatalogV2) -> tuple[str, str]:
    if len(base_key) != 64 or any(c not in "0123456789abcdef" for c in base_key):
        raise ValueError("base_cache_key must be a SHA-256 identity.")
    shared_source = inspect.getsourcefile(canonicalize_chart_aids)
    if shared_source is None:
        raise RuntimeError("Cannot identify the shared aid canonicalization implementation.")
    taxonomy_source = object_types.__file__
    if taxonomy_source is None:
        raise RuntimeError("Cannot identify the object taxonomy implementation.")
    catalog_hash = _catalog_digest(catalog)
    identity = {
        "policy": POLICY_VERSION,
        "base_cache_key": base_key,
        "source_catalog_sha256": catalog_hash,
        "shared_implementation_sha256": _digest_bytes(Path(shared_source).read_bytes()),
        "object_taxonomy_sha256": _digest_bytes(Path(taxonomy_source).read_bytes()),
        "view_implementation_sha256": _digest_bytes(Path(__file__).read_bytes()),
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return _digest_bytes(encoded), catalog_hash


def _source_rows(catalog: CatalogV2) -> list[CatalogRecordV2]:
    return [
        record
        for record in sorted(catalog.records, key=lambda r: r.key)
        if is_visual_entity(record.normalized_type or record.raw_type)
    ]


def _validate_alignment(world: PreparedWorld, catalog: CatalogV2) -> None:
    if not isinstance(world, PreparedWorld) or not isinstance(catalog, CatalogV2):
        raise TypeError("Use a PreparedWorld and its original CatalogV2.")
    if CRS.from_user_input(world.crs) != CRS.from_epsg(32618):
        raise ValueError("The base world must use the supported EPSG:32618 CRS.")
    records = _source_rows(catalog)
    if world.object_keys != tuple(record.key for record in records):
        raise ValueError("Source catalog keys/order do not match the base prepared world.")
    _validate_rows(world.objects, world.object_keys)
    forward = Transformer.from_crs("EPSG:4326", world.crs, always_xy=True)
    geod = Geod(ellps="WGS84")
    for row, record in zip(world.objects, records, strict=True):
        east, north, quality = _visible_feature_offset(record)
        lon, lat = record.longitude_deg, record.latitude_deg
        if quality == "calibrated" and (east != 0 or north != 0):
            lon, lat, _ = geod.fwd(
                lon, lat, math.degrees(math.atan2(east, north)), math.hypot(east, north)
            )
        x, y = forward.transform(lon, lat)
        if tuple(row[:4]) != (x, y, lon, lat):
            raise ValueError(f"Source coordinate/visible-offset mismatch for {record.key!r}.")
        kind_name = normalize_object_type(record.normalized_type or record.raw_type)
        kind = 1 if kind_name == "physical_aid" else 2 if kind_name == "vessel" else 0
        extent = float(dict(record.metadata).get("angular_half_width_deg", 0.0) or 0.0)
        if row[6] != kind or row[5] != extent:
            raise ValueError(f"Source object type/extent mismatch for {record.key!r}.")
        known_bound = _effective_position_uncertainty(record, bound_m=None, exact_asserted=False)
        if known_bound is not None and row[4] < known_bound:
            raise ValueError(
                f"Cached position bound is tighter than source metadata for {record.key!r}."
            )
        if quality == "unsupported" and np.isfinite(row[4]):
            raise ValueError(
                f"Unsupported source offset has a finite cache bound for {record.key!r}."
            )


def _validate_rows(objects: np.ndarray, keys: tuple[str, ...]) -> None:
    if objects.ndim != 2 or objects.shape != (len(keys), 8) or objects.dtype != np.float64:
        raise ValueError("Invalid object-view row layout.")
    if len(set(keys)) != len(keys):
        raise ValueError("Object-view keys must remain unique.")
    if not np.isfinite(objects[:, [0, 1, 2, 3, 5, 6, 7]]).all():
        raise ValueError("Object-view coordinates and attributes must be finite.")
    if np.isnan(objects[:, 4]).any() or np.any(objects[:, 4] < 0):
        raise ValueError("Position uncertainty must be nonnegative or positive infinity.")
    if np.any((objects[:, 5] < 0) | (objects[:, 5] > 180)):
        raise ValueError("Invalid object-view angular extent.")
    if np.any(~np.isin(objects[:, 6], (0, 1, 2))) or np.any(~np.isin(objects[:, 7], (0, 1))):
        raise ValueError("Invalid object-view kind or identity flags.")


def _identity_token(record: CatalogRecordV2) -> str | None:
    value = dict(record.metadata).get("physical_entity_id") or dict(record.provenance).get(
        "physical_entity_id"
    )
    return str(value) if value else None


def _build_view(world: PreparedWorld, original: CatalogV2) -> PreparedWorld:
    canonical = canonicalize_chart_aids(original)
    source_index = {key: i for i, key in enumerate(world.object_keys)}
    groups_by_key: dict[str, tuple[CatalogRecordV2, ...]] = {}
    token_groups: dict[str, set[str]] = {}
    canonical_keys = {
        (r.latitude_deg, r.longitude_deg): r.key for r in canonical.records if is_chart_aid(r)
    }
    for group_indices in chart_coordinate_groups(original.records):
        members = tuple(original.records[i] for i in group_indices)
        if not members:
            raise ValueError("Shared canonicalizer returned an empty chart-aid group.")
        first = members[0]
        key = canonical_keys.get(
            normalize_aid_coordinate(first.latitude_deg, first.longitude_deg),
            first.key
            if len(members) == 1
            else canonical_aid_key(first.latitude_deg, first.longitude_deg),
        )
        groups_by_key[key] = members
        for member in members:
            token = _identity_token(member)
            if token is not None:
                token_groups.setdefault(token, set()).add(key)
    forward = Transformer.from_crs("EPSG:4326", world.crs, always_xy=True)
    rows: list[np.ndarray] = []
    keys: list[str] = []
    for record in sorted(canonical.records, key=lambda r: r.key):
        members = groups_by_key.get(record.key)
        kind_name = normalize_object_type(record.normalized_type)
        if members is not None and kind_name not in ("physical_aid", "unknown"):
            raise ValueError("A canonical aid must retain a physical-aid or unknown type.")
        if not is_visual_entity(record.normalized_type or record.raw_type):
            continue
        if not is_chart_aid(record):
            if record.key not in source_index:
                raise ValueError("Shared canonicalizer changed a non-aid record key.")
            rows.append(world.objects[source_index[record.key]].copy())
            keys.append(record.key)
            continue
        if members is None:
            raise ValueError("Shared canonical chart key does not align with its coordinate group.")
        indices = [source_index[m.key] for m in members]
        source_rows = world.objects[indices]
        row = source_rows[0].copy()
        metadata = dict(record.metadata)
        offset_conflict = bool(
            metadata.get("visible_feature_offset_conflict", False)
            or metadata.get("visible_feature_offset_geometry_unsupported", False)
        )
        cross_location_alias = (
            metadata.get("identity_ambiguous") is True
            or metadata.get("physical_linkage_status") == "unresolved"
            or any(
                token is not None and len(token_groups[token]) > 1
                for token in (_identity_token(m) for m in members)
            )
        )
        row[4] = np.max(source_rows[:, 4])
        canonical_bound = _effective_position_uncertainty(
            record, bound_m=None, exact_asserted=False
        )
        if canonical_bound is not None:
            row[4] = max(row[4], canonical_bound)
        row[5] = np.max(source_rows[:, 5])
        # Auxiliary chart components share identity without creating a marker label.
        row[6] = 1.0 if kind_name == "physical_aid" else 0.0
        row[7] = float(
            np.all(source_rows[:, 7] == 1) and not cross_location_alias and not offset_conflict
        )
        # Use exactly the normalized chart anchor and visible offset used by
        # the canonical simulator catalog, not a raw cached source coordinate.
        lon, lat = record.longitude_deg, record.latitude_deg
        if offset_conflict:
            row[4] = np.inf
        else:
            east, north, quality = _visible_feature_offset(record)
            if quality == "calibrated" and (east != 0 or north != 0):
                lon, lat, _ = Geod(ellps="WGS84").fwd(
                    lon, lat, math.degrees(math.atan2(east, north)), math.hypot(east, north)
                )
        x, y = forward.transform(lon, lat)
        row[:4] = (x, y, lon, lat)
        rows.append(row)
        keys.append(record.key)
    objects = np.asarray(rows, dtype=np.float64).reshape((-1, 8))
    object_keys = tuple(keys)
    _validate_rows(objects, object_keys)
    xy = objects[:, :2]
    tree = pack_tree(objects, np.column_stack((xy, xy)))
    return replace(
        world,
        objects=objects,
        object_keys=object_keys,
        object_tree=tree,
        position_support_complete=bool(np.isfinite(objects[:, 4]).all()),
        identities_complete=bool(np.all(objects[:, 7] == 1)),
    )


def canonicalize_prepared_world(world: PreparedWorld, original_catalog: CatalogV2) -> PreparedWorld:
    """Canonicalize aid objects without rebuilding or copying the map layers."""
    _validate_alignment(world, original_catalog)
    return _build_view(world, original_catalog)


def _payload_digest(arrays: dict[str, np.ndarray], keys: tuple[str, ...]) -> str:
    digest = hashlib.sha256(json.dumps(keys, separators=(",", ":")).encode())
    for name, array in sorted(arrays.items()):
        digest.update(name.encode())
        digest.update(str(array.dtype).encode())
        digest.update(str(array.shape).encode())
        digest.update(np.ascontiguousarray(array).tobytes())
    return digest.hexdigest()


def _validate_tree(bounds: np.ndarray, children: np.ndarray, objects: np.ndarray) -> None:
    count = len(objects)
    node_count = max(0, 2 * count - 1)
    if (
        bounds.shape != (node_count, 4)
        or bounds.dtype != np.float64
        or children.shape != (node_count, 2)
        or children.dtype != np.int32
    ):
        raise ValueError("Invalid object-only tree layout in aid-view sidecar.")
    if not np.isfinite(bounds).all() or np.any(bounds[:, :2] > bounds[:, 2:]):
        raise ValueError("Invalid object-only tree bounds in aid-view sidecar.")
    if not count:
        return
    leaves = children[:, 0] < 0
    item_indices = -children[leaves, 0].astype(np.int64) - 1
    if (
        np.any(children[leaves, 1] != -1)
        or len(item_indices) != count
        or not np.array_equal(np.sort(item_indices), np.arange(count))
    ):
        raise ValueError("Object-only tree leaves do not reference every object exactly once.")
    if not np.array_equal(bounds[leaves], objects[item_indices][:, [0, 1, 0, 1]]):
        raise ValueError("Object-only tree leaf bounds do not match object coordinates.")
    branches = np.flatnonzero(~leaves)
    links = children[branches]
    if np.any(links <= branches[:, None]) or np.any(links >= node_count):
        raise ValueError("Object-only tree children must reference descendants.")
    indegree = np.bincount(links.ravel(), minlength=node_count)
    if indegree[0] != 0 or np.any(indegree[1:] != 1):
        raise ValueError("Object-only tree has duplicate or unreachable nodes.")
    child_bounds = bounds[links]
    envelopes = np.column_stack(
        (child_bounds[:, :, :2].min(axis=1), child_bounds[:, :, 2:].max(axis=1))
    )
    if not np.array_equal(bounds[branches], envelopes):
        raise ValueError("Object-only tree branch bounds do not enclose their descendants.")


def _save(path: Path, key: str, base_key: str, catalog_hash: str, view: PreparedWorld) -> None:
    arrays = {
        "objects": view.objects,
        "object_bounds": view.object_tree.bounds,
        "object_children": view.object_tree.children,
    }
    header = {
        "policy": POLICY_VERSION,
        "key": key,
        "base_cache_key": base_key,
        "source_catalog_sha256": catalog_hash,
        "object_keys": view.object_keys,
        "position_support_complete": view.position_support_complete,
        "identities_complete": view.identities_complete,
        "payload_sha256": _payload_digest(arrays, view.object_keys),
    }
    arrays["header"] = np.frombuffer(json.dumps(header, allow_nan=False).encode(), dtype=np.uint8)
    temporary = path.with_name(path.name + f".{uuid4().hex}.pending")
    try:
        with temporary.open("xb") as stream:
            np.savez(stream, allow_pickle=False, **arrays)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _load(
    path: Path, key: str, base_key: str, catalog_hash: str, world: PreparedWorld
) -> PreparedWorld:
    with np.load(path, allow_pickle=False) as data:
        if set(data.files) != {"objects", "object_bounds", "object_children", "header"}:
            raise ValueError(
                "Unexpected aid-view sidecar arrays; map data must not be stored here."
            )
        header = json.loads(data["header"].tobytes().decode())
        if not isinstance(header, dict):
            raise ValueError("Invalid aid-view sidecar metadata.")
        if (
            header.get("policy") != POLICY_VERSION
            or header.get("key") != key
            or header.get("base_cache_key") != base_key
            or header.get("source_catalog_sha256") != catalog_hash
        ):
            raise ValueError("Aid-view sidecar identity mismatch.")
        objects = np.ascontiguousarray(data["objects"])
        raw_keys = header.get("object_keys")
        if not isinstance(raw_keys, list) or any(not isinstance(k, str) or not k for k in raw_keys):
            raise ValueError("Invalid aid-view sidecar object keys.")
        keys = tuple(raw_keys)
        _validate_rows(objects, keys)
        bounds, children = data["object_bounds"], data["object_children"]
        arrays = {"objects": objects, "object_bounds": bounds, "object_children": children}
        if header.get("payload_sha256") != _payload_digest(arrays, keys):
            raise ValueError("Aid-view sidecar payload checksum mismatch.")
        _validate_tree(bounds, children, objects)
        position_complete = bool(np.isfinite(objects[:, 4]).all())
        identities_complete = bool(np.all(objects[:, 7] == 1))
        if (
            header.get("position_support_complete") is not position_complete
            or header.get("identities_complete") is not identities_complete
        ):
            raise ValueError("Aid-view scalar flags do not match its object rows.")
        tree = type(world.object_tree)(
            np.ascontiguousarray(bounds), np.ascontiguousarray(children), objects
        )
        return replace(
            world,
            objects=objects,
            object_keys=keys,
            object_tree=tree,
            position_support_complete=position_complete,
            identities_complete=identities_complete,
        )


def load_or_canonicalize(
    cache_dir: Path, base_cache_key: str, world: PreparedWorld, original_catalog: CatalogV2
) -> tuple[PreparedWorld, bool, str]:
    """Load/build a lightweight object sidecar; never write the base map cache."""
    _validate_alignment(world, original_catalog)
    key, catalog_hash = _sidecar_identity(base_cache_key, original_catalog)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"aid-view-{key}.npz"
    with path.with_suffix(".lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if path.exists():
            return _load(path, key, base_cache_key, catalog_hash, world), True, key
        view = _build_view(world, original_catalog)
        _save(path, key, base_cache_key, catalog_hash, view)
        return view, False, key
