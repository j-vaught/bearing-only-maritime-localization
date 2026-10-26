#!/usr/bin/env python3
"""Acquire bounded, provenance-preserving NOAA ENC General/Coastal source layers.

Only source features are archived. A coverage polygon is not proof of known
water: consumers must apply CATCOV exclusions and require complete land
feature retrieval before declaring a region known.
"""

from __future__ import annotations

import argparse
import dataclasses
import gzip
import hashlib
import json
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BBOX = (-82.0, 24.5, -66.0, 45.5)
HOSTS = (
    "https://encdirect.noaa.gov",
    "https://gis.charttools.noaa.gov",
    "https://maritimeboundaries.noaa.gov",
)
SERVICES = {
    "general": (
        "/arcgis/rest/services/encdirect/enc_general/MapServer",
        {"coverage_area", "land_area"},
    ),
    "coastal": (
        "/arcgis/rest/services/encdirect/enc_coastal/MapServer",
        {"coverage_area", "land_area"},
    ),
}
TIMEOUT_S = 12
MAX_WALL_S = 900
MAX_DOWNLOAD_BYTES = 5 * 1024**3
MIN_FREE_BYTES = 40 * 1024**3
INITIAL_BATCH = 75
MAX_REQUESTS_PER_LAYER = 1600


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def object_id_field(metadata: dict[str, Any]) -> str:
    field = metadata.get("objectIdField") or metadata.get("objectIdFieldName")
    if field:
        return str(field)
    for info in metadata.get("fields", []):
        if info.get("type") == "esriFieldTypeOID":
            return str(info["name"])
    raise ValueError("Layer metadata has no object ID field.")


def envelope(bbox: tuple[float, float, float, float]) -> str:
    xmin, ymin, xmax, ymax = bbox
    return json.dumps(
        {
            "xmin": xmin,
            "ymin": ymin,
            "xmax": xmax,
            "ymax": ymax,
            "spatialReference": {"wkid": 4326},
        },
        separators=(",", ":"),
    )


class BoundedClient:
    def __init__(
        self,
        *,
        timeout_s: int = TIMEOUT_S,
        max_wall_s: int = MAX_WALL_S,
        max_requests: int = MAX_REQUESTS_PER_LAYER,
    ):
        self.timeout_s = timeout_s
        self.deadline = time.monotonic() + max_wall_s
        self.max_requests = max_requests
        self.requests = 0
        self.lock = threading.Lock()
        self.errors: list[dict[str, Any]] = []

    def request(self, url: str) -> tuple[dict[str, Any] | None, bytes, str | None]:
        with self.lock:
            if time.monotonic() >= self.deadline:
                raise TimeoutError("acquisition wall-time budget exhausted")
            if self.requests >= self.max_requests:
                raise RuntimeError("request-count budget exhausted")
            self.requests += 1
        request = urllib.request.Request(
            url, headers={"User-Agent": "bearing-only-maritime-localization/1.0"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                raw = response.read()
            payload = json.loads(raw)
            if isinstance(payload, dict) and "error" not in payload:
                return payload, raw, None
            err = payload.get("error", payload) if isinstance(payload, dict) else payload
            return None, raw, json.dumps(err, sort_keys=True)[:2000]
        except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
            return None, b"", f"{type(exc).__name__}: {exc}"

    def get_first(
        self, path: str, params: dict[str, str]
    ) -> tuple[dict[str, Any] | None, str | None, str | None]:
        query = urllib.parse.urlencode(params)
        for host in HOSTS:
            url = f"{host}{path}?{query}"
            for attempt in range(2):
                payload, raw, error = self.request(url)
                if payload is not None:
                    return payload, url, sha256_bytes(raw)
                self.errors.append({"url": url, "attempt": attempt + 1, "error": error})
        return None, None, None

    def query_batch(
        self, path: str, layer_id: int, ids: list[int], id_field: str
    ) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
        params = {
            "f": "json",
            "objectIds": ",".join(map(str, ids)),
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": "4326",
        }
        query = urllib.parse.urlencode(params)
        failures: list[dict[str, Any]] = []
        for host in HOSTS:
            url = f"{host}{path}/{layer_id}/query?{query}"
            payload, raw, error = self.request(url)
            if payload is None:
                failures.append({"url": url, "error": error})
                continue
            rows = payload.get("features")
            if not isinstance(rows, list):
                failures.append({"url": url, "error": "missing features array"})
                continue
            returned = [row.get("attributes", {}).get(id_field) for row in rows]
            try:
                returned_ids = [int(value) for value in returned if value is not None]
            except (TypeError, ValueError):
                returned_ids = []
            if (
                len(returned_ids) != len(rows)
                or len(returned_ids) != len(set(returned_ids))
                or set(returned_ids) != set(ids)
            ):
                failures.append(
                    {
                        "url": url,
                        "error": "source ID set mismatch",
                        "requested": len(ids),
                        "returned": len(returned_ids),
                        "missing": sorted(set(ids) - set(returned_ids))[:30],
                        "extra": sorted(set(returned_ids) - set(ids))[:30],
                    }
                )
                continue
            return rows, {
                "url": url,
                "requested_ids": ids,
                "returned_ids": sorted(returned_ids),
                "response_sha256": sha256_bytes(raw),
                "response_bytes": len(raw),
                "response_exceeded_transfer_limit": bool(
                    payload.get("exceededTransferLimit", False)
                ),
                "failures_before_success": failures,
            }
        return None, {"requested_ids": ids, "failures": failures}


def query_ids_tiled(
    client: BoundedClient,
    path: str,
    layer_id: int,
    bbox: tuple[float, float, float, float],
    *,
    max_depth: int = 4,
) -> tuple[list[int], dict[str, Any], bool]:
    """Return the exact union of IDs, subdividing failed extents deterministically."""
    tiles: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    def query_one(
        extent: tuple[float, float, float, float],
    ) -> tuple[list[int] | None, dict[str, Any]]:
        xmin, ymin, xmax, ymax = extent
        geom = json.dumps(
            {
                "xmin": xmin,
                "ymin": ymin,
                "xmax": xmax,
                "ymax": ymax,
                "spatialReference": {"wkid": 4326},
            },
            separators=(",", ":"),
        )
        params = {
            "f": "json",
            "where": "1=1",
            "returnIdsOnly": "true",
            "geometry": geom,
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
        }
        query = urllib.parse.urlencode(params)
        failures: list[dict[str, Any]] = []
        for host in HOSTS:
            url = f"{host}{path}/{layer_id}/query?{query}"
            payload, raw, error = client.request(url)
            raw_ids = payload.get("objectIds") if payload else None
            if isinstance(raw_ids, list):
                try:
                    ids = [int(item) for item in raw_ids]
                except (TypeError, ValueError):
                    ids = []
                if len(ids) == len(raw_ids) and len(ids) == len(set(ids)):
                    return ids, {
                        "bbox": list(extent),
                        "url": url,
                        "response_sha256": sha256_bytes(raw),
                        "response_bytes": len(raw),
                        "returned_ids_count": len(ids),
                        "returned_ids_sha256": sha256_bytes(canonical_json(sorted(ids))),
                        "host_failures_before_success": failures,
                    }
                failures.append({"url": url, "error": "duplicate or non-integer IDs"})
            else:
                failures.append({"url": url, "error": error or "response omitted objectIds"})
        return None, {"bbox": list(extent), "failures": failures}

    def visit(extent: tuple[float, float, float, float], depth: int) -> None:
        ids, record = query_one(extent)
        if ids is not None:
            tiles.append({**record, "ids": ids})
            return
        if depth >= max_depth:
            unresolved.append(record)
            return
        xmin, ymin, xmax, ymax = extent
        xmid, ymid = (xmin + xmax) / 2, (ymin + ymax) / 2
        for child in (
            (xmin, ymin, xmid, ymid),
            (xmid, ymin, xmax, ymid),
            (xmin, ymid, xmid, ymax),
            (xmid, ymid, xmax, ymax),
        ):
            visit(child, depth + 1)

    xmin, ymin, xmax, ymax = bbox
    full_params = {
        "f": "json",
        "where": "1=1",
        "returnIdsOnly": "true",
        "geometry": json.dumps(
            {
                "xmin": xmin,
                "ymin": ymin,
                "xmax": xmax,
                "ymax": ymax,
                "spatialReference": {"wkid": 4326},
            },
            separators=(",", ":"),
        ),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }
    full_payload, full_url, full_hash = client.get_first(f"{path}/{layer_id}/query", full_params)
    raw_ids = full_payload.get("objectIds") if full_payload else None
    full_ids: list[int] = []
    if isinstance(raw_ids, list):
        try:
            full_ids = [int(item) for item in raw_ids]
        except (TypeError, ValueError):
            full_ids = []
    if isinstance(raw_ids, list) and len(full_ids) == len(raw_ids) == len(set(full_ids)):
        tiles.append(
            {
                "bbox": list(bbox),
                "url": full_url,
                "response_sha256": full_hash,
                "returned_ids_count": len(full_ids),
                "returned_ids_sha256": sha256_bytes(canonical_json(sorted(full_ids))),
                "ids": full_ids,
            }
        )
    else:
        visit(bbox, 0)
    unique_ids = sorted({oid for tile in tiles for oid in tile["ids"]})
    return (
        unique_ids,
        {
            "mode": "full_extent"
            if len(tiles) == 1 and not unresolved
            else "adaptive_spatial_tiles",
            "tiles": tiles,
            "unresolved_tiles": unresolved,
            "requested_ids_count": len(unique_ids),
            "requested_ids_sha256": sha256_bytes(canonical_json(unique_ids)),
        },
        not unresolved,
    )


def acquire_layer(
    client: BoundedClient,
    *,
    band: str,
    role: str,
    path: str,
    layer: dict[str, Any],
    bbox: tuple[float, float, float, float],
    started_utc: str,
) -> dict[str, Any]:
    layer_id = int(layer["id"])
    id_field = object_id_field(layer)
    ids, id_query, id_query_complete = query_ids_tiled(client, path, layer_id, bbox)

    results: dict[int, dict[str, Any]] = {}
    batch_log: list[dict[str, Any]] = []

    def fetch_recursive(batch: list[int]) -> None:
        if not batch:
            return
        rows, record = client.query_batch(path, layer_id, batch, id_field)
        if rows is not None:
            for row in rows:
                results[int(row["attributes"][id_field])] = row
            batch_log.append(record)
            return
        batch_log.append(record)
        if len(batch) == 1:
            return
        mid = len(batch) // 2
        fetch_recursive(batch[:mid])
        fetch_recursive(batch[mid:])

    for start in range(0, len(ids), INITIAL_BATCH):
        fetch_recursive(ids[start : start + INITIAL_BATCH])

    returned_ids = sorted(results)
    missing_ids = sorted(set(ids) - set(returned_ids))
    invalid_geometry_ids: list[int] = []
    for oid, feature in results.items():
        geometry = feature.get("geometry")
        rings = geometry.get("rings") if isinstance(geometry, dict) else None
        if not isinstance(rings, list) or not rings:
            invalid_geometry_ids.append(oid)
            continue
        try:
            from shapely.geometry import Polygon

            polygons = [Polygon(ring) for ring in rings]
            if any(poly.is_empty or not poly.is_valid or poly.area == 0 for poly in polygons):
                invalid_geometry_ids.append(oid)
        except Exception:
            invalid_geometry_ids.append(oid)
    archive = {
        "schema_version": 1,
        "source": "NOAA ENC Direct",
        "service_path": path,
        "layer_id": layer_id,
        "layer_name": layer.get("name"),
        "band": band,
        "role": role,
        "crs": "EPSG:4326",
        "bbox": list(bbox),
        "retrieval_started_utc": started_utc,
        "source_layer_editing_info": layer.get("editingInfo"),
        "id_query": id_query,
        "id_query_complete": id_query_complete,
        "feature_batch_provenance": batch_log,
        "object_id_field": id_field,
        "requested_ids": ids,
        "returned_ids": returned_ids,
        "missing_ids": missing_ids,
        "invalid_geometry_ids": sorted(invalid_geometry_ids),
        "features": [results[oid] for oid in returned_ids],
    }
    return {
        "archive": archive,
        "id_query": id_query,
        "batches": batch_log,
        "missing_ids": missing_ids,
        "invalid_geometry_ids": sorted(invalid_geometry_ids),
        "exact_id_complete": not missing_ids and id_query_complete,
        "geometry_complete": not invalid_geometry_ids,
        "feature_count": len(returned_ids),
        "object_id_field": id_field,
        "layer_id": layer_id,
    }


def gzip_deterministic(path: Path, payload: bytes) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        with gzip.GzipFile(filename="", mode="wb", fileobj=stream, mtime=0, compresslevel=6) as gz:
            gz.write(payload)
    data = path.read_bytes()
    return len(data), sha256_bytes(data)


def build_map_v2(output_root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Build a conservative scale-aware General/Coastal base MapV2 candidate."""
    from pyproj import Geod
    from shapely import make_valid
    from shapely.geometry import GeometryCollection, box, mapping, shape
    from shapely.ops import unary_union

    from maritime_localization_shared.contracts.inputs_v2 import MapV2

    def polygonal(geometry):
        candidate = make_valid(geometry) if not geometry.is_valid else geometry
        parts = []
        if candidate.geom_type == "Polygon":
            parts = [candidate]
        elif candidate.geom_type in {"MultiPolygon", "GeometryCollection"}:
            for part in candidate.geoms:
                if part.geom_type == "Polygon" and not part.is_empty:
                    parts.append(part)
                elif part.geom_type == "MultiPolygon":
                    parts.extend(poly for poly in part.geoms if not poly.is_empty)
        return unary_union(parts) if parts else GeometryCollection()

    import importlib.util

    converter_path = ROOT / "tools" / "acquire_accuracy_maps.py"
    spec = importlib.util.spec_from_file_location("accuracy_map_converter", converter_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load ENC geometry converter")
    converter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(converter)
    to_geojson_geometry = converter.to_geojson_geometry

    domain = box(*BBOX)
    coverage: dict[str, Any] = {}
    land: dict[str, Any] = {}
    complete_bands: set[str] = set()
    for band in ("coastal", "general"):
        coverage_entry = manifest["source_layers"].get(f"{band}_coverage_area", {})
        land_entry = manifest["source_layers"].get(f"{band}_land_area", {})
        if not (coverage_entry.get("complete") and land_entry.get("complete")):
            continue
        coverage_archive = json.loads(
            gzip.decompress((ROOT / coverage_entry["archive_path"]).read_bytes())
        )
        land_archive = json.loads(gzip.decompress((ROOT / land_entry["archive_path"]).read_bytes()))
        available = []
        excluded = []
        for raw in coverage_archive["features"]:
            attrs = raw.get("attributes") or {}
            geometry = to_geojson_geometry(raw.get("geometry") or {})
            if geometry is None:
                raise ValueError(f"Coverage feature {band}/{attrs.get('OBJECTID')} has no polygon")
            category = str(attrs.get("CATCOV", "")).strip().casefold()
            candidate = shape(geometry).intersection(domain)
            if category in {"1", "coverage available"}:
                available.append(candidate)
            elif category in {"2", "no coverage available"}:
                excluded.append(candidate)
            else:
                raise ValueError(f"Unrecognized CATCOV {attrs.get('CATCOV')!r} in {band} coverage")
        raw_coverage = polygonal(unary_union(available) if available else GeometryCollection())
        if excluded:
            raw_coverage = polygonal(raw_coverage.difference(polygonal(unary_union(excluded))))
        coverage[band] = polygonal(raw_coverage.intersection(domain))
        land_geometries = []
        for raw in land_archive["features"]:
            geometry = to_geojson_geometry(raw.get("geometry") or {})
            if geometry is None:
                object_id = (raw.get("attributes") or {}).get("OBJECTID")
                raise ValueError(f"Land feature {band}/{object_id} has no polygon in {band}.")
            land_geometries.append(shape(geometry).intersection(domain))
        land[band] = polygonal(
            unary_union(land_geometries) if land_geometries else GeometryCollection()
        )
        complete_bands.add(band)

    # Coastal is the more detailed band; General fills only where Coastal has
    # no reported coverage. The domain complement remains explicitly unknown.
    coastal_coverage = coverage.get("coastal", GeometryCollection())
    general_coverage = coverage.get("general", GeometryCollection())
    general_effective = polygonal(general_coverage.difference(coastal_coverage))
    known = polygonal(unary_union([coastal_coverage, general_effective]))
    coastal_land = polygonal(
        land.get("coastal", GeometryCollection()).intersection(coastal_coverage)
    )
    general_land = polygonal(
        land.get("general", GeometryCollection()).intersection(general_effective)
    )
    land_geometry = polygonal(unary_union([coastal_land, general_land]))
    unknown = polygonal(domain.difference(known))
    status = "unknown" if known.is_empty else ("known" if unknown.is_empty else "partial")

    def encode(geometry):
        return json.dumps(mapping(geometry), sort_keys=True, separators=(",", ":"))

    geod = Geod(ellps="WGS84")
    domain_area = abs(geod.geometry_area_perimeter(domain)[0])
    known_area = abs(geod.geometry_area_perimeter(known)[0]) if not known.is_empty else 0.0
    map_v2 = MapV2(
        crs="EPSG:4326",
        source="NOAA ENC Direct General and Coastal layers; scale-aware base candidate",
        map_version="noaa-enc-east-coast-general-coastal-base-v1",
        coverage_status=status,
        coverage_geometry_geojson=encode(known) if not known.is_empty else None,
        unknown_geometry_geojson=encode(unknown) if status == "partial" else None,
        land_geometry_geojson=encode(land_geometry) if not land_geometry.is_empty else None,
    )
    encoded = canonical_json(dataclasses.asdict(map_v2))
    output_path = output_root / "coast-base.map-v2.json.gz"
    size, digest = gzip_deterministic(output_path, encoded)
    return {
        "path": str(output_path.relative_to(ROOT)),
        "bytes": size,
        "sha256": digest,
        "complete": bool(complete_bands),
        "coverage_status": map_v2.coverage_status,
        "coverage_bounds_wgs84": list(known.bounds) if not known.is_empty else None,
        "known_area_km2": round(known_area / 1e6, 3),
        "domain_area_km2": round(domain_area / 1e6, 3),
        "known_fraction_of_domain": round(known_area / domain_area, 8) if domain_area else 0.0,
        "unknown_fraction_of_domain": round(1.0 - known_area / domain_area, 8)
        if domain_area
        else 1.0,
        "crs": map_v2.crs,
        "complete_scale_bands": sorted(complete_bands),
        "source_layer_archive_hashes": {
            key: value.get("archive_sha256")
            for key, value in manifest["source_layers"].items()
            if value.get("archive_sha256")
        },
    }


def recover_cached_layer(
    output_root: Path, *, band: str, role: str, path: str, layer_id: int
) -> dict[str, Any] | None:
    """Reuse a prior archive only when its source identity, OID set, and polygons verify."""
    archive_path = output_root / f"{band}_{role}.esri.json.gz"
    if not archive_path.exists():
        return None
    compressed = archive_path.read_bytes()
    try:
        archive = json.loads(gzip.decompress(compressed))
    except (OSError, json.JSONDecodeError):
        return None
    requested = archive.get("requested_ids")
    returned = archive.get("returned_ids")
    id_field = archive.get("object_id_field")
    features = archive.get("features")
    if (
        archive.get("service_path") != path
        or archive.get("layer_id") != layer_id
        or archive.get("band") != band
        or archive.get("role") != role
        or not isinstance(requested, list)
        or requested != sorted(set(requested))
        or returned != requested
        or not isinstance(features, list)
        or not id_field
    ):
        return None
    feature_ids = [feature.get("attributes", {}).get(id_field) for feature in features]
    try:
        feature_ids = sorted(int(value) for value in feature_ids)
    except (TypeError, ValueError):
        return None
    invalid: list[int] = []
    from shapely.geometry import Polygon

    for feature in features:
        oid = int(feature["attributes"][id_field])
        rings = (feature.get("geometry") or {}).get("rings")
        if not isinstance(rings, list) or not rings:
            invalid.append(oid)
            continue
        try:
            if any(not Polygon(ring).is_valid or Polygon(ring).area == 0 for ring in rings):
                invalid.append(oid)
        except Exception:
            invalid.append(oid)
    if feature_ids != requested or invalid:
        return None
    digest = sha256_bytes(compressed)
    return {
        "archive_path": str(archive_path.relative_to(ROOT)),
        "archive_bytes": len(compressed),
        "archive_sha256": digest,
        "archive_uncompressed_bytes": len(gzip.decompress(compressed)),
        "complete": True,
        "exact_id_complete": True,
        "geometry_complete": True,
        "feature_count": len(features),
        "missing_ids": [],
        "invalid_geometry_ids": [],
        "object_id_field": id_field,
        "layer_id": layer_id,
        "id_query": archive.get("id_query")
        or {
            "mode": "verified_cached_archive",
            "requested_ids_count": len(requested),
            "requested_ids_sha256": sha256_bytes(canonical_json(requested)),
            "requested_equals_returned_feature_ids": True,
            "original_query_record": "not embedded in prior archive",
        },
        "data_origin": "verified_cached_archive",
        "source_retrieval_epoch_utc": archive.get("retrieval_started_utc"),
        "cache_verification": {
            "source_path_layer_band_match": True,
            "requested_ids_equal_returned_ids_and_feature_oids": True,
            "all_source_polygon_rings_valid": True,
        },
    }


def run(
    output_root: Path,
    *,
    client_factory: Callable[[], BoundedClient] = BoundedClient,
    bbox: tuple[float, float, float, float] = BBOX,
) -> dict[str, Any]:
    free = shutil.disk_usage(output_root.parent if output_root.parent.exists() else ROOT).free
    if free < MIN_FREE_BYTES:
        raise RuntimeError(f"disk free {free} is below required {MIN_FREE_BYTES} bytes")
    started = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    output_root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "source": "NOAA ENC Direct",
        "retrieval_started_utc": started,
        "bbox": list(bbox),
        "crs": "EPSG:4326",
        "acquisition_limits": {
            "timeout_seconds": TIMEOUT_S,
            "wall_seconds": MAX_WALL_S,
            "max_archive_bytes": MAX_DOWNLOAD_BYTES,
            "minimum_free_bytes": MIN_FREE_BYTES,
            "initial_batch_ids": INITIAL_BATCH,
            "host_fallback_count": len(HOSTS),
        },
        "services": {},
        "source_layers": {},
        "errors": [],
    }
    total_bytes = 0
    client = client_factory()
    for band, (path, wanted) in SERVICES.items():
        service_error_start = len(client.errors)
        metadata, metadata_url, metadata_hash = client.get_first(path, {"f": "json"})
        if metadata is None:
            manifest["services"][band] = {"complete": False, "error": client.errors[-3:]}
            manifest["errors"].append(f"{band}: service metadata unavailable")
            continue
        manifest["services"][band] = {
            "metadata_url": metadata_url,
            "metadata_sha256": metadata_hash,
            "service_description": metadata.get("serviceDescription"),
            "full_extent": metadata.get("fullExtent"),
            "document_info": metadata.get("documentInfo"),
            "service_item_id": metadata.get("serviceItemId"),
            "request_errors": client.errors[service_error_start:],
        }
        layers = {
            str(layer.get("name", "")).split(".", 1)[-1].casefold(): layer
            for layer in metadata.get("layers", [])
            if str(layer.get("name", "")).split(".", 1)[-1].casefold() in wanted
        }
        for role in sorted(wanted):
            layer = layers.get(role)
            if layer is None:
                manifest["source_layers"][f"{band}_{role}"] = {
                    "complete": False,
                    "error": "layer absent in metadata",
                }
                manifest["errors"].append(f"{band}/{role}: layer absent in metadata")
                continue
            layer_error_start = len(client.errors)
            layer_request_start = client.requests
            layer_error_start = len(client.errors)
            layer_request_start = client.requests
            try:
                layer_meta, layer_metadata_url, layer_metadata_hash = client.get_first(
                    f"{path}/{int(layer['id'])}", {"f": "json"}
                )
                if layer_meta is None:
                    raise RuntimeError(f"Layer metadata unavailable for {band}/{role}")
                layer.update(layer_meta)
                result = acquire_layer(
                    client,
                    band=band,
                    role=role,
                    path=path,
                    layer=layer,
                    bbox=bbox,
                    started_utc=started,
                )
                serialized = canonical_json(result["archive"])
                archive_path = output_root / f"{band}_{role}.esri.json.gz"
                is_complete = bool(result["exact_id_complete"] and result["geometry_complete"])
                cached = (
                    None
                    if is_complete
                    else recover_cached_layer(
                        output_root, band=band, role=role, path=path, layer_id=int(layer["id"])
                    )
                )
                if cached is not None:
                    cached["refresh_failure"] = {
                        "missing_ids": result["missing_ids"],
                        "invalid_geometry_ids": result["invalid_geometry_ids"],
                    }
                    result = cached
                    archive_label = result["archive_path"]
                    size = result["archive_bytes"]
                    digest = result["archive_sha256"]
                else:
                    size, digest = gzip_deterministic(archive_path, serialized)
                    archive_label = str(archive_path.relative_to(ROOT))
                    total_bytes += size
                    if total_bytes > MAX_DOWNLOAD_BYTES:
                        archive_path.unlink(missing_ok=True)
                        raise RuntimeError("archive budget of 5 GiB exceeded")
                result.pop("archive", None)
                if "archive_path" not in result:
                    try:
                        archive_label = str(archive_path.relative_to(ROOT))
                    except ValueError:
                        archive_label = str(archive_path)
                result.update(
                    {
                        "archive_path": archive_label,
                        "archive_bytes": size,
                        "archive_sha256": digest,
                        "archive_uncompressed_bytes": result.get(
                            "archive_uncompressed_bytes", len(serialized)
                        ),
                        "complete": bool(
                            result["exact_id_complete"] and result["geometry_complete"]
                        ),
                        "source_metadata_url": metadata_url,
                        "source_metadata_sha256": metadata_hash,
                        "layer_metadata_url": layer_metadata_url,
                        "layer_metadata_sha256": layer_metadata_hash,
                        "requests_used": client.requests - layer_request_start,
                        "request_errors": client.errors[layer_error_start:],
                    }
                )
                manifest["source_layers"][f"{band}_{role}"] = result
            except Exception as exc:
                manifest["source_layers"][f"{band}_{role}"] = {
                    "complete": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "requests_used": client.requests - layer_request_start,
                    "request_errors": client.errors[layer_error_start:],
                }
                manifest["errors"].append(f"{band}/{role}: {type(exc).__name__}: {exc}")
    manifest["archive_total_bytes"] = sum(
        item.get("archive_bytes", 0) for item in manifest["source_layers"].values()
    )
    try:
        manifest["base_map_v2"] = build_map_v2(output_root, manifest)
    except Exception as exc:
        manifest["base_map_v2"] = {"complete": False, "error": f"{type(exc).__name__}: {exc}"}
        manifest["errors"].append(f"MapV2 candidate: {type(exc).__name__}: {exc}")
    manifest["retrieval_completed_utc"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    manifest["deliverable_total_bytes"] = manifest["archive_total_bytes"] + manifest.get(
        "base_map_v2", {}
    ).get("bytes", 0)
    manifest["overall_complete"] = (
        all(item.get("complete") is True for item in manifest["source_layers"].values())
        and len(manifest["source_layers"]) == 4
    )
    manifest["overall_complete_semantics"] = (
        "All four General and Coastal layers have complete exact-ID and valid-polygon retrieval. "
        "MapV2 coverage remains partial; the rest of the domain is explicitly unknown."
    )
    manifest_path = output_root / "manifest.json"
    manifest_path.write_bytes(canonical_json(manifest) + b"\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "data/maps/coast-base")
    args = parser.parse_args(argv)
    try:
        manifest = run(args.output_root.resolve())
    except Exception as exc:
        print(f"acquisition failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "overall_complete": manifest["overall_complete"],
                "source_layers": {
                    key: {
                        "complete": val.get("complete"),
                        "features": val.get("feature_count"),
                        "missing": len(val.get("missing_ids", [])),
                        "invalid_geometry": len(val.get("invalid_geometry_ids", [])),
                        "archive_path": val.get("archive_path"),
                        "archive_sha256": val.get("archive_sha256"),
                        "error": val.get("error"),
                    }
                    for key, val in manifest["source_layers"].items()
                },
                "errors": manifest["errors"],
            },
            indent=2,
        )
    )
    return 0 if manifest["overall_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
