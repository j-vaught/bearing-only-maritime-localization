from __future__ import annotations

import gzip
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from pyproj import Transformer
from shapely import transform as shapely_transform
from shapely.geometry import GeometryCollection, box, shape
from shapely.ops import unary_union

from maritime.importers.enc import (
    ENCCollection,
    ENCFeature,
    _polygonal_only,
    _valid_polygon,
    compose_scale_fallback_map_v2,
    mosaic_map_v2,
)
from maritime.schemas.inputs_v2 import MapV2
from maritime.serialization import contract_to_dict

ROOT = Path(__file__).resolve().parents[1]
DOMAIN_BOUNDS = (-82.0, 24.5, -66.0, 45.5)
DOMAIN = box(*DOMAIN_BOUNDS)
SOURCE_DIR = ROOT / ".agent-work" / "cache" / "maps" / "east-coast-fullscale"
SCALE_BANDS = ("berthing", "harbour", "approach")
RESOLVE_SAME_SCALE_CONFLICTS = False


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def area_km2(geometry) -> float:
    project = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True)
    projected = shapely_transform(geometry, project.transform, interleaved=False)
    return float(projected.area) / 1_000_000.0


def load_scale_maps(band: str) -> list[MapV2]:
    archive_path = SOURCE_DIR / f"{band}.geojson.gz"
    manifest_path = SOURCE_DIR / f"{band}.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    archive = archive_path.read_bytes()
    if not manifest.get("complete") or sha256(archive) != manifest.get("sha256"):
        raise ValueError(f"{band} source archive is incomplete or has a checksum mismatch.")
    payload = json.loads(gzip.decompress(archive))
    grouped: dict[str, list[ENCFeature]] = defaultdict(list)
    coverage_shapes: dict[str, list] = defaultdict(list)
    for raw in payload["features"]:
        props = dict(raw.get("properties") or {})
        chart_id = str(props.get("DSNM") or "")
        if not chart_id:
            raise ValueError(f"{band} source feature has no NOAA chart-cell identity.")
        layer = str(props.get("service_layer") or "")
        object_id = props.get("OBJECTID", raw.get("id"))
        if object_id is None:
            raise ValueError(f"{band} source feature has no NOAA object identity.")
        props["OBJECTID"] = object_id
        geometry = _valid_polygon(
            shape(raw.get("geometry") or {}), f"{band}:{chart_id}:{props.get('OBJECTID')}"
        )
        if layer.endswith("Coverage_area"):
            coverage_shapes[chart_id].append(geometry)
        feature_id = f"{chart_id}:{layer}:{object_id}"
        grouped[chart_id].append(
            ENCFeature(
                feature_id=feature_id,
                chart_id=chart_id,
                scale_band=band,
                layer=layer,
                geometry=geometry,
                attributes=tuple(sorted(props.items())),
                source="NOAA ENC Direct",
                epoch=str(props["SORDAT"]) if props.get("SORDAT") else None,
            )
        )
    if set(grouped) != set(coverage_shapes):
        raise ValueError(f"{band} land and coverage chart-cell identities do not match.")
    maps: list[MapV2] = []
    for chart_id, features in grouped.items():
        chart_domain = _polygonal_only(unary_union(coverage_shapes[chart_id]).intersection(DOMAIN))
        if chart_domain.is_empty:
            continue
        collection = ENCCollection(
            crs="EPSG:4326",
            features=tuple(features),
            collection_id=f"{band}:{chart_id}",
        )
        maps.append(
            mosaic_map_v2(
                collection,
                chart_domain,
                map_version=f"{band}:{chart_id}",
                densify_geographic_edges=False,
            )
        )
    print(f"{band}: composed {len(maps)} chart-cell map inputs", flush=True)
    return maps


def main() -> None:
    data_dir = ROOT / "data"
    old_map_bytes = (data_dir / "map.json.gz").read_bytes()
    old_map_doc = json.loads(gzip.decompress(old_map_bytes))
    old_map_doc.pop("contract_type", None)
    old_map = MapV2(**old_map_doc)
    detailed = {band: load_scale_maps(band) for band in SCALE_BANDS}
    map_version = (
        f"noaa-enc-east-coast-fullscale-map-v4:{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
    result, conflicts = compose_scale_fallback_map_v2(
        old_map,
        detailed,
        domain=DOMAIN,
        map_version=map_version,
        source="NOAA ENC Direct full-coast scale mosaic",
        densify_geographic_edges=False,
        resolve_same_scale_conflicts=RESOLVE_SAME_SCALE_CONFLICTS,
    )
    new_coverage = (
        shape(json.loads(result.coverage_geometry_geojson))
        if result.coverage_geometry_geojson
        else GeometryCollection()
    )
    new_unknown = (
        shape(json.loads(result.unknown_geometry_geojson))
        if result.unknown_geometry_geojson
        else _polygonal_only(DOMAIN.difference(new_coverage))
    )
    new_land = (
        shape(json.loads(result.land_geometry_geojson))
        if result.land_geometry_geojson
        else GeometryCollection()
    )
    old_coverage = (
        shape(json.loads(old_map.coverage_geometry_geojson))
        if old_map.coverage_geometry_geojson
        else GeometryCollection()
    )
    old_unknown = (
        shape(json.loads(old_map.unknown_geometry_geojson))
        if old_map.unknown_geometry_geojson
        else _polygonal_only(DOMAIN.difference(old_coverage))
    )
    old_land = (
        shape(json.loads(old_map.land_geometry_geojson))
        if old_map.land_geometry_geojson
        else GeometryCollection()
    )
    output_payload = json.dumps(
        contract_to_dict(result), separators=(",", ":"), allow_nan=False
    ).encode()
    output_archive = gzip.compress(output_payload, compresslevel=6, mtime=0)
    output_path = data_dir / "map.json.gz"
    metadata_path = data_dir / "sources.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    map_metadata = metadata["map"]
    source_manifests = {}
    for band in SCALE_BANDS:
        band_manifest = json.loads(
            (SOURCE_DIR / f"{band}.manifest.json").read_text(encoding="utf-8")
        )
        band_manifest.pop("archive_path", None)
        source_manifests[band] = band_manifest
    map_metadata.update(
        {
            "raw_filename": "map.json",
            "archive_filename": "map.json.gz",
            "raw_sha256": sha256(output_payload),
            "archive_sha256": sha256(output_archive),
            "raw_bytes": len(output_payload),
            "archive_bytes": len(output_archive),
            "source_information": {
                "map_version": map_version,
                "source": result.source,
                "license_note": map_metadata.get("source_information", {}).get("license_note"),
                "domain": {
                    "crs": "EPSG:4326",
                    "bbox_wgs84": list(DOMAIN_BOUNDS),
                    "area_method": "EPSG:6933 equal-area projection",
                    "area_km2": area_km2(DOMAIN),
                },
                "coverage": {
                    "status": result.coverage_status,
                    "known_area_km2": area_km2(new_coverage),
                    "known_fraction": area_km2(new_coverage) / area_km2(DOMAIN),
                    "unknown_area_km2": area_km2(new_unknown),
                    "unknown_fraction": area_km2(new_unknown) / area_km2(DOMAIN),
                    "unknown_is_not_inferred_as_water": True,
                },
                "scale_precedence": list(SCALE_BANDS) + ["coastal", "general", "overview"],
                "full_coast_high_scale_sources": {**source_manifests},
                "scale_conflict_area_deg2": (
                    conflicts
                    if RESOLVE_SAME_SCALE_CONFLICTS
                    else {band: None for band in SCALE_BANDS + ("coastal", "general", "overview")}
                ),
                "same_scale_conflicts_checked": RESOLVE_SAME_SCALE_CONFLICTS,
                "comparison_to_previous_map": {
                    "previous_map_version": old_map.map_version,
                    "known_area_gain_km2": area_km2(new_coverage) - area_km2(old_coverage),
                    "unknown_area_reduction_km2": area_km2(old_unknown) - area_km2(new_unknown),
                    "land_label_change_area_km2": area_km2(new_land.symmetric_difference(old_land)),
                },
                "source_info_url": "https://www.nauticalcharts.noaa.gov/learn/encdirect/",
            },
        }
    )
    metadata_payload = (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode()
    tmp_map = output_path.with_suffix(".json.gz.tmp")
    tmp_meta = metadata_path.with_suffix(".json.tmp")
    tmp_map.write_bytes(output_archive)
    tmp_meta.write_bytes(metadata_payload)
    candidate_map_doc = json.loads(gzip.decompress(tmp_map.read_bytes()))
    candidate_map_doc.pop("contract_type", None)
    MapV2(**candidate_map_doc)
    # Replacement occurs only after all source checks and final contract parsing pass.
    tmp_map.replace(output_path)
    tmp_meta.replace(metadata_path)
    print(
        json.dumps(map_metadata["source_information"]["comparison_to_previous_map"], indent=2),
        flush=True,
    )
    print(json.dumps(map_metadata["source_information"]["coverage"], indent=2), flush=True)
    print(f"updated {output_path} ({len(output_archive) / 1e6:.1f} MB)", flush=True)


if __name__ == "__main__":
    main()
