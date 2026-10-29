"""Scale-aware NOAA ENC polygon ingestion with explicit chart provenance."""

from __future__ import annotations

import csv
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pyproj import CRS, Transformer
from shapely import make_valid, segmentize, transform
from shapely.geometry import GeometryCollection, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.strtree import STRtree

from maritime.catalog_identity import canonicalize_chart_aids
from maritime.schemas.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MapV2,
)

SCALE_PRIORITY = ("berthing", "harbour", "approach", "coastal", "general", "overview")
_SCALE_RANK = {band: index for index, band in enumerate(SCALE_PRIORITY)}


@dataclass(frozen=True, slots=True)
class ENCFeature:
    """One source ENC polygon with stable feature and chart identity."""

    feature_id: str
    chart_id: str
    scale_band: str
    layer: str
    geometry: BaseGeometry
    attributes: tuple[tuple[str, Any], ...] = ()
    source: str = "NOAA ENC Direct"
    epoch: str | None = None
    identity_ambiguous: bool = False

    def __post_init__(self) -> None:
        if not self.feature_id or not self.chart_id or not self.layer:
            raise ValueError("ENC feature, chart, and layer identities must be nonempty.")
        if self.scale_band not in _SCALE_RANK:
            raise ValueError(f"Unsupported ENC scale band {self.scale_band!r}.")
        if self.geometry.is_empty or self.geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError("ENC features must contain nonempty polygon geometry.")
        if not self.geometry.is_valid:
            raise ValueError(f"ENC feature {self.feature_id!r} has invalid geometry.")
        if len(dict(self.attributes)) != len(self.attributes):
            raise ValueError("ENC feature attribute keys must be unique.")


@dataclass(frozen=True, slots=True)
class ENCCollection:
    """Scale-specific coverage and land polygons in their declared CRS."""

    crs: str
    features: tuple[ENCFeature, ...]
    collection_id: str
    source: str = "NOAA ENC Direct"

    def __post_init__(self) -> None:
        if not self.crs or not self.collection_id:
            raise ValueError("ENC collection CRS and identity must be nonempty.")
        ids = [feature.feature_id for feature in self.features]
        if len(ids) != len(set(ids)):
            raise ValueError("ENC feature IDs must be unique within a collection.")


@dataclass(frozen=True, slots=True)
class MosaicLayer:
    """A disjoint effective scale region and land clipped to that region."""

    scale_band: str
    coverage: BaseGeometry
    land: BaseGeometry
    feature_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ENCAid:
    """One aid row; coincident source features are retained independently."""

    feature_id: str
    chart_id: str
    scale_band: str
    service_layer: str
    longitude_deg: float
    latitude_deg: float
    attributes: tuple[tuple[str, Any], ...]
    identity_ambiguous: bool = False
    source: str | None = None
    source_retrieved_utc: str | None = None


@dataclass(frozen=True, slots=True)
class PhysicalAid:
    """One resolved physical aid with links to all source component rows."""

    physical_id: str
    chart_id: str
    fullname: str
    longitude_deg: float
    latitude_deg: float
    component_types: tuple[str, ...]
    feature_ids: tuple[str, ...]
    identity_ambiguous: bool


@dataclass(frozen=True, slots=True)
class ENCAidCatalog:
    """Full source catalog with lossless features and conservative physical groups."""

    features: tuple[ENCAid, ...]
    physical_aids: tuple[PhysicalAid, ...]
    source: str | None
    retrieved_utc: str | None


def _normalized_name(aid: ENCAid) -> str:
    attrs = dict(aid.attributes)
    name = attrs.get("fullname") or attrs.get("objnam") or attrs.get("name") or ""
    return re.sub(r"[^a-z0-9]+", " ", str(name).casefold()).strip()


def _component_type(aid: ENCAid) -> str:
    layer = aid.service_layer.rsplit(".", 1)[-1].casefold()
    for component in ("topmark", "light", "beacon", "daymark"):
        if component in layer:
            return component
    return layer


def _distance_m(first: ENCAid, second: ENCAid) -> float:
    lat1, lat2 = math.radians(first.latitude_deg), math.radians(second.latitude_deg)
    dlat = lat2 - lat1
    dlon = math.radians(second.longitude_deg - first.longitude_deg)
    hav = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6_371_008.8 * 2 * math.asin(min(1.0, math.sqrt(hav)))


def group_physical_aids(
    features: Iterable[ENCAid], *, proximity_m: float = 20.0
) -> tuple[PhysicalAid, ...]:
    """Group named compatible components without dropping source features.

    Components are grouped only within the same source chart and within the
    distance limit. Source-layer identity and scale remain on every feature.
    Ambiguous name collisions and unidentifiable source rows remain flagged.
    """
    if proximity_m <= 0 or not math.isfinite(proximity_m):
        raise ValueError("Physical aid proximity must be finite and positive.")
    rows = tuple(features)
    named: dict[tuple[str, str], list[ENCAid]] = {}
    singles: list[ENCAid] = []
    for aid in rows:
        name = _normalized_name(aid)
        if not name:
            singles.append(aid)
        else:
            named.setdefault((aid.chart_id, name), []).append(aid)

    raw_groups: list[list[ENCAid]] = [[aid] for aid in singles]
    ambiguous_group_indexes: set[int] = set()
    for (chart_id, name), cluster_rows in sorted(named.items()):
        groups: list[list[ENCAid]] = []
        local_ambiguous: set[int] = set()
        for aid in sorted(
            cluster_rows,
            key=lambda item: (
                item.latitude_deg,
                item.longitude_deg,
                item.scale_band,
                item.service_layer,
                item.feature_id,
            ),
        ):
            candidates = []
            for index, group in enumerate(groups):
                if all(_distance_m(aid, member) <= proximity_m for member in group):
                    component = _component_type(aid)
                    types = {_component_type(member) for member in group}
                    scales = {member.scale_band for member in group}
                    if component not in types or aid.scale_band not in scales:
                        candidates.append(index)
            if len(candidates) == 1:
                groups[candidates[0]].append(aid)
            else:
                groups.append([aid])
                if len(candidates) > 1:
                    local_ambiguous.add(len(groups) - 1)
                    local_ambiguous.update(candidates)
        # Colocated same-name groups can be separate physical aids or source
        # components that lack enough identity to resolve safely.
        for i, first in enumerate(groups):
            for j in range(i + 1, len(groups)):
                if any(_distance_m(a, b) <= proximity_m for a in first for b in groups[j]):
                    local_ambiguous.update((i, j))
        offset = len(raw_groups)
        raw_groups.extend(groups)
        ambiguous_group_indexes.update(offset + i for i in local_ambiguous)

    physical: list[PhysicalAid] = []
    for index, group in enumerate(raw_groups):
        group = sorted(group, key=lambda item: item.feature_id)
        name = _normalized_name(group[0])
        digest_payload = [group[0].chart_id, name, [aid.feature_id for aid in group]]
        import hashlib

        digest = hashlib.sha256(
            json.dumps(digest_payload, separators=(",", ":")).encode()
        ).hexdigest()[:20]
        physical.append(
            PhysicalAid(
                physical_id=f"enc-physical:{digest}",
                chart_id=group[0].chart_id,
                fullname=name,
                longitude_deg=sum(aid.longitude_deg for aid in group) / len(group),
                latitude_deg=sum(aid.latitude_deg for aid in group) / len(group),
                component_types=tuple(sorted({_component_type(aid) for aid in group})),
                feature_ids=tuple(aid.feature_id for aid in group),
                identity_ambiguous=(
                    index in ambiguous_group_indexes or any(aid.identity_ambiguous for aid in group)
                ),
            )
        )
    return tuple(physical)


def load_enc_physical_aid_catalog(path: str | Path) -> ENCAidCatalog:
    """Load the complete ENC aid CSV as both source rows and physical groups."""
    features = load_enc_aids_csv(path)
    source_manifest_path = Path(path).with_suffix(".manifest.json")
    source_manifest = (
        json.loads(source_manifest_path.read_text(encoding="utf-8"))
        if source_manifest_path.exists()
        else {}
    )
    return ENCAidCatalog(
        features=features,
        physical_aids=group_physical_aids(features),
        source=source_manifest.get("source"),
        retrieved_utc=source_manifest.get("retrieved_utc"),
    )


def load_enc_aids_csv(path: str | Path) -> tuple[ENCAid, ...]:
    """Load source aid rows without coordinate rounding or cross-scale merging."""
    rows: list[ENCAid] = []
    occurrences: dict[str, int] = {}
    source_path = Path(path)
    manifest_path = source_path.with_suffix(".manifest.json")
    source_manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    )
    with source_path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            scale = row["scale_band"]
            layer = row["service_layer"]
            attrs = {
                key: value
                for key, value in row.items()
                if key not in {"scale_band", "service_layer", "longitude", "latitude"}
            }
            chart_id = str(attrs.get("dsnm") or "chart-identity-unknown")
            identity = [
                (key, value)
                for key, value in attrs.items()
                if value
                and (key.endswith(".fid") or key in {"lnam", "objectid", "source_object_id"})
            ]
            ambiguous = not identity
            if identity:
                feature_id = f"{scale}:{layer}:{chart_id}:" + "|".join(
                    f"{key}={value}" for key, value in sorted(identity)
                )
            else:
                import hashlib

                canonical = json.dumps(
                    [scale, layer, chart_id, row.get("longitude"), row.get("latitude"), attrs],
                    sort_keys=True,
                    separators=(",", ":"),
                )
                feature_id = f"ambiguous:{hashlib.sha256(canonical.encode()).hexdigest()[:20]}"
            occurrence = occurrences.get(feature_id, 0)
            occurrences[feature_id] = occurrence + 1
            if occurrence:
                ambiguous = True
                feature_id = f"{feature_id}:duplicate-{occurrence}"
            rows.append(
                ENCAid(
                    feature_id=feature_id,
                    chart_id=chart_id,
                    scale_band=scale,
                    service_layer=layer,
                    longitude_deg=float(row["longitude"]),
                    latitude_deg=float(row["latitude"]),
                    attributes=tuple(sorted(attrs.items())),
                    identity_ambiguous=ambiguous,
                    source=source_manifest.get("source"),
                    source_retrieved_utc=source_manifest.get("retrieved_utc"),
                )
            )
    return tuple(rows)


def enc_aids_catalog_to_catalog_v2(catalog: ENCAidCatalog) -> CatalogV2:
    """Convert source aid rows to CatalogV2 with only resolved physical identities."""
    group_for_feature = {
        feature_id: physical
        for physical in catalog.physical_aids
        for feature_id in physical.feature_ids
    }
    records: list[CatalogRecordV2] = []
    for aid in catalog.features:
        physical = group_for_feature.get(aid.feature_id)
        attrs = dict(aid.attributes)
        linkage_resolved = bool(physical and physical.fullname and not physical.identity_ambiguous)
        metadata: dict[str, Any] = {
            "chart_id": aid.chart_id,
            "scale_band": aid.scale_band,
            "service_layer": aid.service_layer,
            "source_feature_id": aid.feature_id,
            "source_record_identity_status": (
                "snapshot_row_derived" if aid.identity_ambiguous else "qualified_source_key"
            ),
            "physical_linkage_status": "resolved" if linkage_resolved else "unresolved",
            "observation_association_status": "candidate_geometry_only",
            "identity_ambiguous": not linkage_resolved,
            "source_attributes": attrs,
        }
        if linkage_resolved and physical is not None:
            metadata["physical_entity_id"] = physical.physical_id
        provenance = {
            "source": aid.source or catalog.source or "NOAA ENC Direct to GIS",
            "source_retrieved_utc": aid.source_retrieved_utc or catalog.retrieved_utc,
            "source_epoch_sordat": attrs.get("sordat") or None,
            "source_object_identity": [
                [key, value]
                for key, value in aid.attributes
                if value and (key.endswith(".fid") or key in {"source_object_id", "objectid"})
            ],
        }
        records.append(
            CatalogRecordV2(
                key=aid.feature_id,
                latitude_deg=aid.latitude_deg,
                longitude_deg=aid.longitude_deg,
                raw_type=aid.service_layer,
                normalized_type=_component_type(aid),
                metadata=tuple(sorted(metadata.items())),
                provenance=tuple(sorted(provenance.items())),
            )
        )
    timestamp = catalog.retrieved_utc or datetime.now(UTC).isoformat()
    return CatalogV2(
        records=tuple(records),
        timestamp_utc=timestamp,
        source=catalog.source or "NOAA ENC Direct to GIS",
        catalog_version=f"eastern-seaboard-enc-aids:{timestamp}",
        catalog_id="noaa-enc-direct-eastern-seaboard-physical-aids",
    )


def combine_ais_and_enc_physical_catalogs(
    ais_catalog: CatalogV2,
    aid_catalog: ENCAidCatalog,
    *,
    catalog_id: str = "eastern-seaboard-ais-enc-physical-aids-v2",
) -> CatalogV2:
    """Compose AIS and all source aids, resolving only defensible identities.

    Resolved named groups retain their source components. All chart aids at
    exactly the same latitude/longitude become one runtime physical object,
    retaining source rows and geometric uncertainty. Vessels remain separate.
    AIS timestamps and quality are preserved unchanged.
    """
    if not isinstance(ais_catalog, CatalogV2):
        raise TypeError("ais_catalog must be a CatalogV2.")
    if not isinstance(aid_catalog, ENCAidCatalog):
        raise TypeError("aid_catalog must be an ENCAidCatalog.")
    if not catalog_id.strip():
        raise ValueError("catalog_id must be nonempty.")
    by_feature = {feature.feature_id: feature for feature in aid_catalog.features}
    records = list(ais_catalog.records)
    keys = {record.key for record in records}
    named_groups: dict[str, list[PhysicalAid]] = {}
    for physical in aid_catalog.physical_aids:
        if physical.fullname:
            named_groups.setdefault(physical.fullname, []).append(physical)
    cross_chart_collisions: set[str] = set()
    for same_name_groups in named_groups.values():
        for index, first in enumerate(same_name_groups):
            first_feature = by_feature[first.feature_ids[0]]
            for second in same_name_groups[index + 1 :]:
                if first.chart_id == second.chart_id:
                    continue
                second_feature = by_feature[second.feature_ids[0]]
                if _distance_m(first_feature, second_feature) <= 20.0:
                    cross_chart_collisions.update((first.physical_id, second.physical_id))

    resolved_groups = {
        physical.physical_id: physical
        for physical in aid_catalog.physical_aids
        if physical.fullname
        and not physical.identity_ambiguous
        and physical.physical_id not in cross_chart_collisions
    }
    resolved_features = {
        feature_id for physical in resolved_groups.values() for feature_id in physical.feature_ids
    }
    for physical in aid_catalog.physical_aids:
        if physical.physical_id not in resolved_groups:
            continue
        components = [by_feature[key] for key in physical.feature_ids]
        if physical.physical_id in keys:
            raise ValueError(f"Catalog key collision for physical aid {physical.physical_id!r}.")
        keys.add(physical.physical_id)
        component_rows = [
            {
                "feature_id": aid.feature_id,
                "chart_id": aid.chart_id,
                "scale_band": aid.scale_band,
                "service_layer": aid.service_layer,
                "longitude_deg": aid.longitude_deg,
                "latitude_deg": aid.latitude_deg,
                "source_epoch_sordat": dict(aid.attributes).get("sordat") or None,
            }
            for aid in components
        ]
        epochs = sorted(
            {
                str(value)
                for aid in components
                if (value := dict(aid.attributes).get("sordat")) not in (None, "")
            }
        )
        metadata = {
            "identity_ambiguous": False,
            "source_record_identity_status": "qualified_source_components",
            "physical_linkage_status": "resolved_by_chart_heuristic",
            "observation_association_status": "candidate_geometry_only",
            "identity_resolution": "same_chart_name_and_proximity",
            "physical_entity_id": physical.physical_id,
            "source_component_count": len(components),
            "source_components": component_rows,
            "position_uncertainty_m": None,
            "uncertainty_status": "unspecified_by_source",
        }
        provenance = {
            "source": aid_catalog.source or "NOAA ENC Direct to GIS",
            "source_retrieved_utc": aid_catalog.retrieved_utc,
            "source_chart_ids": sorted({aid.chart_id for aid in components}),
            "source_feature_ids": sorted(aid.feature_id for aid in components),
            "source_layers": sorted({aid.service_layer for aid in components}),
            "source_scale_bands": sorted({aid.scale_band for aid in components}),
            "source_epoch_sordat": epochs,
        }
        records.append(
            CatalogRecordV2(
                key=physical.physical_id,
                latitude_deg=physical.latitude_deg,
                longitude_deg=physical.longitude_deg,
                raw_type="|".join(physical.component_types),
                normalized_type="physical_aid",
                metadata=tuple(sorted(metadata.items())),
                provenance=tuple(sorted(provenance.items())),
            )
        )

    group_by_feature = {
        feature_id: physical
        for physical in aid_catalog.physical_aids
        for feature_id in physical.feature_ids
    }
    for aid in aid_catalog.features:
        if aid.feature_id in resolved_features:
            continue
        physical = group_by_feature.get(aid.feature_id)
        if physical is None:
            reason = "missing_physical_group"
        elif aid.identity_ambiguous:
            reason = "source_identity_ambiguous"
        elif not physical.fullname:
            reason = "missing_name"
        elif physical.physical_id in cross_chart_collisions:
            reason = "cross_chart_same_name_proximity"
        else:
            reason = "grouping_ambiguous"
        key = f"enc-component:{aid.feature_id}"
        if key in keys:
            raise ValueError(f"Catalog key collision for unresolved aid {key!r}.")
        keys.add(key)
        attrs = dict(aid.attributes)
        component_type = _component_type(aid)
        metadata = {
            "identity_ambiguous": True,
            "source_record_identity_status": (
                "snapshot_row_derived" if aid.identity_ambiguous else "qualified_source_key"
            ),
            "physical_linkage_status": "unresolved",
            "observation_association_status": "candidate_geometry_only",
            "identity_resolution": "unresolved",
            "identity_resolution_reason": reason,
            "source_feature_id": aid.feature_id,
            "physical_group_id": physical.physical_id if physical is not None else None,
            "source_component_count": 1,
            "source_components": [
                {
                    "feature_id": aid.feature_id,
                    "chart_id": aid.chart_id,
                    "scale_band": aid.scale_band,
                    "service_layer": aid.service_layer,
                    "longitude_deg": aid.longitude_deg,
                    "latitude_deg": aid.latitude_deg,
                }
            ],
            "source_attributes": attrs,
            "position_uncertainty_m": None,
            "uncertainty_status": "unspecified_by_source",
        }
        provenance = {
            "source": aid.source or aid_catalog.source or "NOAA ENC Direct to GIS",
            "source_retrieved_utc": aid.source_retrieved_utc or aid_catalog.retrieved_utc,
            "source_chart_id": aid.chart_id,
            "source_feature_id": aid.feature_id,
            "source_layer": aid.service_layer,
            "source_scale_band": aid.scale_band,
            "source_epoch_sordat": attrs.get("sordat") or None,
        }
        records.append(
            CatalogRecordV2(
                key=key,
                latitude_deg=aid.latitude_deg,
                longitude_deg=aid.longitude_deg,
                raw_type=aid.service_layer,
                normalized_type=component_type,
                metadata=tuple(sorted(metadata.items())),
                provenance=tuple(sorted(provenance.items())),
            )
        )
    return canonicalize_chart_aids(
        CatalogV2(
            records=tuple(records),
            timestamp_utc=ais_catalog.timestamp_utc,
            source="AIS source snapshot plus NOAA ENC physical aids",
            catalog_version="3-exact-coordinate-aids",
            catalog_id=catalog_id,
        )
    )


def compose_partial_map_v2(
    maps: Iterable[MapV2],
    *,
    domain: BaseGeometry,
    map_version: str,
) -> MapV2:
    """Mosaic reported coverage and make every remaining domain point unknown.

    Partial source maps contribute validated coverage geometry. Source unknown
    areas take precedence in overlaps. No seam becomes inferred water or land.
    """
    domain = _valid_polygon(domain, "map mosaic domain")
    inputs = tuple(maps)
    if not inputs:
        raise ValueError("At least one input MapV2 is required.")
    parsed_crs = CRS.from_user_input(inputs[0].crs)
    coverages: list[BaseGeometry] = []
    unknowns: list[BaseGeometry] = []
    lands: list[BaseGeometry] = []
    for source_map in inputs:
        if not isinstance(source_map, MapV2):
            raise TypeError("maps must contain only MapV2 values.")
        if not parsed_crs.equals(CRS.from_user_input(source_map.crs)):
            raise ValueError("All input maps must use the same declared CRS.")
        if source_map.coverage_geometry_geojson is not None:
            coverages.append(shape(json.loads(source_map.coverage_geometry_geojson)))
        if source_map.unknown_geometry_geojson is not None:
            unknowns.append(shape(json.loads(source_map.unknown_geometry_geojson)))
        if source_map.land_geometry_geojson is not None:
            lands.append(shape(json.loads(source_map.land_geometry_geojson)))
    source_coverage = unary_union(coverages) if coverages else GeometryCollection()
    source_unknown = unary_union(unknowns) if unknowns else GeometryCollection()
    coverage = _polygonal_only(source_coverage.intersection(domain).difference(source_unknown))
    unknown = _polygonal_only(domain.difference(coverage))
    land = _polygonal_only(
        (unary_union(lands) if lands else GeometryCollection()).intersection(coverage)
    )
    status: Literal["known", "partial", "unknown"]
    if coverage.is_empty:
        status = "unknown"
    elif unknown.is_empty:
        status = "known"
    else:
        status = "partial"
    return MapV2(
        crs=inputs[0].crs,
        source="NOAA ENC Direct validated port map mosaics",
        map_version=map_version,
        coverage_status=status,
        coverage_geometry_geojson=_geojson_geometry(coverage) if not coverage.is_empty else None,
        unknown_geometry_geojson=_geojson_geometry(unknown) if status == "partial" else None,
        land_geometry_geojson=_geojson_geometry(land) if not land.is_empty else None,
    )


def compose_fallback_map_v2(
    fallback_map: MapV2,
    detailed_maps: Iterable[MapV2],
    *,
    domain: BaseGeometry,
    map_version: str,
    source: str,
) -> MapV2:
    """Overlay validated detailed chart coverage on a coarser fallback.

    A detailed map is authoritative only inside its own reported known
    coverage. Its unknown or out-of-chart geometry does not erase available
    fallback coverage. Areas outside every source's known coverage remain
    explicitly unknown, and land is always intersected with known coverage.
    """
    if not isinstance(fallback_map, MapV2):
        raise TypeError("fallback_map must be a MapV2 value.")
    domain = _valid_polygon(domain, "fallback map composition domain")
    detailed_inputs = tuple(detailed_maps)
    target_crs = CRS.from_user_input(fallback_map.crs)

    def known_coverage(source_map: MapV2) -> BaseGeometry:
        if not isinstance(source_map, MapV2):
            raise TypeError("All map inputs must be MapV2 values.")
        if not target_crs.equals(CRS.from_user_input(source_map.crs)):
            raise ValueError("All input maps must use the same declared CRS.")
        if source_map.coverage_geometry_geojson is None:
            return GeometryCollection()
        coverage = shape(json.loads(source_map.coverage_geometry_geojson))
        if source_map.unknown_geometry_geojson is not None:
            coverage = coverage.difference(shape(json.loads(source_map.unknown_geometry_geojson)))
        return _polygonal_only(coverage.intersection(domain))

    fallback_coverage = known_coverage(fallback_map)
    coverage_parts = [fallback_coverage] if not fallback_coverage.is_empty else []
    fallback_land = GeometryCollection()
    if not fallback_coverage.is_empty and fallback_map.land_geometry_geojson is not None:
        fallback_land = shape(json.loads(fallback_map.land_geometry_geojson)).intersection(
            fallback_coverage
        )

    detailed_coverage_parts: list[BaseGeometry] = []
    detailed_land_parts: list[BaseGeometry] = []
    for source_map in detailed_inputs:
        detailed_coverage = known_coverage(source_map)
        if detailed_coverage.is_empty:
            continue
        detailed_coverage_parts.append(detailed_coverage)
        coverage_parts.append(detailed_coverage)
        if source_map.land_geometry_geojson is not None:
            detailed_land_parts.append(
                shape(json.loads(source_map.land_geometry_geojson)).intersection(detailed_coverage)
            )

    detailed_coverage_union = (
        unary_union(detailed_coverage_parts) if detailed_coverage_parts else GeometryCollection()
    )
    coverage = _polygonal_only(
        (unary_union(coverage_parts) if coverage_parts else GeometryCollection()).intersection(
            domain
        )
    )
    # Remove coarse land wherever detailed coverage is authoritative, then
    # add detailed land. Detailed chart water therefore overrides coarse land.
    detailed_land = (
        unary_union(detailed_land_parts) if detailed_land_parts else GeometryCollection()
    )
    land = _polygonal_only(
        unary_union(
            [fallback_land.difference(detailed_coverage_union), detailed_land]
        ).intersection(coverage)
    )
    unknown = _polygonal_only(domain.difference(coverage))

    status: Literal["known", "partial", "unknown"]
    if coverage.is_empty:
        status = "unknown"
    elif unknown.is_empty:
        status = "known"
    else:
        status = "partial"
    return MapV2(
        crs=fallback_map.crs,
        source=source,
        map_version=map_version,
        coverage_status=status,
        coverage_geometry_geojson=(_geojson_geometry(coverage) if status != "unknown" else None),
        unknown_geometry_geojson=(_geojson_geometry(unknown) if status == "partial" else None),
        land_geometry_geojson=(_geojson_geometry(land) if not land.is_empty else None),
    )


def compose_scale_fallback_map_v2(
    fallback_map: MapV2,
    detailed_maps_by_scale: Mapping[str, Iterable[MapV2]],
    *,
    domain: BaseGeometry,
    map_version: str,
    source: str,
    densify_geographic_edges: bool = True,
    geographic_segment_length_deg: float | None = None,
    resolve_same_scale_conflicts: bool = True,
) -> tuple[MapV2, dict[str, float]]:
    """Compose per-area detailed scales over a complete coarse fallback.

    Higher-ranked ENC scales claim known coverage before lower scales. For
    overlapping inputs at the same scale, contradictory land/water labels are
    excluded from known coverage and reserve that area from all lower scales.
    This avoids choosing an arbitrary source while preserving genuine unknown
    seams. The returned audit maps each scale to its conflicting degree-squared
    area; callers should report geodesic areas for geographic maps.
    """
    if geographic_segment_length_deg is not None and (
        not math.isfinite(geographic_segment_length_deg) or geographic_segment_length_deg <= 0
    ):
        raise ValueError("geographic_segment_length_deg must be finite and positive.")
    if not isinstance(fallback_map, MapV2):
        raise TypeError("fallback_map must be a MapV2 value.")
    domain = _valid_polygon(domain, "scale fallback composition domain")
    target_crs = CRS.from_user_input(fallback_map.crs)

    def known_parts(source_map: MapV2) -> tuple[BaseGeometry, BaseGeometry]:
        if not isinstance(source_map, MapV2):
            raise TypeError("All map inputs must be MapV2 values.")
        if not target_crs.equals(CRS.from_user_input(source_map.crs)):
            raise ValueError("All input maps must use the same declared CRS.")
        if source_map.coverage_geometry_geojson is None:
            return GeometryCollection(), GeometryCollection()
        coverage = shape(json.loads(source_map.coverage_geometry_geojson))
        if source_map.unknown_geometry_geojson is not None:
            coverage = coverage.difference(shape(json.loads(source_map.unknown_geometry_geojson)))
        coverage = _polygonal_only(coverage.intersection(domain))
        land = (
            shape(json.loads(source_map.land_geometry_geojson))
            if source_map.land_geometry_geojson is not None
            else GeometryCollection()
        )
        land = _polygonal_only(land.intersection(coverage))
        return coverage, land

    scale_maps: dict[str, tuple[MapV2, ...]] = {}
    for band, maps in detailed_maps_by_scale.items():
        if band not in _SCALE_RANK:
            raise ValueError(f"Unsupported ENC scale band {band!r}.")
        scale_maps[band] = tuple(maps)

    reserved = GeometryCollection()
    output_coverages: list[BaseGeometry] = []
    output_lands: list[BaseGeometry] = []
    conflict_area_by_scale: dict[str, float] = {}
    for band in SCALE_PRIORITY:
        maps = scale_maps.get(band, ())
        parts = [known_parts(source_map) for source_map in maps]
        parts = [(coverage, land) for coverage, land in parts if not coverage.is_empty]
        if not parts:
            conflict_area_by_scale[band] = 0.0
            continue
        band_coverage = unary_union([coverage for coverage, _ in parts])
        band_land = unary_union([land for _, land in parts])
        conflict_parts: list[BaseGeometry] = []
        if resolve_same_scale_conflicts:
            water_parts = [_polygonal_only(coverage.difference(land)) for coverage, land in parts]
            coverage_tree = STRtree([coverage for coverage, _ in parts])
            for index, (first_coverage, first_land) in enumerate(parts):
                first_water = water_parts[index]
                for candidate_index in coverage_tree.query(first_coverage):
                    second_index = int(candidate_index)
                    if second_index <= index:
                        continue
                    second_coverage, second_land = parts[second_index]
                    overlap = first_coverage.intersection(second_coverage)
                    if overlap.is_empty:
                        continue
                    second_water = water_parts[second_index]
                    disagreement = _polygonal_only(
                        unary_union(
                            [
                                _polygonal_only(first_land.intersection(second_water)),
                                _polygonal_only(first_water.intersection(second_land)),
                            ]
                        ).intersection(overlap)
                    )
                    if not disagreement.is_empty:
                        conflict_parts.append(disagreement)
        conflict = (
            _polygonal_only(unary_union(conflict_parts)) if conflict_parts else GeometryCollection()
        )
        conflict_area_by_scale[band] = float(conflict.area)
        effective = _polygonal_only(band_coverage.difference(reserved).difference(conflict))
        if not effective.is_empty:
            output_coverages.append(effective)
            output_lands.append(_polygonal_only(band_land.intersection(effective)))
        # Any source coverage at this scale owns the area even if contradictory.
        reserved = _polygonal_only(unary_union([reserved, band_coverage, conflict]))

    fallback_coverage, fallback_land = known_parts(fallback_map)
    fallback_effective = _polygonal_only(fallback_coverage.difference(reserved))
    if not fallback_effective.is_empty:
        output_coverages.append(fallback_effective)
        output_lands.append(_polygonal_only(fallback_land.intersection(fallback_effective)))

    coverage = _polygonal_only(
        (unary_union(output_coverages) if output_coverages else GeometryCollection()).intersection(
            domain
        )
    )
    land = _polygonal_only(
        (unary_union(output_lands) if output_lands else GeometryCollection()).intersection(coverage)
    )
    unknown = _polygonal_only(domain.difference(coverage))
    status: Literal["known", "partial", "unknown"]
    if coverage.is_empty:
        status = "unknown"
    elif unknown.is_empty:
        status = "known"
    else:
        status = "partial"
    result = MapV2(
        crs=fallback_map.crs,
        source=source,
        map_version=map_version,
        coverage_status=status,
        coverage_geometry_geojson=(
            _geojson_geometry(
                coverage,
                densify_geographic_edges=(
                    densify_geographic_edges or geographic_segment_length_deg is not None
                ),
                max_segment_length_degrees=geographic_segment_length_deg or 0.0001,
            )
            if status != "unknown"
            else None
        ),
        unknown_geometry_geojson=(
            _geojson_geometry(
                unknown,
                densify_geographic_edges=(
                    densify_geographic_edges or geographic_segment_length_deg is not None
                ),
                max_segment_length_degrees=geographic_segment_length_deg or 0.0001,
            )
            if status == "partial"
            else None
        ),
        land_geometry_geojson=(
            _geojson_geometry(
                land,
                densify_geographic_edges=(
                    densify_geographic_edges or geographic_segment_length_deg is not None
                ),
                max_segment_length_degrees=geographic_segment_length_deg or 0.0001,
            )
            if not land.is_empty
            else None
        ),
    )
    return result, conflict_area_by_scale


def _valid_polygon(geometry: BaseGeometry, label: str) -> BaseGeometry:
    if geometry.is_empty or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError(f"{label} must be a nonempty polygon or multipolygon.")
    if not geometry.is_valid:
        repaired = make_valid(geometry)
        if repaired.is_empty or repaired.geom_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError(f"{label} is invalid and cannot be repaired as polygon geometry.")
        geometry = repaired
    return geometry


def _polygonal_only(geometry: BaseGeometry) -> BaseGeometry:
    """Retain repaired polygon parts after overlays that create boundary remnants."""
    if geometry.is_empty:
        return GeometryCollection()
    candidate = make_valid(geometry) if not geometry.is_valid else geometry
    parts: list[BaseGeometry] = []
    if candidate.geom_type == "Polygon":
        parts = [candidate]
    elif candidate.geom_type in {"MultiPolygon", "GeometryCollection"}:
        for part in candidate.geoms:
            if part.geom_type == "Polygon" and not part.is_empty:
                parts.append(part)
            elif part.geom_type == "MultiPolygon":
                parts.extend(poly for poly in part.geoms if not poly.is_empty)
    result = unary_union(parts) if parts else GeometryCollection()
    if result.is_empty:
        return GeometryCollection()
    if not result.is_valid:
        result = make_valid(result)
        if result.geom_type == "GeometryCollection":
            parts = [part for part in result.geoms if part.geom_type == "Polygon"]
            result = unary_union(parts) if parts else GeometryCollection()
    return result


def _geojson_geometry(
    geometry: BaseGeometry,
    *,
    densify_geographic_edges: bool = True,
    max_segment_length_degrees: float = 0.0001,
) -> str:
    if geometry.is_empty:
        geometry = GeometryCollection()
    else:
        geometry = _polygonal_only(geometry)
        if densify_geographic_edges:
            if not math.isfinite(max_segment_length_degrees) or max_segment_length_degrees <= 0:
                raise ValueError("max_segment_length_degrees must be finite and positive.")
            # Densify geographic chords so nonlinear projected CRS transforms
            # retain valid shoreline topology when MapV2 is explicitly reprojected.
            geometry = _polygonal_only(
                segmentize(geometry, max_segment_length=max_segment_length_degrees)
            )
    return json.dumps(mapping(geometry), sort_keys=True, separators=(",", ":"), allow_nan=False)


def normalize_map_v2_for_target_crs(map_v2: MapV2, target_crs: str | int) -> MapV2:
    """Repair projection-induced polygon topology while retaining geographic MapV2 CRS."""
    forward = Transformer.from_crs(map_v2.crs, target_crs, always_xy=True).transform
    inverse = Transformer.from_crs(target_crs, map_v2.crs, always_xy=True).transform

    def projected(source: str | None) -> BaseGeometry | None:
        if source is None:
            return None
        geometry = transform(shape(json.loads(source)), forward, interleaved=False)
        return _polygonal_only(geometry)

    coverage = projected(map_v2.coverage_geometry_geojson)
    unknown_input = projected(map_v2.unknown_geometry_geojson)
    land = projected(map_v2.land_geometry_geojson)
    if coverage is not None and map_v2.coverage_status == "partial":
        original_coverage = coverage
        unknown_input = unknown_input or GeometryCollection()
        domain = _polygonal_only(unary_union([original_coverage, unknown_input]))
        # Unknown takes precedence where source boundaries overlap after
        # reprojection; retain every source unknown region conservatively.
        coverage = _polygonal_only(original_coverage.difference(unknown_input))
        unknown = _polygonal_only(domain.difference(coverage))
    else:
        unknown = None
    if land is not None and coverage is not None:
        land = _polygonal_only(land.intersection(coverage))
        if unknown is not None:
            land = _polygonal_only(land.difference(unknown))

    def geographic(geometry: BaseGeometry | None) -> str | None:
        if geometry is None:
            return None
        projected_geometry = geometry
        for _ in range(8):
            # Sample target-space straight edges densely before inverse projection
            # so the next CRS transform preserves local shoreline topology.
            sampled = segmentize(projected_geometry, max_segment_length=50.0)
            result = transform(sampled, inverse, interleaved=False)
            check = transform(result, forward, interleaved=False)
            if check.is_valid:
                return json.dumps(
                    mapping(result), sort_keys=True, separators=(",", ":"), allow_nan=False
                )
            projected_geometry = _polygonal_only(check)
        raise ValueError("Map geometry could not be stabilized under target CRS reprojection.")

    return MapV2(
        crs=map_v2.crs,
        source=map_v2.source,
        map_version=map_v2.map_version,
        coverage_status=map_v2.coverage_status,
        coverage_geometry_geojson=geographic(coverage),
        unknown_geometry_geojson=geographic(unknown),
        land_geometry_geojson=geographic(land),
    )


def _safe_feature_id(attrs: dict[str, Any], scale_band: str, layer: str) -> tuple[str, str]:
    """Use the S-57 long name where available, otherwise chart plus service object ID."""
    chart_id = str(attrs.get("DSNM") or attrs.get("CELL_NAME") or "chart-identity-unknown")
    long_name = attrs.get("LNAM")
    object_id = attrs.get("OBJECTID")
    identifier = (
        str(long_name)
        if long_name not in (None, "")
        else (f"{chart_id}:{layer}:{object_id}" if object_id is not None else "")
    )
    if not identifier:
        # Preserve duplicate ambiguity rather than collapsing colocated objects.
        stable_payload = json.dumps(attrs, sort_keys=True, default=str, separators=(",", ":"))
        import hashlib

        digest = hashlib.sha256(stable_payload.encode()).hexdigest()[:16]
        identifier = f"ambiguous:{scale_band}:{layer}:{digest}"
    return chart_id, identifier


def load_enc_geojson(path: str | Path) -> ENCCollection:
    """Read the acquisition tool's per-feature GeoJSON without deduplicating."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("type") != "FeatureCollection":
        raise ValueError("ENC input must be a GeoJSON FeatureCollection.")
    crs = payload.get("crs", {}).get("properties", {}).get("name", "EPSG:4326")
    collection_id = str(payload.get("name") or Path(path).stem)
    features: list[ENCFeature] = []
    occurrences: dict[str, int] = {}
    for raw in payload.get("features", []):
        props = dict(raw.get("properties") or {})
        scale = str(props.get("scale_band", ""))
        layer = str(props.get("service_layer") or props.get("layer") or "unknown")
        chart_id, feature_id = _safe_feature_id(props, scale, layer)
        base_id = feature_id
        occurrence = occurrences.get(base_id, 0)
        occurrences[base_id] = occurrence + 1
        ambiguous = base_id.startswith("ambiguous:") or occurrence > 0
        if occurrence:
            feature_id = f"{base_id}:repeat-{occurrence}"
        geometry = shape(raw.get("geometry") or {})
        features.append(
            ENCFeature(
                feature_id=feature_id,
                chart_id=chart_id,
                scale_band=scale,
                layer=layer,
                geometry=_valid_polygon(geometry, f"feature {feature_id}"),
                attributes=tuple(sorted(props.items())),
                source=str(props.get("source") or "NOAA ENC Direct"),
                epoch=(str(props["SORDAT"]) if props.get("SORDAT") else None),
                identity_ambiguous=ambiguous,
            )
        )
    return ENCCollection(crs=crs, features=tuple(features), collection_id=collection_id)


def _coverage_category(feature: ENCFeature) -> int | None:
    attributes = dict(feature.attributes)
    value = attributes.get("CATCOV", attributes.get("catcov"))
    if value in (1, "1"):
        return 1
    if value in (2, "2"):
        return 2
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized == "coverage available":
            return 1
        if normalized == "no coverage available":
            return 2
    return None


def mosaic_by_scale(
    collection: ENCCollection,
    domain: BaseGeometry,
    *,
    land_layer_names: Iterable[str] = ("Land_Area",),
    coverage_layer_names: Iterable[str] = ("Coverage_area",),
    incomplete_scale_bands: Iterable[str] = (),
) -> tuple[MosaicLayer, ...]:
    """Build scale precedence regions, retaining holes and clipping land exactly."""
    domain = _valid_polygon(domain, "mosaic domain")
    land_names = {name.casefold() for name in land_layer_names}
    coverage_names = {name.casefold() for name in coverage_layer_names}
    selected: dict[str, list[ENCFeature]] = {band: [] for band in SCALE_PRIORITY}
    for feature in collection.features:
        tail = feature.layer.rsplit(".", 1)[-1].casefold()
        if tail in land_names or tail in coverage_names:
            selected[feature.scale_band].append(feature)

    incomplete = set(incomplete_scale_bands)
    unknown_bands = incomplete - set(SCALE_PRIORITY)
    if unknown_bands:
        raise ValueError(f"Unknown ENC scale bands: {sorted(unknown_bands)}.")
    detailed_coverage: list[BaseGeometry] = []
    mosaic: list[MosaicLayer] = []
    for band in SCALE_PRIORITY:
        if band in incomplete:
            continue
        features = selected[band]
        coverage_features = [
            feature
            for feature in features
            if feature.layer.rsplit(".", 1)[-1].casefold() in coverage_names
        ]
        available = [
            feature.geometry.intersection(domain)
            for feature in coverage_features
            if _coverage_category(feature) == 1
        ]
        excluded = [
            feature.geometry.intersection(domain)
            for feature in coverage_features
            if _coverage_category(feature) == 2
        ]
        raw_coverage = _polygonal_only(
            (unary_union(available) if available else GeometryCollection()).difference(
                unary_union(excluded) if excluded else GeometryCollection()
            )
        )
        effective = _polygonal_only(
            (
                raw_coverage.difference(unary_union(detailed_coverage))
                if detailed_coverage
                else raw_coverage
            ).intersection(domain)
        )
        if not effective.is_empty:
            lands = [
                feature.geometry.intersection(effective)
                for feature in features
                if feature.layer.rsplit(".", 1)[-1].casefold() in land_names
            ]
            land = _polygonal_only(unary_union(lands) if lands else GeometryCollection())
            ids = tuple(
                sorted(
                    feature.feature_id
                    for feature in features
                    if feature.geometry.intersects(effective)
                )
            )
            mosaic.append(MosaicLayer(band, effective, land, ids))
        if not raw_coverage.is_empty:
            detailed_coverage.append(raw_coverage)
    return tuple(mosaic)


def mosaic_map_v2(
    collection: ENCCollection,
    domain: BaseGeometry,
    *,
    map_version: str,
    incomplete_scale_bands: Iterable[str] = (),
    densify_geographic_edges: bool = True,
) -> MapV2:
    """Convert the geographic ENC mosaic into the shared version-2 map contract."""
    layers = mosaic_by_scale(collection, domain, incomplete_scale_bands=incomplete_scale_bands)
    known = _polygonal_only(
        unary_union([layer.coverage for layer in layers]) if layers else GeometryCollection()
    )
    land = _polygonal_only(
        unary_union([layer.land for layer in layers]) if layers else GeometryCollection()
    )
    unknown = _polygonal_only(domain.difference(known))
    coverage_geojson = (
        _geojson_geometry(known, densify_geographic_edges=densify_geographic_edges)
        if not known.is_empty
        else None
    )
    unknown_geojson = (
        _geojson_geometry(unknown, densify_geographic_edges=densify_geographic_edges)
        if not unknown.is_empty
        else None
    )
    if not known.is_empty and unknown_geojson is not None:
        encoded_unknown = shape(json.loads(unknown_geojson))
        if encoded_unknown.is_empty and not unknown.is_empty:
            # Retain sub-meter no-data slivers as a conservative uncertainty
            # region instead of dropping them during geographic serialization.
            unknown = _polygonal_only(unknown.buffer(1e-5))
            known = _polygonal_only(known.difference(unknown))
            land = _polygonal_only(land.intersection(known))
            coverage_geojson = _geojson_geometry(
                known, densify_geographic_edges=densify_geographic_edges
            )
            unknown_geojson = _geojson_geometry(
                unknown, densify_geographic_edges=densify_geographic_edges
            )
    status: Literal["known", "partial", "unknown"]
    if known.is_empty:
        status = "unknown"
    elif unknown_geojson is None or shape(json.loads(unknown_geojson)).is_empty:
        status = "known"
        unknown_geojson = None
    else:
        status = "partial"
    return MapV2(
        crs=collection.crs,
        source=collection.source,
        map_version=map_version,
        coverage_status=status,
        coverage_geometry_geojson=(coverage_geojson if status != "unknown" else None),
        unknown_geometry_geojson=(unknown_geojson if status == "partial" else None),
        land_geometry_geojson=(
            _geojson_geometry(land, densify_geographic_edges=densify_geographic_edges)
            if not land.is_empty
            else None
        ),
    )
