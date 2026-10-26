#!/usr/bin/env python3
"""Acquire NOAA ENC land and chart coverage polygons for the East Coast domain.

All response and manifest files are written beneath .agent-work/cache/maps.
Network retries and per-request timeouts are deliberately bounded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SHARED_ROOT = Path(__file__).resolve().parents[4]
CACHE = SHARED_ROOT / ".agent-work/cache/maps"
MANIFEST = ROOT / "data/maps/accuracy-east-coast-enc.manifest.json"
BBOX = (-82.0, 24.5, -66.0, 45.5)
BANDS = {
    "overview": ("overview",),
    "general": ("general",),
    "coastal": ("coastal",),
    "approach": ("approach",),
    "harbour": ("harbour", "harbor"),
    "berthing": ("berthing",),
}
HOSTS = (
    "https://encdirect.noaa.gov",
    "https://gis.charttools.noaa.gov",
    "https://maritimeboundaries.noaa.gov",
)
TIMEOUT_SECONDS = 12
MAX_WALL_SECONDS = 900
RETRIES_PER_HOST = 1
BATCH_SIZE = 75
MAX_WORKERS = 6
OUT_FIELDS = "*"
BBOX_NAME = "eastern-seaboard-24-port-domain"


def request_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url, headers={"User-Agent": "bearing-only-maritime-localization/1.0"}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.load(response)


def fetch(urls: list[str]) -> tuple[dict[str, Any] | None, list[str], str | None]:
    errors: list[str] = []
    for url in urls:
        for attempt in range(RETRIES_PER_HOST + 1):
            try:
                result = request_json(url)
                if "error" not in result:
                    return result, [], url
                errors.append(f"{url}: {result['error']}")
                break
            except (
                OSError,
                TimeoutError,
                urllib.error.URLError,
                json.JSONDecodeError,
            ) as exc:
                errors.append(f"{url} attempt {attempt + 1}: {exc}")
                if attempt < RETRIES_PER_HOST:
                    time.sleep(0.5)
    return None, errors, None


def service_info() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    services: dict[str, Any] = {}
    for band, aliases in BANDS.items():
        found: tuple[dict[str, Any], str, str] | None = None
        for alias in aliases:
            path = f"/arcgis/rest/services/encdirect/enc_{alias}/MapServer"
            payload, errors, source_url = fetch([f"{host}{path}?f=json" for host in HOSTS])
            if payload is not None and payload.get("layers"):
                found = (payload, path, source_url or "")
                break
            issues.extend({"band": band, "stage": "metadata", "detail": error} for error in errors)
        if found is None:
            services[band] = None
            issues.append({"band": band, "stage": "metadata", "detail": "service unavailable"})
            continue
        metadata, path, host_url = found
        wanted = ("coverage_area", "land_area")
        layers: dict[str, Any] = {}
        for layer in metadata.get("layers", []):
            layer_name = layer.get("name", "").split(".", 1)[-1].casefold()
            if layer_name in wanted and layer.get("type") == "Feature Layer":
                layers[layer_name] = layer
        services[band] = {
            "path": path,
            "metadata_host": host_url.split("/arcgis/", 1)[0],
            "service_description": metadata.get("serviceDescription"),
            "full_extent": metadata.get("fullExtent"),
            "layers": layers,
        }
        for name in wanted:
            if name not in layers:
                issues.append(
                    {"band": band, "layer": name, "stage": "metadata", "detail": "layer absent"}
                )
    return services, issues


def _query_urls(
    path: str, layer_id: int, params: dict[str, Any], preferred_host: str | None = None
) -> list[str]:
    query = urllib.parse.urlencode(params)
    hosts = [preferred_host, *HOSTS] if preferred_host else list(HOSTS)
    hosts = list(dict.fromkeys(host for host in hosts if host))
    return [f"{host}{path}/{layer_id}/query?{query}" for host in hosts]


def layer_ids(
    path: str,
    layer_id: int,
    preferred_host: str | None = None,
    bbox: tuple[float, float, float, float] = BBOX,
) -> tuple[list[int] | None, list[str]]:
    params = {
        "f": "json",
        "where": "1=1",
        "returnIdsOnly": "true",
        "geometry": ",".join(str(value) for value in bbox),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }
    urls = _query_urls(path, layer_id, params, preferred_host)
    response, errors, source_url = fetch(urls)
    if response is None:
        return None, errors
    ids = response.get("objectIds")
    if ids is None:
        summary = json.dumps(response, sort_keys=True, separators=(",", ":"))
        return None, errors + [
            f"{source_url}: response omitted objectIds; response={summary[:2000]}"
        ]
    return sorted(int(value) for value in ids), errors


def layer_features(
    path: str,
    layer_id: int,
    ids: list[int],
    preferred_host: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    batches = [ids[start : start + BATCH_SIZE] for start in range(0, len(ids), BATCH_SIZE)]
    features: list[dict[str, Any]] = []
    errors: list[str] = []

    def fetch_batch(batch: list[int]) -> tuple[list[dict[str, Any]], list[str]]:
        params = {
            "f": "json",
            "objectIds": ",".join(str(value) for value in batch),
            "outFields": OUT_FIELDS,
            "returnGeometry": "true",
            "outSR": "4326",
        }
        response, failures, _ = fetch(_query_urls(path, layer_id, params, preferred_host))
        if response is None:
            return [], failures + [f"feature batch {batch[0]}-{batch[-1]} unavailable"]
        rows = response.get("features")
        if not isinstance(rows, list):
            return [], failures + [f"feature batch {batch[0]}-{batch[-1]} omitted features"]
        returned_ids = {
            row.get("attributes", {}).get("OBJECTID")
            for row in rows
            if row.get("attributes", {}).get("OBJECTID") is not None
        }
        if returned_ids and returned_ids != set(batch):
            failures.append(
                f"feature batch {batch[0]}-{batch[-1]} returned "
                f"{len(returned_ids)}/{len(batch)} IDs"
            )
        elif not returned_ids and len(rows) != len(batch):
            failures.append(
                f"feature batch {batch[0]}-{batch[-1]} returned "
                f"{len(rows)}/{len(batch)} rows without source IDs"
            )
        return rows, failures

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(fetch_batch, batch) for batch in batches]
        for completed, future in enumerate(as_completed(futures), start=1):
            batch_features, batch_errors = future.result()
            features.extend(batch_features)
            errors.extend(batch_errors)
            if completed % 20 == 0 or completed == len(batches):
                print(
                    f"    layer {layer_id}: fetched {completed}/{len(batches)} batches",
                    flush=True,
                )
    return features, errors


def to_geojson_geometry(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Convert Esri polygon rings to RFC 7946 polygon or multipolygon geometry."""
    rings = raw.get("rings")
    if not isinstance(rings, list) or not rings:
        return None

    def area(ring: list[list[float]]) -> float:
        return (
            sum(
                ring[i][0] * ring[(i + 1) % len(ring)][1]
                - ring[(i + 1) % len(ring)][0] * ring[i][1]
                for i in range(len(ring))
            )
            / 2.0
        )

    # Esri outer rings are clockwise and holes counterclockwise. Group each
    # hole with its smallest containing outer using only point-in-ring arithmetic
    # to avoid adding a runtime geometry dependency to the acquisition script.
    outers = [ring for ring in rings if area(ring) < 0]
    holes = [ring for ring in rings if area(ring) >= 0]
    if not outers:
        return None
    polygons: list[list[list[list[float]]]] = [[outer] for outer in outers]
    for hole in holes:
        point = hole[0]
        containing = [
            (abs(area(outer)), index)
            for index, outer in enumerate(outers)
            if _point_in_ring(point, outer)
        ]
        if containing:
            polygons[min(containing)[1]].append(hole)
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def _point_in_ring(point: list[float], ring: list[list[float]]) -> bool:
    x, y = point
    inside = False
    for i, (xi, yi) in enumerate(ring):
        xj, yj = ring[i - 1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
    return inside


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_aid_catalog_v2() -> dict[str, Any]:
    """Write the full-coast source-row CatalogV2 into the shared cache."""
    from maritime_localization_ingest.enc import (
        enc_aids_catalog_to_catalog_v2,
        load_enc_physical_aid_catalog,
    )
    from maritime_localization_shared.serialization import contract_to_dict

    aids_path = ROOT / "data/chart-aids/eastern-seaboard-enc-aids.csv"
    catalog = enc_aids_catalog_to_catalog_v2(load_enc_physical_aid_catalog(aids_path))
    output = CACHE / "noaa-enc-east-coast-aids-catalog-v2.json"
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(contract_to_dict(catalog), separators=(",", ":"), allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(output)
    return {
        "path": str(output),
        "sha256": sha256(output),
        "bytes": output.stat().st_size,
        "record_count": len(catalog.records),
        "source_file": str(aids_path),
        "source_sha256": sha256(aids_path),
        "source_manifest": str(aids_path.with_suffix(".manifest.json")),
        "physical_entity_id_is_present_only_for_unambiguous_groups": True,
        "source_rows_and_attributes_retained": True,
    }


def verify_and_complete_port_sources(
    collection_path: Path,
    services: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Check/fill ENC object IDs inside each port's 15 km map window."""
    import csv

    from pyproj import Geod

    payload = json.loads(collection_path.read_text(encoding="utf-8"))
    features = payload["features"]
    present: dict[tuple[str, str], set[int]] = {}
    for feature in features:
        props = feature.get("properties", {})
        object_id = props.get("OBJECTID")
        if object_id is None:
            continue
        key = (
            str(props.get("scale_band")),
            str(props.get("service_layer", "")).rsplit(".", 1)[-1].casefold(),
        )
        present.setdefault(key, set()).add(int(object_id))

    ports_path = ROOT / "data/sites/east-coast-major-ports-top150-2024.csv"
    geod = Geod(ellps="WGS84")
    port_rows = list(csv.DictReader(ports_path.open(newline="", encoding="utf-8")))
    jobs: list[tuple[str, str, str, str, int, tuple[float, float, float, float]]] = []
    for port in port_rows:
        site_id = port["site_id"]
        latitude = float(port["latitude_deg"])
        longitude = float(port["longitude_deg"])
        points = [
            geod.fwd(longitude, latitude, azimuth, 15_000)[:2] for azimuth in range(0, 360, 5)
        ]
        bbox = (
            min(point[0] for point in points),
            min(point[1] for point in points),
            max(point[0] for point in points),
            max(point[1] for point in points),
        )
        for band in BANDS:
            service = services.get(band)
            if service is None:
                continue
            for layer_name, layer in service["layers"].items():
                jobs.append(
                    (
                        site_id,
                        band,
                        layer_name,
                        service["path"],
                        int(layer["id"]),
                        bbox,
                    )
                )

    def query(
        job: tuple[str, str, str, str, int, tuple[float, float, float, float]],
    ) -> tuple[tuple[str, str, str], list[int] | None, list[str]]:
        site_id, band, layer_name, path, layer_id, bbox = job
        service = services[band]
        ids, errors = layer_ids(path, layer_id, service["metadata_host"], bbox)
        return (site_id, band, layer_name), ids, errors

    expected: dict[tuple[str, str, str], set[int] | None] = {}
    query_failures: dict[tuple[str, str, str], list[str]] = {}
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(query, job) for job in jobs]
        for future in as_completed(futures):
            key, ids, errors = future.result()
            expected[key] = None if ids is None else set(ids)
            if ids is None:
                query_failures[key] = errors
                print(f"{key[0]} {key[1]}/{key[2]} object ID query unavailable", flush=True)

    missing_by_layer: dict[tuple[str, str], set[int]] = {}
    for (_, band, layer_name), ids in expected.items():
        if ids is None:
            continue
        missing = ids - present.get((band, layer_name), set())
        if missing:
            missing_by_layer.setdefault((band, layer_name), set()).update(missing)

    added = 0
    for (band, layer_name), missing in missing_by_layer.items():
        service = services.get(band)
        if service is None:
            continue
        layer = service["layers"].get(layer_name)
        if layer is None:
            continue
        print(f"Retrying {len(missing)} missing {band}/{layer_name} objects locally.", flush=True)
        rows, failures = layer_features(
            service["path"],
            int(layer["id"]),
            sorted(missing),
            service["metadata_host"],
        )
        for row in rows:
            geometry = to_geojson_geometry(row.get("geometry") or {})
            if geometry is None:
                continue
            attrs = dict(row.get("attributes") or {})
            attrs.update(
                {
                    "scale_band": band,
                    "service_layer": layer.get("name"),
                    "source": "NOAA ENC Direct",
                }
            )
            features.append({"type": "Feature", "properties": attrs, "geometry": geometry})
            if attrs.get("OBJECTID") is not None:
                present.setdefault((band, layer_name), set()).add(int(attrs["OBJECTID"]))
                added += 1

    if added:
        temporary = collection_path.with_suffix(".geojson.tmp")
        temporary.write_text(
            json.dumps(payload, separators=(",", ":"), allow_nan=False),
            encoding="utf-8",
        )
        temporary.replace(collection_path)
        print(f"Added {added} source polygons from port-local retries.", flush=True)

    states: dict[str, dict[str, Any]] = {}
    for port in port_rows:
        site_id = port["site_id"]
        state: dict[str, Any] = {}
        for band in BANDS:
            layer_state: dict[str, Any] = {}
            for layer_name in ("coverage_area", "land_area"):
                key = (site_id, band, layer_name)
                ids = expected.get(key)
                if ids is None:
                    layer_state[layer_name] = "unavailable"
                elif ids <= present.get((band, layer_name), set()):
                    layer_state[layer_name] = "complete"
                else:
                    layer_state[layer_name] = "partial"
            state[band] = {
                "coverage_layer": layer_state["coverage_area"],
                "land_layer": layer_state["land_area"],
                "coverage_query_errors": query_failures.get((site_id, band, "coverage_area"), []),
                "land_query_errors": query_failures.get((site_id, band, "land_area"), []),
                "complete": (
                    layer_state["coverage_area"] == "complete"
                    and layer_state["land_area"] == "complete"
                ),
            }
        states[site_id] = state
    return states


def build_port_maps(
    collection_path: Path,
    manifest: dict[str, Any],
    port_source_status: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build 15 km WGS84 map windows around each declared port anchor."""
    import csv

    from pyproj import Geod
    from shapely.geometry import Polygon

    from maritime_localization_ingest.enc import (
        load_enc_geojson,
        mosaic_by_scale,
        mosaic_map_v2,
        normalize_map_v2_for_target_crs,
    )
    from maritime_localization_shared.serialization import contract_to_dict

    ports_path = ROOT / "data/sites/east-coast-major-ports-top150-2024.csv"
    collection = load_enc_geojson(collection_path)
    geod = Geod(ellps="WGS84")
    port_maps: list[dict[str, Any]] = []
    with ports_path.open(newline="", encoding="utf-8") as stream:
        for port in csv.DictReader(stream):
            site_id = port["site_id"]
            incomplete_bands = {
                band
                for band, status in port_source_status[site_id].items()
                if not status["complete"]
            }
            latitude = float(port["latitude_deg"])
            longitude = float(port["longitude_deg"])
            radius_m = 15_000
            boundary = [
                geod.fwd(longitude, latitude, azimuth, radius_m)[:2] for azimuth in range(0, 360, 5)
            ]
            domain = Polygon(boundary)
            retrieval_stamp = str(manifest.get("retrieved_at_utc") or "unknown")
            map_v2 = mosaic_map_v2(
                collection,
                domain,
                map_version=f"noaa-enc-direct:{BBOX_NAME}:{retrieval_stamp}",
                incomplete_scale_bands=incomplete_bands,
            )
            validation_zone = int((longitude + 180) // 6) + 1
            validation_crs = 32600 + validation_zone
            map_v2 = normalize_map_v2_for_target_crs(map_v2, validation_crs)
            map_v2 = normalize_map_v2_for_target_crs(map_v2, validation_crs)
            map_v2 = normalize_map_v2_for_target_crs(map_v2, validation_crs)
            map_v2 = normalize_map_v2_for_target_crs(map_v2, validation_crs)
            output = CACHE / f"noaa-enc-port-{site_id}-15km.map-v2.json"
            output.write_text(
                json.dumps(contract_to_dict(map_v2), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            bands = mosaic_by_scale(collection, domain, incomplete_scale_bands=incomplete_bands)
            port_maps.append(
                {
                    "site_id": site_id,
                    "port": port["port"],
                    "center_lon_lat_deg": [longitude, latitude],
                    "domain_radius_m": radius_m,
                    "coverage_status": map_v2.coverage_status,
                    "projection_validation_crs": f"EPSG:{validation_crs}",
                    "scale_source_status": port_source_status[site_id],
                    "incomplete_source_scales_excluded": sorted(incomplete_bands),
                    "effective_scale_bands": [layer.scale_band for layer in bands],
                    "map_path": str(output),
                    "map_sha256": sha256(output),
                }
            )
            print(
                f"{site_id} {port['port']}: {map_v2.coverage_status}, "
                f"scales={','.join(layer.scale_band for layer in bands) or 'none'}",
                flush=True,
            )
    return port_maps


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="replace the cached acquisition")
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    geojson_path = CACHE / "noaa-enc-east-coast-24-ports.geojson"
    if geojson_path.exists() and MANIFEST.exists() and not args.refresh:
        print(f"Using cached ENC collection: {geojson_path}")
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        services, _ = service_info()
        port_source_status = verify_and_complete_port_sources(geojson_path, services)
        manifest["port_source_status"] = port_source_status
        manifest["collection_sha256"] = sha256(geojson_path)
        manifest["collection_bytes"] = geojson_path.stat().st_size
        manifest["feature_count"] = len(
            json.loads(geojson_path.read_text(encoding="utf-8"))["features"]
        )
        manifest["port_maps"] = build_port_maps(geojson_path, manifest, port_source_status)
        manifest["service_metadata"] = {
            band: {
                "service_path": service["path"],
                "service_host": service["metadata_host"],
                "service_description": service["service_description"],
                "full_extent": service["full_extent"],
            }
            for band, service in services.items()
            if service is not None
        }
        aids_manifest_path = ROOT / "data/chart-aids/eastern-seaboard-enc-aids.manifest.json"
        aids_manifest = json.loads(aids_manifest_path.read_text(encoding="utf-8"))
        aids_path = ROOT / "data/chart-aids/eastern-seaboard-enc-aids.csv"
        manifest["legacy_aids_catalog"] = {
            "file": str(aids_path),
            "sha256": sha256(aids_path),
            "retrieved_utc": aids_manifest.get("retrieved_utc"),
            "source_manifest": str(aids_manifest_path),
            "cross_scale_rows_retained": True,
            "missing_source_attributes_are_not_inferred": True,
        }
        manifest["aids_catalog_v2"] = export_aid_catalog_v2()
        MANIFEST.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0

    deadline = time.monotonic() + MAX_WALL_SECONDS
    services, issues = service_info()
    features: list[dict[str, Any]] = []
    layer_manifest: dict[str, Any] = {}
    for band, service in services.items():
        if service is None:
            continue
        path = service["path"]
        for layer_name, layer in service["layers"].items():
            if time.monotonic() > deadline:
                issues.append({"stage": "deadline", "detail": f"stopped at {MAX_WALL_SECONDS}s"})
                break
            layer_id = int(layer["id"])
            preferred_host = service["metadata_host"]
            ids, failures = layer_ids(path, layer_id, preferred_host)
            if ids is None:
                issues.extend(
                    {"band": band, "layer": layer_name, "stage": "object_ids", "detail": item}
                    for item in failures
                )
                layer_manifest[f"{band}/{layer_name}"] = {"status": "unavailable"}
                continue
            rows, failures = layer_features(path, layer_id, ids, preferred_host)
            accepted = 0
            for row in rows:
                geometry = to_geojson_geometry(row.get("geometry") or {})
                if geometry is None:
                    continue
                attrs = dict(row.get("attributes") or {})
                attrs.update(
                    {
                        "scale_band": band,
                        "service_layer": layer.get("name"),
                        "source": "NOAA ENC Direct",
                    }
                )
                features.append({"type": "Feature", "properties": attrs, "geometry": geometry})
                accepted += 1
            if accepted < len(ids):
                failures.append(f"decoded {accepted}/{len(ids)} requested feature geometries")
            layer_manifest[f"{band}/{layer_name}"] = {
                "status": "partial" if failures else "complete",
                "service_path": path,
                "service_host": service["metadata_host"],
                "layer_id": layer_id,
                "layer_name": layer.get("name"),
                "description": layer.get("description"),
                "display_field": layer.get("displayField"),
                "fields": layer.get("fields"),
                "object_ids_returned": len(ids),
                "features_decoded": accepted,
            }
            issues.extend(
                {"band": band, "layer": layer_name, "stage": "feature_query", "detail": item}
                for item in failures
            )
            print(f"{band}/{layer_name}: {accepted}/{len(ids)} polygons", flush=True)

    payload = {
        "type": "FeatureCollection",
        "name": BBOX_NAME,
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": features,
    }
    temporary = geojson_path.with_suffix(".geojson.tmp")
    temporary.write_text(
        json.dumps(payload, separators=(",", ":"), allow_nan=False), encoding="utf-8"
    )
    temporary.replace(geojson_path)
    manifest = {
        "schema_version": 1,
        "status": "complete" if not issues else "partial",
        "source": "NOAA ENC Direct",
        "source_product": "NOAA ENC Direct weekly scale-specific ArcGIS MapServer",
        "retrieved_at_utc": datetime.now(UTC).isoformat(),
        "domain_name": BBOX_NAME,
        "domain_bbox_wgs84": list(BBOX),
        "scale_precedence": ["berthing", "harbour", "approach", "coastal", "general", "overview"],
        "unknown_policy": "No returned coverage feature means unknown; never infer water.",
        "collection_path": str(geojson_path),
        "collection_sha256": sha256(geojson_path),
        "collection_bytes": geojson_path.stat().st_size,
        "feature_count": len(features),
        "service_metadata": {
            band: {
                "service_path": service["path"],
                "service_host": service["metadata_host"],
                "service_description": service["service_description"],
                "full_extent": service["full_extent"],
            }
            for band, service in services.items()
            if service is not None
        },
        "scale_layers": layer_manifest,
        "issues": issues,
    }
    port_source_status = verify_and_complete_port_sources(geojson_path, services)
    manifest["port_source_status"] = port_source_status
    manifest["collection_sha256"] = sha256(geojson_path)
    manifest["collection_bytes"] = geojson_path.stat().st_size
    manifest["feature_count"] = len(
        json.loads(geojson_path.read_text(encoding="utf-8"))["features"]
    )
    manifest["port_maps"] = build_port_maps(geojson_path, manifest, port_source_status)
    manifest["aids_catalog_v2"] = export_aid_catalog_v2()
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Saved {len(features)} features to {geojson_path}")
    print(f"Manifest: {MANIFEST}")
    print(f"Acquisition status: {manifest['status']}; issues={len(issues)}")
    return 0 if features else 2


if __name__ == "__main__":
    raise SystemExit(main())
