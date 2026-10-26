from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

from pyproj import Geod, Transformer
from shapely import transform as shapely_transform
from shapely.geometry import box, shape

from maritime_localization_ingest.ais import read_ais_csv
from maritime_localization_ingest.enc import (
    combine_ais_and_enc_physical_catalogs,
    compose_partial_map_v2,
    enc_aids_catalog_to_catalog_v2,
    load_enc_physical_aid_catalog,
)
from maritime_localization_shared.contracts.inputs_v2 import CatalogV2, MapV2
from maritime_localization_shared.serialization import (
    read_contract,
    write_contract,
)

ROOT = Path(__file__).resolve().parents[1]
SHARED_ROOT = (
    ROOT.parents[2]
    if ROOT.parent.name == "worktrees" and ROOT.parent.parent.name == ".agent-work"
    else next(parent for parent in (ROOT, *ROOT.parents) if (parent / ".agent-work").is_dir())
)
CACHE = SHARED_ROOT / ".agent-work" / "cache" / "maps"
SOURCE_MAP_MANIFEST = ROOT / "data" / "maps" / "accuracy-east-coast-enc.manifest.json"
OUTPUT_MANIFEST = ROOT / "data" / "maps" / "eastern-seaboard-accuracy-v2.manifest.json"
DOMAIN_BOUNDS = (-82.0, 24.5, -66.0, 45.5)
AIS_TARGET_UTC = "2023-07-15T12:00:30Z"
GEOD = Geod(ellps="WGS84")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def geodesic_area_km2(geometry) -> float:
    area_m2, _ = GEOD.geometry_area_perimeter(geometry)
    return abs(float(area_m2)) / 1_000_000.0


def equal_area_km2(geometry) -> float:
    """Measure polygon area in WGS 84 / NSIDC EASE-Grid 2.0 Global."""
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True)
    projected = shapely_transform(geometry, transformer.transform, interleaved=False)
    return float(projected.area) / 1_000_000.0


def build_coast_base_candidate() -> int:
    """Build an isolated full-coast MapV2 candidate from validated source layers."""
    import gzip
    import sys

    from shapely.geometry import GeometryCollection, box, shape
    from shapely.ops import unary_union
    from shapely.strtree import STRtree

    from maritime_localization_ingest.enc import (
        SCALE_PRIORITY,
        ENCCollection,
        _geojson_geometry,
        _polygonal_only,
        compose_scale_fallback_map_v2,
        load_enc_geojson,
        mosaic_by_scale,
    )

    base_root = (
        SHARED_ROOT
        / ".agent-work"
        / "worktrees"
        / "a08-coast-base"
        / "data"
        / "maps"
        / "coast-base"
    )
    source_manifest_path = base_root / "manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if not source_manifest.get("overall_complete"):
        raise ValueError("NOAA coast-base source retrieval is not complete.")

    source_copy_root = CACHE / "a34-coast-base-v2-source"
    source_copy_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_manifest_path, source_copy_root / "manifest.json")
    source_manifest_sha256 = sha256(source_manifest_path)
    source_layer_audit: dict[str, dict[str, object]] = {}
    complete_bands: set[str] = set()
    for band in ("coastal", "general"):
        role_archives: dict[str, dict] = {}
        band_complete = True
        for role in ("coverage_area", "land_area"):
            key = f"{band}_{role}"
            entry = source_manifest["source_layers"][key]
            archive_path = base_root / Path(entry["archive_path"]).name
            copied_path = source_copy_root / archive_path.name
            if not entry.get("complete") or not archive_path.is_file():
                band_complete = False
                source_layer_audit[key] = {
                    "complete": False,
                    "reason": "missing_or_incomplete_source_archive",
                }
                continue
            actual_sha = sha256(archive_path)
            if actual_sha != entry["archive_sha256"]:
                raise ValueError(f"Source archive SHA-256 mismatch for {key}.")
            shutil.copy2(archive_path, copied_path)
            archive = json.loads(gzip.decompress(archive_path.read_bytes()))
            requested_ids = sorted(archive.get("requested_ids", []))
            returned_ids = sorted(archive.get("returned_ids", []))
            feature_ids = []
            for feature in archive.get("features", []):
                object_id = (feature.get("attributes") or {}).get(archive["object_id_field"])
                if object_id is None:
                    raise ValueError(f"Missing source object ID in archive for {key}.")
                feature_ids.append(int(object_id))
            feature_ids.sort()
            if (
                requested_ids != returned_ids
                or returned_ids != feature_ids
                or archive.get("missing_ids")
                or archive.get("invalid_geometry_ids")
            ):
                raise ValueError(f"Source archive ID/geometry validation failed for {key}.")
            role_archives[role] = archive
            source_layer_audit[key] = {
                "complete": True,
                "feature_count": len(archive["features"]),
                "requested_id_count": len(requested_ids),
                "returned_id_count": len(returned_ids),
                "requested_id_set_sha256": hashlib.sha256(
                    json.dumps(requested_ids, separators=(",", ":")).encode()
                ).hexdigest(),
                "returned_id_set_sha256": hashlib.sha256(
                    json.dumps(returned_ids, separators=(",", ":")).encode()
                ).hexdigest(),
                "invalid_geometry_ids": 0,
                "source_archive_path": str(archive_path),
                "candidate_input_copy_path": str(copied_path),
                "sha256": actual_sha,
            }
        if not band_complete or set(role_archives) != {"coverage_area", "land_area"}:
            continue
        if (
            role_archives["coverage_area"].get("band") != band
            or role_archives["land_area"].get("band") != band
        ):
            raise ValueError(f"Unexpected band metadata in {band} source archives.")
        categories = {
            str(feature.get("attributes", {}).get("CATCOV", "")).strip().casefold()
            for feature in role_archives["coverage_area"]["features"]
        }
        if categories - {"1", "2", "coverage available", "no coverage available"}:
            raise ValueError(f"Unrecognized {band} CATCOV values: {sorted(categories)}")
        complete_bands.add(band)

    if not complete_bands:
        raise ValueError("No complete coverage-plus-land scale is available.")
    base_entry = source_manifest.get("base_map_v2", {})
    if (
        not base_entry.get("complete")
        or set(base_entry.get("complete_scale_bands", [])) != complete_bands
    ):
        raise ValueError(
            "The A08 coast-base MapV2 candidate is incomplete or has different scales."
        )
    expected_source_hashes = {
        key: audit["sha256"] for key, audit in source_layer_audit.items() if audit.get("complete")
    }
    if expected_source_hashes != base_entry.get("source_layer_archive_hashes"):
        raise ValueError(
            "The coarse MapV2 candidate does not bind to the verified raw layer hashes."
        )
    base_map_source_path = base_root / Path(base_entry["path"]).name
    if sha256(base_map_source_path) != base_entry["sha256"]:
        raise ValueError("The A08 coast-base MapV2 candidate SHA-256 does not match its manifest.")
    base_map_candidate_copy = source_copy_root / base_map_source_path.name
    shutil.copy2(base_map_source_path, base_map_candidate_copy)
    base_map = MapV2(**json.loads(gzip.decompress(base_map_source_path.read_bytes())))
    domain = box(*DOMAIN_BOUNDS)
    source_epoch = source_manifest.get("retrieval_completed_utc", "unknown")
    if base_map.coverage_status == "unknown":
        raise ValueError("Validated source layers produced no known base coverage.")

    source_map_manifest = json.loads(SOURCE_MAP_MANIFEST.read_text(encoding="utf-8"))
    if source_map_manifest.get("status") != "partial":
        raise ValueError("The 24-port source maps must retain their original partial status.")
    enc_path = Path(source_map_manifest["collection_path"])
    if not enc_path.is_file() or sha256(enc_path) != source_map_manifest["collection_sha256"]:
        raise ValueError("The 24-port ENC collection is missing or has a SHA-256 mismatch.")
    enc_collection = load_enc_geojson(enc_path)
    enc_geometries = [feature.geometry for feature in enc_collection.features]
    enc_tree = STRtree(enc_geometries)
    detailed_maps_by_scale: dict[str, list[MapV2]] = {band: [] for band in SCALE_PRIORITY[:3]}
    site_audit: list[dict[str, object]] = []
    total_intersections = 0
    for item in source_map_manifest["port_maps"]:
        map_path = Path(item["map_path"])
        if not map_path.is_file() or sha256(map_path) != item["map_sha256"]:
            raise ValueError(f"Port map missing or hash mismatch: {map_path}")
        site_map = read_contract(map_path, MapV2)
        if site_map.coverage_geometry_geojson is None:
            site_audit.append({"site_id": item["site_id"], "status": "no_coverage_geometry"})
            continue
        site_coverage = shape(json.loads(site_map.coverage_geometry_geojson))
        site_unknown = (
            shape(json.loads(site_map.unknown_geometry_geojson))
            if site_map.unknown_geometry_geojson is not None
            else GeometryCollection()
        )
        site_domain = _polygonal_only(unary_union([site_coverage, site_unknown]))
        if site_domain.is_empty:
            raise ValueError(f"Empty source domain for port {item['site_id']}.")
        local_feature_indexes = sorted(
            int(index) for index in enc_tree.query(site_domain, predicate="intersects")
        )
        local_features = tuple(enc_collection.features[index] for index in local_feature_indexes)
        total_intersections += len(local_features)
        local_detail_features = tuple(
            feature for feature in local_features if feature.scale_band in SCALE_PRIORITY[:3]
        )
        local_collection = ENCCollection(
            crs=enc_collection.crs,
            features=local_detail_features,
            collection_id=f"{enc_collection.collection_id}:{item['site_id']}:detail",
            source=enc_collection.source,
        )
        incomplete = {
            band
            for band, band_status in item["scale_source_status"].items()
            if not band_status.get("complete")
        }
        layers = mosaic_by_scale(
            local_collection,
            site_domain,
            incomplete_scale_bands=incomplete,
        )
        layer_audit: list[dict[str, object]] = []
        for layer in layers:
            if layer.scale_band not in detailed_maps_by_scale:
                continue
            map_value = MapV2(
                crs=enc_collection.crs,
                source=f"NOAA ENC Direct port scale layer {layer.scale_band}",
                map_version=str(source_map_manifest.get("retrieved_at_utc", "unknown")),
                coverage_status="known",
                coverage_geometry_geojson=_geojson_geometry(layer.coverage),
                unknown_geometry_geojson=None,
                land_geometry_geojson=(
                    _geojson_geometry(layer.land, densify_geographic_edges=False)
                    if not layer.land.is_empty
                    else None
                ),
            )
            detailed_maps_by_scale[layer.scale_band].append(map_value)
            layer_audit.append(
                {
                    "scale_band": layer.scale_band,
                    "effective_coverage_km2": equal_area_km2(layer.coverage),
                    "land_km2": equal_area_km2(layer.land),
                    "source_feature_count": len(layer.feature_ids),
                }
            )
        site_audit.append(
            {
                "site_id": item["site_id"],
                "port": item["port"],
                "source_coverage_status": item["coverage_status"],
                "port_map_sha256": item["map_sha256"],
                "effective_source_scales": item["effective_scale_bands"],
                "incomplete_scales": sorted(incomplete),
                "local_enc_feature_count": len(local_features),
                "scale_layers": layer_audit,
            }
        )
        print(
            f"Composed {item['site_id']} from {len(local_detail_features)} detailed features.",
            file=sys.stderr,
            flush=True,
        )

    candidate, conflicts_deg2 = compose_scale_fallback_map_v2(
        base_map,
        detailed_maps_by_scale,
        domain=domain,
        map_version=f"noaa-enc-east-coast-fullcoast-map-v3-candidate:{source_epoch}",
        source="NOAA ENC Direct General/Coastal fallback with 24 verified port-scale overlays",
        densify_geographic_edges=False,
        geographic_segment_length_deg=0.001,
    )
    if candidate.coverage_geometry_geojson is None or candidate.unknown_geometry_geojson is None:
        raise ValueError("Candidate map must carry explicit domain coverage and unknown geometry.")
    known = _polygonal_only(shape(json.loads(candidate.coverage_geometry_geojson)))
    unknown = _polygonal_only(shape(json.loads(candidate.unknown_geometry_geojson)))
    land = (
        _polygonal_only(shape(json.loads(candidate.land_geometry_geojson)))
        if candidate.land_geometry_geojson is not None
        else GeometryCollection()
    )
    if not known.is_valid or not unknown.is_valid or not land.is_valid:
        raise ValueError("Candidate output contains invalid polygon topology.")
    if known.intersection(unknown).area > 1e-10 or land.difference(known).area > 1e-10:
        raise ValueError("Candidate coverage, unknown, and land topologies overlap inconsistently.")

    def projection_chord_check(geometry):
        import numpy as np
        from shapely import segmentize

        sampled_geometry = segmentize(geometry, max_segment_length=0.001)
        maximum_error_m = 0.0
        maximum_segment_m = 0.0
        stack = [sampled_geometry]
        while stack:
            part = stack.pop()
            if part.geom_type == "Polygon":
                for ring in [part.exterior, *part.interiors]:
                    coordinates = np.asarray(ring.coords, dtype=np.float64)
                    first = coordinates[:-1]
                    last = coordinates[1:]
                    if not len(first):
                        continue
                    x0, y0 = projection_check.transform(first[:, 0], first[:, 1])
                    x1, y1 = projection_check.transform(last[:, 0], last[:, 1])
                    x0, y0, x1, y1 = map(np.asarray, (x0, y0, x1, y1))
                    lengths = np.hypot(x1 - x0, y1 - y0)
                    maximum_segment_m = max(maximum_segment_m, float(lengths.max(initial=0)))
                    for fraction in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
                        sample_x = first[:, 0] + fraction * (last[:, 0] - first[:, 0])
                        sample_y = first[:, 1] + fraction * (last[:, 1] - first[:, 1])
                        projected_x, projected_y = projection_check.transform(sample_x, sample_y)
                        deviation = np.hypot(
                            np.asarray(projected_x) - (x0 + fraction * (x1 - x0)),
                            np.asarray(projected_y) - (y0 + fraction * (y1 - y0)),
                        )
                        maximum_error_m = max(maximum_error_m, float(deviation.max(initial=0)))
            elif hasattr(part, "geoms"):
                stack.extend(part.geoms)
        return {
            "max_sampled_chord_deviation_m": maximum_error_m,
            "max_projected_segment_length_m": maximum_segment_m,
        }

    projection_check = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True)
    projection_accuracy = {
        "method": (
            "Sample nine interior points per segment and measure projected distance to its "
            "endpoint chord after segmentizing source lon/lat edges. This is a sampled "
            "approximation check, not a formal bound."
        ),
        "crs": "EPSG:32618",
        "segment_length_degrees": 0.001,
        "by_geometry": {
            "coverage": projection_chord_check(known),
            "unknown": projection_chord_check(unknown),
            "land": projection_chord_check(land),
        },
    }

    candidate_path = CACHE / "noaa-enc-east-coast-fullcoast-map-v3-candidate.json"
    write_contract(candidate_path, candidate)
    base_coverage = (
        _polygonal_only(shape(json.loads(base_map.coverage_geometry_geojson)))
        if base_map.coverage_geometry_geojson is not None
        else GeometryCollection()
    )
    base_unknown = (
        _polygonal_only(shape(json.loads(base_map.unknown_geometry_geojson)))
        if base_map.unknown_geometry_geojson is not None
        else _polygonal_only(domain.difference(base_coverage))
    )
    base_land = (
        _polygonal_only(shape(json.loads(base_map.land_geometry_geojson)))
        if base_map.land_geometry_geojson is not None
        else GeometryCollection()
    )
    added_land = _polygonal_only(land.difference(base_land))
    base_land_removed = _polygonal_only(base_land.difference(land))
    base_metrics = {
        "path": str(base_map_source_path),
        "sha256": base_entry["sha256"],
        "coverage_status": base_map.coverage_status,
        "known_area_km2": equal_area_km2(base_coverage),
        "unknown_area_km2": equal_area_km2(base_unknown),
        "land_area_km2": equal_area_km2(base_land),
    }
    baseline_path = CACHE / "noaa-enc-east-coast-fullcoast-map-v2.json"
    baseline_metrics: dict[str, float | str] = {}
    if baseline_path.is_file():
        baseline = read_contract(baseline_path, MapV2)
        baseline_coverage = (
            _polygonal_only(shape(json.loads(baseline.coverage_geometry_geojson)))
            if baseline.coverage_geometry_geojson is not None
            else GeometryCollection()
        )
        baseline_unknown = (
            _polygonal_only(shape(json.loads(baseline.unknown_geometry_geojson)))
            if baseline.unknown_geometry_geojson is not None
            else _polygonal_only(domain.difference(baseline_coverage))
        )
        baseline_metrics = {
            "path": str(baseline_path),
            "coverage_status": baseline.coverage_status,
            "known_km2": equal_area_km2(baseline_coverage),
            "unknown_km2": equal_area_km2(baseline_unknown),
        }

    detailed_coverage_by_scale: dict[str, object] = {}
    detailed_coverages: list = []
    for band in SCALE_PRIORITY[:3]:
        band_parts = []
        for source_map in detailed_maps_by_scale[band]:
            if source_map.coverage_geometry_geojson is None:
                continue
            source_coverage = shape(json.loads(source_map.coverage_geometry_geojson))
            if source_map.unknown_geometry_geojson is not None:
                source_coverage = source_coverage.difference(
                    shape(json.loads(source_map.unknown_geometry_geojson))
                )
            band_parts.append(_polygonal_only(source_coverage.intersection(domain)))
        band_union = _polygonal_only(
            unary_union(band_parts) if band_parts else GeometryCollection()
        )
        detailed_coverage_by_scale[band] = {
            "union_area_km2_before_cross_scale_precedence": equal_area_km2(band_union),
            "source_map_count": len(detailed_maps_by_scale[band]),
        }
        if not band_union.is_empty:
            detailed_coverages.append(band_union)
    detailed_union = _polygonal_only(
        unary_union(detailed_coverages) if detailed_coverages else GeometryCollection()
    )
    coarse_coverage = (
        _polygonal_only(shape(json.loads(base_map.coverage_geometry_geojson)))
        if base_map.coverage_geometry_geojson is not None
        else GeometryCollection()
    )
    detailed_inside_coarse = _polygonal_only(detailed_union.intersection(coarse_coverage))
    detailed_outside_coarse = _polygonal_only(detailed_union.difference(coarse_coverage))

    manifest = {
        "schema_version": 1,
        "candidate_only": True,
        "activation_status": "awaiting_root_review",
        "candidate_map_path": str(candidate_path),
        "candidate_map_sha256": sha256(candidate_path),
        "candidate_map_version": candidate.map_version,
        "domain_bbox_wgs84": list(DOMAIN_BOUNDS),
        "crs": candidate.crs,
        "source": "NOAA ENC Direct",
        "source_retrieval_completed_utc": source_epoch,
        "source_retrieval_started_utc": source_manifest.get("retrieval_started_utc"),
        "coast_base_manifest_path": str(source_manifest_path),
        "coast_base_manifest_sha256": source_manifest_sha256,
        "coast_base_manifest_overall_complete": source_manifest["overall_complete"],
        "coast_base_map_v2_generator_status": source_manifest.get("base_map_v2"),
        "coast_base_map_v2_candidate_path": str(base_map_source_path),
        "coast_base_map_v2_candidate_sha256": base_entry["sha256"],
        "coast_base_map_v2_candidate_copy_path": str(base_map_candidate_copy),
        "coast_base_source_layers": source_layer_audit,
        "complete_base_bands_used": [
            band for band in ("coastal", "general") if band in complete_bands
        ],
        "coast_base_feature_count": sum(
            int(feature_count)
            for layer in source_layer_audit.values()
            if layer.get("complete")
            and isinstance(feature_count := layer.get("feature_count"), int)
        ),
        "repair_policy": (
            "The coarse base uses the validated A08 MapV2 candidate tied to all raw layer "
            "hashes. Port scale mosaics retain polygon components after clipping and overlay. "
            "Raw source features remain lossless in the archived inputs."
        ),
        "port_source_manifest_path": str(SOURCE_MAP_MANIFEST),
        "port_source_manifest_sha256": sha256(SOURCE_MAP_MANIFEST),
        "port_collection_path": str(enc_path),
        "port_collection_sha256": source_map_manifest["collection_sha256"],
        "port_maps_verified": len(site_audit),
        "port_map_source_bytes_unchanged": True,
        "port_local_feature_intersections": total_intersections,
        "port_scale_precedence": list(SCALE_PRIORITY),
        "port_scale_union_area_by_band_km2_before_cross_scale_precedence": (
            detailed_coverage_by_scale
        ),
        "port_scale_union_area_km2": equal_area_km2(detailed_union),
        "port_scale_union_inside_coarse_base_km2": equal_area_km2(detailed_inside_coarse),
        "port_scale_union_outside_coarse_base_km2": equal_area_km2(detailed_outside_coarse),
        "same_scale_land_water_conflicts_deg2": conflicts_deg2,
        "site_scale_audit": site_audit,
        "map_coverage_status": candidate.coverage_status,
        "area_method": "EPSG:6933 equal-area projection of EPSG:4326 geometry",
        "geometry_segmentization": projection_accuracy,
        "domain_area_km2": equal_area_km2(domain),
        "known_area_km2": equal_area_km2(known),
        "unknown_area_km2": equal_area_km2(unknown),
        "land_area_km2": equal_area_km2(land),
        "fine_map_added_land_area_km2": equal_area_km2(added_land),
        "fine_map_removed_coarse_land_area_km2": equal_area_km2(base_land_removed),
        "fine_map_land_label_change_area_km2": equal_area_km2(
            unary_union([added_land, base_land_removed])
        ),
        "known_fraction": equal_area_km2(known) / equal_area_km2(domain),
        "unknown_fraction": equal_area_km2(unknown) / equal_area_km2(domain),
        "coarse_base_map": base_metrics,
        "known_area_gain_vs_base_km2": equal_area_km2(known)
        - float(base_metrics["known_area_km2"]),
        "unknown_area_reduction_vs_base_km2": float(base_metrics["unknown_area_km2"])
        - equal_area_km2(unknown),
        "known_fraction_gain_vs_base_percentage_points": 100.0
        * (equal_area_km2(known) - float(base_metrics["known_area_km2"]))
        / equal_area_km2(domain),
        "baseline_map": baseline_metrics,
        "provenance_limitations": [
            (
                "The coarse base uses complete General and Coastal coverage and land layers. "
                "Water is not inferred outside those coverage polygons."
            ),
            (
                "Complete Berthing, Harbour, and Approach layers may override the base. "
                "Coastal and General port layers do not override equal-scale base data."
            ),
            (
                "Conflicting same-scale port land/water labels stay unknown. Source charts "
                "may differ by epoch and object generalization."
            ),
            (
                "The detailed Harbour coverage union is wholly inside coarse known coverage. "
                "The reported 0.044 km2 known-area delta versus the independently serialized "
                "coarse base is a boundary representation residual, not added source coverage."
            ),
            (
                "This is a chart-derived geometry candidate. It does not model navigable "
                "depth, tides, or real-time shoreline conditions."
            ),
        ],
    }
    manifest_path = CACHE / "noaa-enc-east-coast-fullcoast-map-v3-candidate.manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "candidate_map_path": str(candidate_path),
                "manifest_path": str(manifest_path),
                "manifest": manifest,
            },
            indent=2,
        )
    )
    return 0


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--coast-base-candidate", action="store_true")
    args = parser.parse_args()
    if args.coast_base_candidate:
        return build_coast_base_candidate()
    free_bytes = shutil.disk_usage(SHARED_ROOT).free
    if free_bytes < 40 * 1024**3:
        raise RuntimeError(f"Refusing new artifacts with only {free_bytes / 1024**3:.1f} GiB free.")
    CACHE.mkdir(parents=True, exist_ok=True)
    map_source_manifest = json.loads(SOURCE_MAP_MANIFEST.read_text(encoding="utf-8"))
    if map_source_manifest.get("status") != "partial":
        raise ValueError("The source ENC collection must retain its partial status.")

    input_maps: list[MapV2] = []
    map_inputs = []
    for item in map_source_manifest["port_maps"]:
        path = Path(item["map_path"])
        if not path.is_file() or sha256(path) != item["map_sha256"]:
            raise ValueError(f"Port map missing or hash mismatch: {path}")
        map_value = read_contract(path, MapV2)
        input_maps.append(map_value)
        map_inputs.append(
            {
                "site_id": item["site_id"],
                "coverage_status": item["coverage_status"],
                "path": str(path),
                "sha256": item["map_sha256"],
            }
        )

    map_version = "noaa-enc-east-coast-partial-mosaic:" + str(
        map_source_manifest.get("retrieved_at_utc", "source-epoch-unknown")
    )
    fullcoast_map = compose_partial_map_v2(
        input_maps,
        domain=box(*DOMAIN_BOUNDS),
        map_version=map_version,
    )
    map_path = CACHE / "noaa-enc-east-coast-fullcoast-map-v2.json"
    write_contract(map_path, fullcoast_map)
    if (
        fullcoast_map.coverage_geometry_geojson is None
        or fullcoast_map.unknown_geometry_geojson is None
    ):
        raise ValueError("The full-domain map must have explicit coverage and unknown geometry.")
    coverage = shape(json.loads(fullcoast_map.coverage_geometry_geojson))
    unknown = shape(json.loads(fullcoast_map.unknown_geometry_geojson))
    domain = box(*DOMAIN_BOUNDS)

    ais_path = ROOT / "data" / "ais" / "eastern-seaboard_2023-07-15T1200Z.csv"
    ais_manifest_path = ais_path.with_suffix(".manifest.json")
    ais_catalog = read_ais_csv(ais_path, target_time_utc=AIS_TARGET_UTC)
    aid_path = ROOT / "data" / "chart-aids" / "eastern-seaboard-enc-aids.csv"
    aid_manifest_path = aid_path.with_suffix(".manifest.json")
    aid_catalog = load_enc_physical_aid_catalog(aid_path)
    raw_aid_catalog = enc_aids_catalog_to_catalog_v2(aid_catalog)
    raw_aid_path = CACHE / "noaa-enc-east-coast-aids-catalog-row-identity-v2.json"
    if raw_aid_path.exists():
        saved_raw_aids = read_contract(raw_aid_path, CatalogV2)
        if saved_raw_aids != raw_aid_catalog:
            raise ValueError("Cached lossless aid catalog differs from current source conversion.")
        raw_catalog_hash = sha256(raw_aid_path)
    else:
        write_contract(raw_aid_path, raw_aid_catalog)
        raw_catalog_hash = sha256(raw_aid_path)

    combined = combine_ais_and_enc_physical_catalogs(ais_catalog, aid_catalog)
    catalog_path = CACHE / "noaa-enc-east-coast-fullcoast-catalog-v2.json"
    write_contract(catalog_path, combined)

    target = datetime.fromisoformat(AIS_TARGET_UTC.replace("Z", "+00:00"))
    ais_ages_s = [
        abs(
            (
                datetime.fromisoformat(record.report_time_utc.replace("Z", "+00:00")) - target
            ).total_seconds()
        )
        for record in ais_catalog.records
        if record.report_time_utc is not None
    ]
    motion_status = Counter(
        dict(record.metadata).get("motion_status") for record in ais_catalog.records
    )
    quality_counts = Counter(str(record.quality) for record in ais_catalog.records)
    runtime_aid_records = [
        record
        for record in combined.records
        if dict(record.metadata).get("identity_resolution") is not None
    ]
    aid_runtime_count = len(runtime_aid_records)
    aid_resolved_runtime_count = sum(
        dict(record.metadata).get("identity_ambiguous") is False for record in runtime_aid_records
    )
    aid_unresolved_reason_counts = Counter(
        dict(record.metadata).get("identity_resolution_reason", "unspecified")
        for record in runtime_aid_records
        if dict(record.metadata).get("identity_ambiguous") is True
    )
    aid_source_identity_counts = Counter(
        str(dict(record.metadata).get("source_record_identity_status", "unspecified"))
        for record in runtime_aid_records
    )
    aid_physical_linkage_counts = Counter(
        str(dict(record.metadata).get("physical_linkage_status", "unspecified"))
        for record in runtime_aid_records
    )
    aid_merged_group_count = sum(
        len(aid.feature_ids) > 1 and not aid.identity_ambiguous and bool(aid.fullname)
        for aid in aid_catalog.physical_aids
    )
    aid_fid_rows = [
        aid
        for aid in aid_catalog.features
        if any(key.endswith(".fid") and value not in (None, "") for key, value in aid.attributes)
    ]
    aid_unnamed_fid_rows = [
        aid
        for aid in aid_fid_rows
        if not any(
            key in {"fullname", "objnam", "name"} and value not in (None, "")
            for key, value in aid.attributes
        )
    ]
    type_counts = Counter(record.normalized_type or "untyped" for record in combined.records)
    port_status_counts = Counter(item["coverage_status"] for item in map_inputs)
    map_manifest = {
        "schema_version": 1,
        "domain_name": map_source_manifest["domain_name"],
        "domain_bbox_wgs84": list(DOMAIN_BOUNDS),
        "coverage_status": fullcoast_map.coverage_status,
        "unknown_policy": (
            "Only source-reported MapV2 coverage is eligible; every domain point outside "
            "the surviving union, including ENC seams and out-of-port coast, is explicit unknown."
        ),
        "known_coverage_area_km2_geodesic": geodesic_area_km2(coverage),
        "explicit_unknown_area_km2_geodesic": geodesic_area_km2(unknown),
        "domain_area_km2_geodesic": geodesic_area_km2(domain),
        "known_coverage_fraction": coverage.area / domain.area,
        "source_manifest": {
            "path": str(SOURCE_MAP_MANIFEST),
            "sha256": sha256(SOURCE_MAP_MANIFEST),
            "source_collection_path": map_source_manifest["collection_path"],
            "source_collection_sha256": map_source_manifest["collection_sha256"],
            "source_collection_status": map_source_manifest["status"],
            "source_feature_count": map_source_manifest["feature_count"],
            "source_issue_count": len(map_source_manifest["issues"]),
            "port_map_count": len(map_inputs),
            "port_coverage_status_counts": dict(sorted(port_status_counts.items())),
            "ports": map_inputs,
        },
        "output": {
            "path": str(map_path),
            "sha256": sha256(map_path),
            "bytes": map_path.stat().st_size,
            "map_version": fullcoast_map.map_version,
            "crs": fullcoast_map.crs,
        },
        "limitations": [
            "The source ENC collection is partial and contains 314 acquisition issues.",
            "The 24 port map domains do not form complete coast coverage.",
            "Unknown takes precedence where a source map reports unknown overlap.",
            (
                "The five partial port maps contribute only validated reported coverage; "
                "their no-data seams remain unknown."
            ),
            (
                "This MapV2 is an Eastern Seaboard coverage mosaic, not a complete navigational "
                "chart or a claim of complete coastline coverage."
            ),
        ],
    }

    output_manifest = {
        "schema_version": 1,
        "catalog": {
            "path": str(catalog_path),
            "sha256": sha256(catalog_path),
            "bytes": catalog_path.stat().st_size,
            "catalog_id": combined.catalog_id,
            "catalog_version": combined.catalog_version,
            "catalog_timestamp_utc": combined.timestamp_utc,
            "record_count": len(combined.records),
            "record_count_by_normalized_type": dict(sorted(type_counts.items())),
            "ais": {
                "source_path": str(ais_path),
                "source_sha256": sha256(ais_path),
                "source_manifest_path": str(ais_manifest_path),
                "source_manifest_sha256": sha256(ais_manifest_path),
                "source_snapshot_window_utc": [
                    json.loads(ais_manifest_path.read_text(encoding="utf-8"))["time_start_utc"],
                    json.loads(ais_manifest_path.read_text(encoding="utf-8"))[
                        "time_end_utc_exclusive"
                    ],
                ],
                "reference_timestamp_utc": ais_catalog.timestamp_utc,
                "record_count": len(ais_catalog.records),
                "mmsi_present_in_source_export": False,
                "motion_status_counts": dict(sorted(motion_status.items())),
                "quality_counts": dict(sorted(quality_counts.items())),
                "absolute_report_age_seconds": {
                    "minimum": min(ais_ages_s),
                    "median": sorted(ais_ages_s)[len(ais_ages_s) // 2],
                    "maximum": max(ais_ages_s),
                },
                "source_key_identity_preserved": True,
                "stale_reports_are_not_retimed_or_extrapolated": True,
            },
            "physical_aids": {
                "source_path": str(aid_path),
                "source_sha256": sha256(aid_path),
                "source_manifest_path": str(aid_manifest_path),
                "source_manifest_sha256": sha256(aid_manifest_path),
                "source_retrieved_utc": aid_catalog.retrieved_utc,
                "source_raw_component_count": len(aid_catalog.features),
                "qualified_fid_source_row_count": len(aid_fid_rows),
                "unnamed_qualified_fid_source_row_count": len(aid_unnamed_fid_rows),
                "qualified_fid_source_keys_are_unique": len(
                    {aid.feature_id for aid in aid_fid_rows}
                )
                == len(aid_fid_rows),
                "lossless_raw_catalog_path": str(raw_aid_path),
                "lossless_raw_catalog_sha256": raw_catalog_hash,
                "lossless_raw_catalog_record_count": len(raw_aid_catalog.records),
                "physical_group_count": len(aid_catalog.physical_aids),
                "runtime_physical_record_count": aid_runtime_count,
                "runtime_resolved_physical_entity_count": aid_resolved_runtime_count,
                "runtime_unresolved_component_count": (
                    aid_runtime_count - aid_resolved_runtime_count
                ),
                "runtime_unresolved_reason_counts": dict(
                    sorted(aid_unresolved_reason_counts.items())
                ),
                "runtime_source_record_identity_status_counts": dict(
                    sorted(aid_source_identity_counts.items())
                ),
                "runtime_physical_linkage_status_counts": dict(
                    sorted(aid_physical_linkage_counts.items())
                ),
                "runtime_unresolved_records_remain_geometry_candidates": True,
                "observation_association_is_not_inferred_from_physical_linkage": True,
                "support_counts_only_resolved_representative_after_angular_merge": True,
                "physical_linkage_heuristic": (
                    "same-chart normalized-name complementary-component grouping within 20 m; "
                    "heuristic and not externally validated"
                ),
                "raw_component_count_preserved_in_runtime_or_resolved_provenance": (
                    len(aid_catalog.features)
                ),
                "merged_component_groups": aid_merged_group_count,
                "unresolved_components_are_retained_for_search": True,
                "unresolved_components_are_not_proven_distinct_support": True,
                "source_epochs_retained_per_component": True,
                "raw_feature_ids_and_attributes_retained": True,
            },
        },
        "map": map_manifest,
        "time_and_support_policy": {
            "catalog_time_is_the_ais_source_reference_instant": True,
            "enc_aids_retain_source_retrieval_and_chart_epoch_per_provenance": True,
            "ais_and_enc_are_mixed_epoch_inputs": True,
            "unknown_map_regions_are_not_inferred_as_water_or_land": True,
            "range_los_and_angular_merge_must_precede_support_counts": True,
        },
        "artifact_paths_are_server_local": True,
    }
    OUTPUT_MANIFEST.write_text(
        json.dumps(output_manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"Catalog: {catalog_path} sha256={sha256(catalog_path)} records={len(combined.records)}")
    print(
        f"Raw aid components: {raw_aid_path} sha256={raw_catalog_hash} "
        f"records={len(raw_aid_catalog.records)}"
    )
    print(f"Map: {map_path} sha256={sha256(map_path)} status={fullcoast_map.coverage_status}")
    print(f"Manifest: {OUTPUT_MANIFEST} sha256={sha256(OUTPUT_MANIFEST)}")
    print(
        f"Physical aids runtime={aid_runtime_count}; "
        f"groups={len(aid_catalog.physical_aids)}; raw rows={len(aid_catalog.features)}"
    )
    print(
        f"AIS records={len(ais_catalog.records)}; report age seconds "
        f"min/median/max={min(ais_ages_s)}/"
        f"{sorted(ais_ages_s)[len(ais_ages_s) // 2]}/{max(ais_ages_s)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
