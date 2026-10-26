from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from shapely.geometry import box

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
