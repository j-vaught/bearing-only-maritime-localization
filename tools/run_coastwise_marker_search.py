"""Run the coastwise marker search from explicit V2 map/catalog and marker-only inputs.

The marker JSON is intentionally separate from MeasurementSetV2. A caller must
provide detector-labeled positive/negative marker observations; vessel bearings
are not implicitly treated as chart-marker evidence.
"""

from __future__ import annotations

import argparse
import json
from math import isfinite
from pathlib import Path

from pyproj import CRS, Transformer
from shapely.geometry import shape
from shapely.ops import transform as transform_geometry

from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import CatalogV2, MapV2, MeasurementSetV2
from maritime.schemas.object_types import normalize_object_type
from maritime.search.coastwise_marker_search import run_coastwise_marker_search
from maritime.search.deterministic_marker_scoring import MarkerObservation
from maritime.serialization import read_contract


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path, help="CatalogV2 JSON")
    parser.add_argument("--map", required=True, type=Path, help="MapV2 JSON")
    marker_inputs = parser.add_mutually_exclusive_group(required=True)
    marker_inputs.add_argument(
        "--markers-json",
        type=Path,
        help="Marker-only JSON object with a detections array of MarkerObservation fields",
    )
    marker_inputs.add_argument(
        "--measurements",
        type=Path,
        help="MeasurementSetV2 JSON; only physical_aid reports become positive marker inputs",
    )
    parser.add_argument(
        "--marker-bearing-tolerance-deg",
        type=float,
        help="Required with --measurements; calibrated tolerance used for each typed report",
    )
    parser.add_argument(
        "--assert-typed-markers-reliable",
        action="store_true",
        help="Caller certifies normalized physical_aid labels are reliable marker detections",
    )
    parser.add_argument("--target-crs", help="Projected meter CRS for the map and search bounds")
    parser.add_argument(
        "--bounds-en-m",
        required=True,
        nargs=4,
        type=float,
        metavar=("E_MIN", "E_MAX", "N_MIN", "N_MAX"),
        help="Finite projected search rectangle",
    )
    parser.add_argument("--visibility-radius-m", required=True, type=float)
    parser.add_argument("--chart-coverage-geojson", type=Path)
    parser.add_argument("--chart-coverage-crs", help="CRS of chart coverage geometry")
    parser.add_argument(
        "--assert-chart-catalog-complete",
        action="store_true",
        help="Caller certifies complete chart-marker catalog scope in the supplied polygon",
    )
    parser.add_argument(
        "--assert-catalog-objects-cover-envelope",
        action="store_true",
        help="Caller certifies all catalog object records covering each visibility envelope",
    )
    parser.add_argument("--certified-max-projection-scale", type=float)
    parser.add_argument("--certified-azimuth-error-deg", type=float)
    parser.add_argument("--catalog-position-uncertainty-m", type=float)
    parser.add_argument("--assert-catalog-exact-positions", action="store_true")
    parser.add_argument("--assert-catalog-nominal-visible-offsets", action="store_true")
    parser.add_argument("--assert-catalog-object-identities", action="store_true")
    parser.add_argument("--minimum-distinct-markers", type=int)
    parser.add_argument("--device-ids", required=True, nargs="+", type=int)
    parser.add_argument("--matching-backend", choices=("gpu", "cpu"), default="gpu")
    parser.add_argument("--initial-cell-size-m", type=float, default=10_000.0)
    parser.add_argument("--target-cell-size-m", type=float, default=1_000.0)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--max-evaluations", type=int)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def _measurements_to_marker_observations(
    measurements: MeasurementSetV2,
    *,
    tolerance_deg: float,
    typed_markers_reliable: bool,
) -> tuple[MarkerObservation, ...]:
    """Adapt report order to stable IDs without using vessel/unknown reports as markers."""
    if not isinstance(measurements, MeasurementSetV2):
        raise TypeError("measurements must be a MeasurementSetV2.")
    if not isinstance(typed_markers_reliable, bool):
        raise TypeError("typed_markers_reliable must be bool.")
    if (
        not isinstance(tolerance_deg, (int, float))
        or isinstance(tolerance_deg, bool)
        or not isfinite(tolerance_deg)
        or not 0.0 <= tolerance_deg <= 180.0
    ):
        raise ValueError("tolerance_deg must be finite and in [0, 180].")
    observations = []
    for index, measurement in enumerate(measurements.measurements):
        normalized_type = normalize_object_type(measurement.object_type) or "unknown"
        is_aid = normalized_type == "physical_aid"
        is_trusted = is_aid and typed_markers_reliable
        report_id = f"measurement-{index:06d}@{measurement.timestamp_utc}#type={normalized_type}"
        observations.append(
            MarkerObservation(
                marker_id=report_id,
                bearing_deg=measurement.bearing_deg % 360.0,
                tolerance_deg=tolerance_deg,
                positive=is_aid,
                identity_status="resolved" if is_trusted or not is_aid else "unresolved",
                tolerance_calibrated=is_trusted,
            )
        )
    return tuple(observations)


def main() -> None:
    args = _arguments()
    catalog = read_contract(args.catalog, CatalogV2)
    map_v2 = read_contract(args.map, MapV2)
    vector_map = VectorMap.from_map_v2(map_v2, target_crs=args.target_crs)

    if args.markers_json is not None:
        if args.marker_bearing_tolerance_deg is not None or args.assert_typed_markers_reliable:
            raise ValueError(
                "--marker-bearing-tolerance-deg and --assert-typed-markers-reliable are only "
                "valid with --measurements."
            )
        marker_payload = json.loads(args.markers_json.read_text(encoding="utf-8"))
        if not isinstance(marker_payload, dict) or set(marker_payload) != {"detections", "schema"}:
            raise ValueError("marker JSON must contain exactly schema and detections.")
        if marker_payload["schema"] != "MarkerObservationSetV1":
            raise ValueError("marker JSON schema must be MarkerObservationSetV1.")
        raw_markers = marker_payload["detections"]
        if not isinstance(raw_markers, list):
            raise ValueError("marker detections must be a JSON array.")
        markers = tuple(MarkerObservation(**row) for row in raw_markers)
    else:
        if args.marker_bearing_tolerance_deg is None:
            raise ValueError("--marker-bearing-tolerance-deg is required with --measurements.")
        measurements = read_contract(args.measurements, MeasurementSetV2)
        markers = _measurements_to_marker_observations(
            measurements,
            tolerance_deg=args.marker_bearing_tolerance_deg,
            typed_markers_reliable=args.assert_typed_markers_reliable,
        )

    chart_geometry = None
    if args.chart_coverage_geojson is not None:
        if not args.chart_coverage_crs:
            raise ValueError("--chart-coverage-crs is required with chart coverage GeoJSON.")
        geometry_payload = json.loads(args.chart_coverage_geojson.read_text(encoding="utf-8"))
        if isinstance(geometry_payload, dict) and geometry_payload.get("type") == "Feature":
            geometry_payload = geometry_payload.get("geometry")
        chart_geometry = shape(geometry_payload)
        source_crs = CRS.from_user_input(args.chart_coverage_crs)
        if not source_crs.equals(vector_map.crs):
            transformer = Transformer.from_crs(source_crs, vector_map.crs, always_xy=True)
            chart_geometry = transform_geometry(transformer.transform, chart_geometry)

    def progress(stage: str, values: dict[str, int]) -> None:
        print(json.dumps({"stage": stage, **values}), flush=True)

    result = run_coastwise_marker_search(
        vector_map,
        catalog,
        markers,
        search_bounds_en_m=tuple(args.bounds_en_m),
        chart_coverage_geometry=chart_geometry,
        chart_catalog_complete=args.assert_chart_catalog_complete,
        catalog_objects_cover_envelope=args.assert_catalog_objects_cover_envelope,
        catalog_object_identities_proven=args.assert_catalog_object_identities,
        catalog_position_uncertainty_m=args.catalog_position_uncertainty_m,
        catalog_exact_positions_asserted=args.assert_catalog_exact_positions,
        catalog_nominal_visible_offsets_asserted=args.assert_catalog_nominal_visible_offsets,
        minimum_distinct_markers=args.minimum_distinct_markers,
        visibility_radius_m=args.visibility_radius_m,
        certified_max_projection_scale=args.certified_max_projection_scale,
        certified_azimuth_error_deg=args.certified_azimuth_error_deg,
        device_ids=args.device_ids,
        matching_backend=args.matching_backend,
        output_path=args.output,
        initial_cell_size_m=args.initial_cell_size_m,
        target_cell_size_m=args.target_cell_size_m,
        batch_size=args.batch_size,
        max_evaluations=args.max_evaluations,
        progress=progress,
    )
    print(
        json.dumps(
            {
                "status": result.status,
                "result": str(args.output),
                "retained_terminal_cells": len(result.traversal.retained),
                "unprocessed_frontier": len(result.traversal.unprocessed),
                "center_score_errors": len(result.score_errors),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
