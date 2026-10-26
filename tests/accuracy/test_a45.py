from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest
from shapely.geometry import MultiPolygon, Polygon, box

from maritime_localization_shared.geospatial.vector_los import line_of_sight_clear
from maritime_localization_shared.geospatial.vector_map import VectorMap


def test_approved_candidate_map_bytes_match_manifest() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "data/maps/accuracy-inputs-v3/manifest.json").read_text())
    payload = manifest["map_payload"]
    archive = root / "data/maps/accuracy-inputs-v3" / payload["archive_filename"]
    archive_bytes = archive.read_bytes()
    raw_bytes = gzip.decompress(archive_bytes)

    assert hashlib.sha256(archive_bytes).hexdigest() == payload["archive_sha256"]
    assert len(raw_bytes) == payload["original_bytes"]
    assert hashlib.sha256(raw_bytes).hexdigest() == payload["original_sha256"]


def test_vector_map_indexed_and_unindexed_los_preserve_exact_geometry() -> None:
    coverage = box(0.0, 0.0, 10.0, 10.0)
    # The narrow opening is a genuine polygon hole that must remain water.
    land = box(2.0, 2.0, 8.0, 8.0).difference(box(4.9, 2.0, 5.1, 8.0))
    unknown = box(6.0, 6.0, 7.0, 7.0)
    vector_map = VectorMap(
        land,
        crs="EPSG:32618",
        coverage_geometry=coverage,
        unknown_geometry=unknown,
    )

    assert vector_map.point_water_status((1.0, 1.0)) == "water"
    assert vector_map.point_water_status((3.0, 3.0)) == "land"
    assert vector_map.point_water_status((5.0, 5.0)) == "water"
    assert vector_map.point_water_status((6.5, 6.5)) == "unknown"
    assert vector_map.point_water_status((10.0, 5.0)) == "water"
    assert vector_map.point_water_status((11.0, 5.0)) == "unknown"

    rays = (
        ((5.0, 1.0), (5.0, 9.0)),  # through the narrow water opening
        ((1.0, 1.0), (9.0, 9.0)),  # across known land
        ((1.0, 5.0), (2.0, 5.0)),  # ends on a shoreline
        ((1.0, 6.5), (9.0, 6.5)),  # known land precedes unknown coverage
    )
    for observer, target in rays:
        indexed = vector_map.line_visibility(observer, target, indexed=True)
        unindexed = vector_map.line_visibility(observer, target, indexed=False)
        assert indexed == unindexed

    assert vector_map.line_visibility((5.0, 1.0), (5.0, 9.0)) == "clear"
    assert vector_map.line_visibility((1.0, 1.0), (9.0, 9.0)) == "blocked"
    assert vector_map.line_visibility((1.0, 5.0), (2.0, 5.0)) == "clear"


def test_reference_los_keeps_shoreline_and_narrow_interval_rules() -> None:
    rectangle = ((2.0, -1.0), (4.0, -1.0), (4.0, 1.0), (2.0, 1.0))
    narrow_land = ((2.0, -1.0), (2.0005, -1.0), (2.0005, 1.0), (2.0, 1.0))

    assert line_of_sight_clear((0.0, 0.0), (2.0, 0.0), [rectangle])
    assert not line_of_sight_clear((0.0, 0.0), (6.0, 0.0), [rectangle])
    assert line_of_sight_clear(
        (0.0, 0.0),
        (6.0, 0.0),
        [narrow_land],
        tolerance_m=0.001,
    )
    assert not line_of_sight_clear(
        (0.0, 0.0),
        (6.0, 0.0),
        [narrow_land],
        tolerance_m=0.0001,
    )


def test_direct_single_polygon_and_multipolygon_fast_path_preserves_identity() -> None:
    import maritime_localization_shared.geospatial.vector_map as vector_map_module

    polygon = box(0.0, 0.0, 2.0, 2.0)
    multipolygon = MultiPolygon((box(4.0, 0.0, 6.0, 2.0), box(8.0, 0.0, 10.0, 2.0)))

    assert vector_map_module._polygonal_geometry(polygon, "geometry") is polygon
    assert vector_map_module._polygonal_geometry(multipolygon, "geometry") is multipolygon
    first = box(0.0, 0.0, 2.0, 2.0)
    second = box(1.0, 0.0, 3.0, 2.0)
    merged = vector_map_module._polygonal_geometry((first, second), "geometry")
    assert merged is not first
    assert merged is not None
    assert merged.area == 6.0


def test_single_geometry_fast_path_preserves_queries_and_sequence_normalization() -> None:
    coverage = box(0.0, 0.0, 10.0, 10.0)
    land = box(2.0, 2.0, 8.0, 8.0)
    unknown = box(6.0, 6.0, 7.0, 7.0)
    direct = VectorMap(
        land,
        crs="EPSG:32618",
        coverage_geometry=coverage,
        unknown_geometry=unknown,
    )
    sequence = VectorMap(
        (land,),
        crs="EPSG:32618",
        coverage_geometry=coverage,
        unknown_geometry=unknown,
    )

    points = ((1.0, 1.0), (3.0, 3.0), (6.5, 6.5), (10.0, 5.0), (11.0, 5.0))
    for point in points:
        assert direct.point_water_status(point) == sequence.point_water_status(point)
    rays = (((1.0, 1.0), (9.0, 9.0)), ((1.0, 5.0), (2.0, 5.0)))
    for observer, target in rays:
        assert direct.line_visibility(observer, target) == sequence.line_visibility(
            observer, target
        )


def test_polygonal_geometry_rejects_empty_and_invalid_direct_geometry() -> None:
    import maritime_localization_shared.geospatial.vector_map as vector_map_module

    with pytest.raises(ValueError, match="must not contain empty"):
        vector_map_module._polygonal_geometry(Polygon(), "geometry")
    with pytest.raises(ValueError, match="must not be empty"):
        vector_map_module._polygonal_geometry((), "geometry")
    invalid = Polygon(((0.0, 0.0), (1.0, 1.0), (1.0, 0.0), (0.0, 1.0)))
    with pytest.raises(ValueError, match="must contain valid"):
        vector_map_module._polygonal_geometry(invalid, "geometry")
