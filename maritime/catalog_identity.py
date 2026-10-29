"""Exact-coordinate chart-aid identity shared by simulation and search.

The rule merges eligible aid records only when their parsed latitude and
longitude values are equal. It never rounds coordinates or merges by distance.
Non-chart records are returned unchanged. Fog signals, radio stations, and
retro reflectors can join coordinate groups while retaining unknown coarse type
unless a co-located known physical aid establishes it. Component snapshots
preserve the full input record contract, including metadata and provenance.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict, defaultdict
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import asdict, fields, replace
from math import isfinite
from threading import Lock
from typing import Any

from maritime.schemas.inputs_v2 import CatalogRecordV2, CatalogV2
from maritime.schemas.object_types import normalize_object_type

RULE = "user_exact_coordinate_rule_v1"
COMPONENT_SCHEMA = "CatalogRecordV2CompleteV1"
_OFFSET_KEYS = {"visible_feature_offset_east_m", "visible_feature_offset_north_m"}
_BOUND_KEYS = ("position_uncertainty_m", "horizontal_uncertainty_m", "uncertainty_m")
_LAYER_KEYS = {
    "layer",
    "layer_name",
    "source_layer",
    "source_layers",
    "service_layer",
    "enc_layer",
    "component_layer",
    "component_layers",
    "source_layer_name",
}
_RECORD_FIELDS = {f.name for f in fields(CatalogRecordV2)}
# Identity keys avoid hashing or comparing a complete coast-wide catalog.
# Strong references make id reuse impossible while an entry is retained.
_CATALOG_CACHE_LIMIT = 4
_catalog_cache: OrderedDict[int, tuple[CatalogV2, CatalogV2]] = OrderedDict()
_catalog_cache_lock = Lock()


def _coordinate(latitude_deg: float, longitude_deg: float) -> tuple[float, float]:
    if isinstance(latitude_deg, bool) or isinstance(longitude_deg, bool):
        raise ValueError("Aid coordinates must be numeric geographic values.")
    lat, lon = float(latitude_deg), float(longitude_deg)
    if not isfinite(lat) or not isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("Aid coordinates must be finite latitude/longitude.")
    return (0.0 if lat == 0 else lat, 0.0 if lon == 0 else lon)


def canonical_aid_key(latitude_deg: float, longitude_deg: float) -> str:
    """A reversible coordinate key with no decimal rounding or hash collision."""
    lat, lon = _coordinate(latitude_deg, longitude_deg)
    return f"chart-coordinate:{float(lat).hex()}:{float(lon).hex()}"


def _component_layer(value: Any, *, physical_only: bool = False) -> bool:
    if not isinstance(value, str):
        return False
    name = re.sub(r"[_\s-]+", " ", value.rsplit(".", 1)[-1].strip().casefold())
    words = name.split()
    if words in (["light", "point"], ["daymark", "point"], ["topmark", "point"]):
        return True
    if len(words) >= 2 and words[0] in {"beacon", "buoy"} and words[-1] == "point":
        return True
    return not physical_only and words in (
        ["fog", "signal", "point"],
        ["radio", "station", "point"],
        ["retro", "reflector", "point"],
    )


def _layer_values(mapping: dict[str, Any]):
    for key, value in mapping.items():
        if key.casefold() in _LAYER_KEYS:
            if isinstance(value, str):
                yield value
            elif isinstance(value, (list, tuple)):
                yield from (v for v in value if isinstance(v, str))
    components = mapping.get("source_components")
    if isinstance(components, (list, tuple)):
        for component in components:
            if isinstance(component, dict):
                for key in _LAYER_KEYS:
                    if isinstance(component.get(key), str):
                        yield component[key]


def _canonical_chart_type(record: CatalogRecordV2) -> str | None:
    """Return eligible component coarse type, without promoting auxiliary types."""
    if not isinstance(record, CatalogRecordV2):
        raise TypeError("record must be CatalogRecordV2.")
    labels = (record.normalized_type, record.raw_type)
    neutral = tuple(normalize_object_type(label) for label in labels)
    metadata, provenance = dict(record.metadata), dict(record.provenance)
    if any(kind in {"vessel", "virtual_aid"} for kind in neutral):
        return None
    if any(
        mapping.get(key) is True
        for mapping in (metadata, provenance)
        for key in ("virtual", "is_virtual", "virtual_aid")
    ):
        return None
    if "physical_aid" in neutral:
        return "physical_aid"
    explicit_daymark = any(
        isinstance(label, str)
        and re.sub(r"[_\s-]+", "", label.strip().casefold()) in {"daymark", "topmark"}
        for label in labels
    )
    layers = [
        *labels,
        *(value for mapping in (metadata, provenance) for value in _layer_values(mapping)),
        *(record.key.split(":") if record.key.startswith("enc-component:") else ()),
    ]
    if explicit_daymark or any(_component_layer(value, physical_only=True) for value in layers):
        return "physical_aid"
    if any(_component_layer(value) for value in layers):
        return "unknown"
    # Coordinate keys can replace layer-bearing source keys after a merge. Keep
    # our auxiliary-only canonical records eligible without inventing a type.
    if (
        metadata.get("identity_resolution") == RULE
        and metadata.get("source_components_schema") == COMPONENT_SCHEMA
        and metadata.get("chart_coordinate_component_type") == "unknown"
    ):
        return "unknown"
    return None


def is_chart_aid(record: CatalogRecordV2) -> bool:
    """Recognize eligible chart components, excluding vessels and virtual aids.

    Eligibility includes explicit NOAA fog-signal, radio-station, and
    retro-reflector point layers. It does not certify the physical_aid coarse
    type. Use canonical output normalized_type when selecting marker rows.
    """
    return _canonical_chart_type(record) is not None


def chart_coordinate_groups(records: Iterable[CatalogRecordV2]) -> tuple[tuple[int, ...], ...]:
    """Return eligible aid row-index groups in exact-coordinate/key order."""
    rows = tuple(records)
    groups: dict[tuple[float, float], list[int]] = defaultdict(list)
    for index, record in enumerate(rows):
        if is_chart_aid(record):
            groups[_coordinate(record.latitude_deg, record.longitude_deg)].append(index)
    return tuple(
        tuple(sorted(groups[coord], key=lambda i: rows[i].key)) for coord in sorted(groups)
    )


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _snapshot(record: CatalogRecordV2) -> dict[str, Any]:
    # JSON is the input/output contract; normalize tuple containers to arrays.
    return json.loads(_json(asdict(record)))


def _restore(source: dict[str, Any]) -> CatalogRecordV2:
    if set(source) != _RECORD_FIELDS:
        raise ValueError("Canonical source component is not a complete CatalogRecordV2.")
    values = deepcopy(source)
    for key in ("metadata", "provenance"):
        values[key] = tuple((str(k), v) for k, v in values[key])
    return CatalogRecordV2(**values)


def _components(record: CatalogRecordV2) -> list[dict[str, Any]]:
    metadata = dict(record.metadata)
    if (
        metadata.get("identity_resolution") == RULE
        and metadata.get("source_components_schema") == COMPONENT_SCHEMA
    ):
        components = metadata.get("source_components")
        if not isinstance(components, list) or not components:
            raise ValueError("Canonical chart aid has no complete source components.")
        for source in components:
            if not isinstance(source, dict):
                raise ValueError("Invalid canonical source component.")
            _restore(source)
        return deepcopy(components)
    return [_snapshot(record)]


def _physical_id(record: CatalogRecordV2) -> str | None:
    value = dict(record.metadata).get("physical_entity_id")
    if value is None:
        value = dict(record.provenance).get("physical_entity_id")
    return value if isinstance(value, str) and value.strip() else None


def _position_bound(record: CatalogRecordV2) -> float | None:
    values = [
        v
        for key in _BOUND_KEYS
        if isinstance((v := dict(record.metadata).get(key)), (int, float))
        and not isinstance(v, bool)
        and isfinite(v)
        and v >= 0
    ]
    return max(map(float, values)) if values else None


def _offset_signature(record: CatalogRecordV2) -> tuple[Any, ...]:
    metadata = dict(record.metadata)
    for key in metadata:
        lower = key.casefold()
        if lower == "angular_half_width_deg":
            continue
        if ("visible_feature_offset_" in lower and key not in _OFFSET_KEYS) or any(
            token in lower
            for token in ("antenna", "dimension", "length", "beam", "width", "height")
        ):
            return ("unsupported",)
    present = _OFFSET_KEYS.intersection(metadata)
    if not present:
        return ("nominal",)
    if present != _OFFSET_KEYS:
        return ("unsupported",)
    values = tuple(metadata[key] for key in sorted(_OFFSET_KEYS))
    if any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v) for v in values
    ):
        return ("unsupported",)
    return ("calibrated", *map(float, values))


def canonicalize_chart_records(records: Iterable[CatalogRecordV2]) -> tuple[CatalogRecordV2, ...]:
    """Coalesce exact-location aids; preserve ambiguity and all source records."""
    rows = tuple(records)
    if any(not isinstance(row, CatalogRecordV2) for row in rows):
        raise TypeError("records must contain CatalogRecordV2 values.")
    if len({row.key for row in rows}) != len(rows):
        raise ValueError("Input record keys must be unique.")
    groups = chart_coordinate_groups(rows)
    sources: list[list[dict[str, Any]]] = []
    original_rows: list[list[CatalogRecordV2]] = []
    identities: dict[str, set[int]] = defaultdict(set)
    for index, group in enumerate(groups):
        unique = {_json(component): component for i in group for component in _components(rows[i])}
        components = sorted(unique.values(), key=lambda c: (c["key"], _json(c)))
        originals = [_restore(c) for c in components]
        sources.append(components)
        original_rows.append(originals)
        for original in originals:
            if (identity := _physical_id(original)) is not None:
                identities[identity].add(index)
    parents = list(range(len(groups)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for indices in identities.values():
        connected = sorted(indices)
        for index in connected[1:]:
            parents[find(index)] = find(connected[0])
    connected_groups: dict[int, list[int]] = defaultdict(list)
    for index in range(len(groups)):
        connected_groups[find(index)].append(index)
    external_coordinates: dict[str, set[tuple[float, float]]] = defaultdict(set)
    for row in rows:
        if not is_chart_aid(row) and (identity := _physical_id(row)) is not None:
            external_coordinates[identity].add(_coordinate(row.latitude_deg, row.longitude_deg))
    # Visit identities once rather than scanning all IDs for every component.
    conflicts_by_root: dict[int, set[str]] = defaultdict(set)
    for identity, owners in identities.items():
        if len(owners) > 1:
            conflicts_by_root[find(next(iter(owners)))].add(identity)
    for index, originals in enumerate(original_rows):
        coord = _coordinate(
            rows[groups[index][0]].latitude_deg, rows[groups[index][0]].longitude_deg
        )
        for original in originals:
            identity = _physical_id(original)
            if identity is None:
                continue
            locations = external_coordinates.get(identity)
            if locations and (len(locations) > 1 or coord not in locations):
                conflicts_by_root[find(index)].add(identity)
    ambiguous: dict[int, tuple[str, list[str]]] = {}
    for root, indices in connected_groups.items():
        conflicts = conflicts_by_root.get(root, set())
        if len(indices) < 2 and not conflicts:
            continue
        ids = sorted(conflicts)
        token = (
            ids[0]
            if len(ids) == 1
            else "ambiguous-chart-identities:" + hashlib.sha256(_json(ids).encode()).hexdigest()
        )
        for index in indices:
            ambiguous[index] = (token, ids)

    result = [row for row in rows if not is_chart_aid(row)]
    for index, group in enumerate(groups):
        originals = original_rows[index]
        representative = originals[0]
        component_type = (
            "physical_aid"
            if any(_canonical_chart_type(row) == "physical_aid" for row in originals)
            else "unknown"
        )
        coord = _coordinate(rows[group[0]].latitude_deg, rows[group[0]].longitude_deg)
        # A prior canonical merge still has one wrapper row but multiple leaves.
        merged = len(sources[index]) > 1
        key = canonical_aid_key(*coord) if merged else rows[group[0]].key
        metadata = dict(representative.metadata)
        provenance = dict(representative.provenance)
        signatures = [_offset_signature(row) for row in originals]
        compatible = len(set(signatures)) == 1 and signatures[0][0] != "unsupported"
        bounds = [_position_bound(row) for row in originals]
        known_bounds = [value for value in bounds if value is not None]
        bound = max(known_bounds) if compatible and len(known_bounds) == len(bounds) else None
        for field in _BOUND_KEYS:
            metadata.pop(field, None)
        metadata["position_uncertainty_m"] = bound
        metadata["position_uncertainty_status"] = (
            "known_maximum" if bound is not None else "unknown"
        )
        if not compatible:
            for field in _OFFSET_KEYS:
                metadata.pop(field, None)
            metadata["visible_feature_offset_geometry_unsupported"] = True
        extent = []
        for original in originals:
            value = dict(original.metadata).get("angular_half_width_deg", 0.0)
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and isfinite(value)
                and value >= 0
            ):
                extent.append(float(value))
        metadata["angular_half_width_deg"] = max(extent, default=0.0)
        prior_ids = sorted(
            {identity for row in originals if (identity := _physical_id(row)) is not None}
        )
        metadata.update(
            {
                "identity_resolution": RULE,
                "chart_coordinate_component_type": component_type,
                "prior_physical_entity_ids": prior_ids,
                "source_components_schema": COMPONENT_SCHEMA,
                "source_component_count": len(sources[index]),
                "source_components": sources[index],
                "geometry_aggregation_status": "compatible_offsets"
                if compatible
                else "unsupported_offsets",
            }
        )
        if index in ambiguous:
            metadata["identity_ambiguous"] = True
            metadata["physical_linkage_status"] = "unresolved"
            metadata["physical_entity_id"] = ambiguous[index][0]
            metadata["cross_coordinate_physical_ids"] = ambiguous[index][1]
        elif merged:
            metadata["identity_ambiguous"] = False
            metadata["physical_linkage_status"] = "resolved"
            metadata["physical_entity_id"] = canonical_aid_key(*coord)
        # An unmerged source row does not acquire an invented identity proof.
        # Its original identity fields, including absence, remain untouched.
        provenance["chart_coordinate_identity_rule"] = RULE
        provenance["source_component_keys"] = [c["key"] for c in sources[index]]
        result.append(
            replace(
                representative,
                key=key,
                latitude_deg=coord[0],
                longitude_deg=coord[1],
                normalized_type=component_type,
                metadata=tuple(sorted(metadata.items())),
                provenance=tuple(sorted(provenance.items())),
            )
        )
    keys = [row.key for row in result]
    if len(keys) != len(set(keys)):
        raise ValueError("Canonical aid key collides with an untouched record key.")
    return tuple(sorted(result, key=lambda row: (is_chart_aid(row), row.key)))


def canonicalize_chart_aids(catalog: CatalogV2) -> CatalogV2:
    """Apply exact-coordinate identity, reusing a bounded catalog-identity memo.

    The catalog, its records, and nested metadata/provenance must remain read-only
    while reused, as with PreparedPredictionContext. Construct a new catalog after
    changing source data. Identity-only lookup is constant-time and does not hash
    or compare the catalog. Both source-to-result and result-to-self entries are
    strongly held, with at most four entries retained. Bare record iterables are
    deliberately never memoized by canonicalize_chart_records.
    """
    if not isinstance(catalog, CatalogV2):
        raise TypeError("catalog must be CatalogV2.")
    with _catalog_cache_lock:
        cached = _catalog_cache.get(id(catalog))
        if cached is not None and cached[0] is catalog:
            _catalog_cache.move_to_end(id(catalog))
            return cached[1]
        result = replace(catalog, records=canonicalize_chart_records(catalog.records))
        _catalog_cache[id(catalog)] = (catalog, result)
        _catalog_cache[id(result)] = (result, result)
        while len(_catalog_cache) > _CATALOG_CACHE_LIMIT:
            _catalog_cache.popitem(last=False)
        return result


__all__ = [
    "is_chart_aid",
    "chart_coordinate_groups",
    "canonical_aid_key",
    "canonicalize_chart_records",
    "canonicalize_chart_aids",
]
