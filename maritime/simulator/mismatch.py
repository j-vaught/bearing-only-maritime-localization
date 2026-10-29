"""Version 2 simulator inputs and controlled sensor/catalog mismatch settings."""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite

from maritime.catalog_identity import (
    canonicalize_chart_aids,
    canonicalize_chart_records,
    is_chart_aid,
)
from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import CatalogRecordV2, CatalogV2, MeasurementSetV2
from maritime.schemas.truth import EvaluationTruth


@dataclass(frozen=True, slots=True)
class PhysicalObjectV2:
    """A physical target absent from the search catalogue."""

    object_id: str
    longitude_deg: float
    latitude_deg: float
    raw_type: str | None = None
    normalized_type: str | None = None
    metadata: tuple[tuple[str, object], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.object_id, str) or not self.object_id.strip():
            raise ValueError("object_id must be a nonempty string.")
        if not isfinite(self.longitude_deg) or not -180.0 <= self.longitude_deg <= 180.0:
            raise ValueError("longitude_deg must be finite and in [-180, 180].")
        if not isfinite(self.latitude_deg) or not -90.0 <= self.latitude_deg <= 90.0:
            raise ValueError("latitude_deg must be finite and in [-90, 90].")
        if self.raw_type is not None and not self.raw_type.strip():
            raise ValueError("raw_type must be nonempty or None.")
        if self.normalized_type is not None and not self.normalized_type.strip():
            raise ValueError("normalized_type must be nonempty or None.")
        keys = [key for key, _ in self.metadata]
        if len(keys) != len(set(keys)) or any(not isinstance(key, str) or not key for key in keys):
            raise ValueError("metadata keys must be unique, nonempty strings.")


@dataclass(frozen=True, slots=True)
class PhysicalSceneV2:
    """Physical catalog targets and explicit uncataloged visible targets."""

    catalog: CatalogV2
    uncataloged_physical_objects: tuple[PhysicalObjectV2, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.catalog, CatalogV2):
            raise TypeError("catalog must be a CatalogV2.")
        if not isinstance(self.uncataloged_physical_objects, tuple) or any(
            not isinstance(item, PhysicalObjectV2) for item in self.uncataloged_physical_objects
        ):
            raise TypeError("uncataloged_physical_objects must be a tuple of PhysicalObjectV2.")
        keys = [item.object_id for item in self.uncataloged_physical_objects]
        if len(keys) != len(set(keys)) or set(keys).intersection(
            record.key for record in self.catalog.records
        ):
            raise ValueError("Physical object IDs must be unique and distinct from catalog keys.")

        if not self.uncataloged_physical_objects:
            object.__setattr__(self, "catalog", canonicalize_chart_aids(self.catalog))
            return

        catalog_keys = {record.key for record in self.catalog.records}
        catalog_aid_locations = {
            (record.latitude_deg, record.longitude_deg)
            for record in self.catalog.records
            if is_chart_aid(record)
        }
        physical_rows = tuple(
            CatalogRecordV2(
                key=item.object_id,
                latitude_deg=item.latitude_deg,
                longitude_deg=item.longitude_deg,
                raw_type=item.raw_type,
                normalized_type=item.normalized_type,
                metadata=item.metadata,
            )
            for item in self.uncataloged_physical_objects
        )
        rows = canonicalize_chart_records((*self.catalog.records, *physical_rows))
        known, unknown = [], []
        for row in rows:
            catalogued = row.key in catalog_keys or (
                is_chart_aid(row) and (row.latitude_deg, row.longitude_deg) in catalog_aid_locations
            )
            if catalogued:
                known.append(row)
            else:
                unknown.append(
                    PhysicalObjectV2(
                        row.key,
                        row.longitude_deg,
                        row.latitude_deg,
                        row.raw_type,
                        row.normalized_type,
                        row.metadata,
                    )
                )
        object.__setattr__(self, "catalog", replace(self.catalog, records=tuple(known)))
        object.__setattr__(self, "uncataloged_physical_objects", tuple(unknown))


@dataclass(frozen=True, slots=True)
class KnownWaterObserver:
    """Observer pose in geographic coordinates, with heading clockwise from true north."""

    longitude_deg: float
    latitude_deg: float
    heading_deg: float = 0.0

    def __post_init__(self) -> None:
        if not isfinite(self.longitude_deg) or not -180.0 <= self.longitude_deg <= 180.0:
            raise ValueError("longitude_deg must be finite and in [-180, 180].")
        if not isfinite(self.latitude_deg) or not -90.0 <= self.latitude_deg <= 90.0:
            raise ValueError("latitude_deg must be finite and in [-90, 90].")
        if not isfinite(self.heading_deg):
            raise ValueError("heading_deg must be finite.")


@dataclass(frozen=True, slots=True)
class SensorConfigurationV2:
    """Physical sensor parameters; width and bearing uncertainty remain distinct."""

    visibility_radius_m: float = 10_000.0
    angular_resolution_deg: float = 0.1
    bearing_sigma_deg: float = 1.0 / 6.0
    bearing_max_error_deg: float | None = 0.5
    detection_probability: float = 0.9
    type_error_probability: float = 0.0
    expected_clutter_reports: float = 1.0
    outlier_probability: float = 0.0
    report_type_labels: bool = True

    def __post_init__(self) -> None:
        for name in ("visibility_radius_m", "bearing_sigma_deg", "expected_clutter_reports"):
            value = getattr(self, name)
            if not isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and nonnegative.")
        if self.bearing_max_error_deg is not None:
            if (
                isinstance(self.bearing_max_error_deg, bool)
                or not isfinite(self.bearing_max_error_deg)
                or self.bearing_max_error_deg < 0
            ):
                raise ValueError("bearing_max_error_deg must be finite and nonnegative or None.")
            if self.outlier_probability != 0:
                raise ValueError("A hard bearing bound requires outlier_probability=0.")
        if self.visibility_radius_m == 0.0:
            raise ValueError("visibility_radius_m must be positive.")
        if (
            not isfinite(self.angular_resolution_deg)
            or not 0.0 <= self.angular_resolution_deg < 180.0
        ):
            raise ValueError("angular_resolution_deg must be finite and in [0, 180).")
        for name in ("detection_probability", "type_error_probability", "outlier_probability"):
            value = getattr(self, name)
            if not isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be finite and in [0, 1].")
        if not isinstance(self.report_type_labels, bool):
            raise TypeError("report_type_labels must be a bool.")


@dataclass(frozen=True, slots=True)
class SimulatorMismatchConfig:
    """Stress controls. Displacement moves physical catalog targets relative to catalog input."""

    heading_bias_deg: float = 0.0
    catalog_displacement_east_m: float = 0.0
    catalog_displacement_north_m: float = 0.0
    observation_map: VectorMap | None = None

    def __post_init__(self) -> None:
        for name in (
            "heading_bias_deg",
            "catalog_displacement_east_m",
            "catalog_displacement_north_m",
        ):
            if not isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite.")
        if self.observation_map is not None and not isinstance(self.observation_map, VectorMap):
            raise TypeError("observation_map must be a VectorMap or None.")


@dataclass(frozen=True, slots=True)
class GroupTruthDiagnostic:
    """Truth-side postmerge group support, separate from estimator measurements."""

    member_object_ids: tuple[str, ...]
    representative_object_id: str
    catalog_member_keys: tuple[str, ...]
    representative_catalog_key: str | None
    catalog_member_physical_ids: tuple[str, ...]
    identity_limited_catalog_member_keys: tuple[str, ...]
    detected: bool
    observation_index: int | None


@dataclass(frozen=True, slots=True)
class SimulationDiagnosticsV2:
    """Counts and association details reserved for evaluator/support construction."""

    in_range_physical_count: int
    line_of_sight_visible_physical_count: int
    merged_physical_group_count: int
    detected_physical_group_count: int
    clutter_report_count: int
    groups: tuple[GroupTruthDiagnostic, ...]
    observation_map_coverage: str
    heading_bias_deg: float
    catalog_displacement_east_m: float
    catalog_displacement_north_m: float


@dataclass(frozen=True, slots=True)
class SimulationRunV2:
    """Estimator measurements and evaluator-only products from a v2 simulation."""

    measurements: MeasurementSetV2
    truth: EvaluationTruth
    diagnostics: SimulationDiagnosticsV2
