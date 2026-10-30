"""Adversarial checks for conservative GPU land-blocking region pruning."""

from __future__ import annotations

import pytest
from shapely.geometry import Polygon, box

from maritime.geometry.vector_map import VectorMap
from maritime.search.gpu.region_land_visibility import (
    classify_land_blocking_cupy,
    derive_certified_land_barrier_tiles,
)
from maritime.search.gpu.region_pruning import (
    MarkerDetection,
    RegionCell,
    RegionEvidence,
    RegionObject,
)


def gpu_or_skip():
    cp = pytest.importorskip("cupy")
    try:
        if cp.cuda.runtime.getDeviceCount() < 1:
            pytest.skip("CUDA device unavailable")
    except Exception as exc:  # pragma: no cover - host dependent
        pytest.skip(f"CUDA runtime unavailable: {exc}")
    return cp


def _evidence(*objects, **overrides):
    fields = {
        "chart_coverage_complete": True,
        "land_fully_covers": False,
        "map_evidence_covers_visibility_envelope": True,
        "projection_reliable": True,
        "numeric_uncertainty": False,
        "objects": tuple(objects),
        "certified_max_projection_scale": 1.0,
    }
    fields.update(overrides)
    return RegionEvidence(**fields)


def _classify(cell, detections, evidence, vector_map, cp, *, masks=None, barriers=()):
    return classify_land_blocking_cupy(
        (cell,),
        detections,
        (evidence,),
        vector_map,
        compatible_object_masks=None if masks is None else (masks,),
        certified_land_barriers_en_m=barriers,
        cupy_module=cp,
        device_id=0,
    )[0]


def _map(land, *, coverage=None, unknown=None):
    return VectorMap(
        land,
        crs="EPSG:32618",
        coverage_geometry=box(-100, -100, 110, 100) if coverage is None else coverage,
        unknown_geometry=unknown,
    )


def test_thick_rectangle_blocks_every_ray_and_center_block_is_not_enough():
    cp = gpu_or_skip()
    cell = RegionCell((0, 1, 0, 1))
    target = RegionObject("aid", 20, 0.5, identity_proven=True, exact_position_asserted=True)
    det = (MarkerDetection(90, 0.1),)

    # A thick separator blocks every observer-to-target segment.
    land = _map(box(8, -20, 12, 20))
    assert _classify(cell, det, _evidence(target), land, cp).blocked is True

    # This small island blocks the center ray, while the cell's east edge can
    # see around it. The classifier must preserve the cell.
    island = _map(box(9.8, 0.4, 10.2, 0.6))
    assert island.line_visibility((0.5, 0.5), (20, 0.5)) == "blocked"
    assert island.line_visibility((1, 0), (20, 0.5)) == "clear"
    assert _classify(cell, det, _evidence(target), island, cp).blocked is False


@pytest.mark.parametrize(
    "land,target_xy",
    [
        (box(8, -20, 12, 20), (8, 0.5)),  # target on shoreline is endpoint-clear
        (box(8, 0, 12, 1), (20, 0)),  # ray follows the rectangle boundary
    ],
)
def test_shoreline_touch_or_target_endpoint_does_not_certify_blocking(land, target_xy):
    cp = gpu_or_skip()
    cell = RegionCell((0, 1, 0, 1))
    vm = _map(land)
    obj = RegionObject("aid", *target_xy, identity_proven=True, exact_position_asserted=True)
    # Explicit scalar LOS witness validates the intended geometry convention.
    assert vm.line_visibility((0.5, target_xy[1]), target_xy) == "clear"
    assert _classify(cell, (MarkerDetection(90, 1),), _evidence(obj), vm, cp).blocked is False


def test_hole_or_channel_keeps_a_clear_witness_ray():
    cp = gpu_or_skip()
    cell = RegionCell((0, 1, 0, 1))
    from shapely.ops import unary_union

    # A navigable channel crosses the full land barrier.
    land = unary_union((box(8, -10, 12, -2), box(8, 2, 12, 10)))
    vm = _map(land)
    target = RegionObject("aid", 20, 0.5, identity_proven=True, exact_position_asserted=True)
    assert vm.line_visibility((0.5, 0.5), (20, 0.5)) == "clear"
    assert _classify(cell, (MarkerDetection(90, 1),), _evidence(target), vm, cp).blocked is False


def test_unknown_coverage_and_inexact_positions_never_prune():
    cp = gpu_or_skip()
    cell = RegionCell((0, 1, 0, 1))
    det = (MarkerDetection(90, 1),)
    target = RegionObject("aid", 20, 0.5, identity_proven=True, exact_position_asserted=True)
    land = box(8, -20, 12, 20)
    partial = _map(land, coverage=box(-100, -100, 10, 100))
    assert (
        _classify(
            cell, det, _evidence(target, map_evidence_covers_visibility_envelope=False), partial, cp
        ).blocked
        is False
    )
    unknown = _map(land, unknown=box(9, -2, 11, 2))
    assert _classify(cell, det, _evidence(target), unknown, cp).blocked is False
    inexact = RegionObject("aid", 20, 0.5, position_uncertainty_m=0.1, identity_proven=True)
    assert _classify(cell, det, _evidence(inexact), _map(land), cp).blocked is False


def test_multiple_objects_require_all_blocked_and_no_detections_never_penalize():
    cp = gpu_or_skip()
    cell = RegionCell((0, 1, 0, 1))
    det = (MarkerDetection(90, 1), MarkerDetection(270, 1))
    land = _map(box(8, -20, 12, 20))
    blocked = RegionObject("blocked", 20, 0.5, identity_proven=True, exact_position_asserted=True)
    clear = RegionObject("clear", -20, 0.5, identity_proven=True, exact_position_asserted=True)
    assert _classify(cell, det, _evidence(blocked), land, cp).blocked is True
    assert _classify(cell, det, _evidence(blocked, clear), land, cp).blocked is False
    assert _classify(cell, (), _evidence(blocked), land, cp).blocked is False


def test_multiple_cells_gpu_results_match_scalar_los_universal_witnesses():
    cp = gpu_or_skip()
    cells = (RegionCell((0, 1, 0, 1)), RegionCell((0, 1, -1, 0)))
    obj = RegionObject("aid", 20, 0.5, identity_proven=True, exact_position_asserted=True)
    det = (MarkerDetection(90, 1),)
    vm = _map(box(8, -20, 12, 20))
    expected = []
    for cell in cells:
        x0, x1, y0, y1 = cell.bounds_en_m
        # Dense boundary/interior witnesses catch a classifier that reasons
        # only from the cell center. Any clear VectorMap ray forbids pruning.
        witnesses = [
            (x0 + (x1 - x0) * i / 4, y0 + (y1 - y0) * j / 4) for i in range(5) for j in range(5)
        ]
        expected.append(
            all(vm.line_visibility(p, (obj.east_m, obj.north_m)) == "blocked" for p in witnesses)
        )
    actual = classify_land_blocking_cupy(
        cells, det, (_evidence(obj), _evidence(obj)), vm, cupy_module=cp, device_id=0
    )
    assert tuple(decision.blocked for decision in actual) == tuple(expected)


@pytest.mark.parametrize("width", [0.5e-6, 1.0e-6])
def test_sub_tolerance_land_crossing_matches_vector_map_clear(width):
    cp = gpu_or_skip()
    cell = RegionCell((0, 1, 0, 1))
    vm = _map(box(10, -20, 10 + width, 20))
    obj = RegionObject("aid", 20, 0.5, identity_proven=True, exact_position_asserted=True)
    assert vm.line_visibility((0.5, 0.5), (20, 0.5), tolerance_m=1e-6) == "clear"
    assert _classify(cell, (MarkerDetection(90, 1),), _evidence(obj), vm, cp).blocked is False


def test_compatibility_masks_prune_only_when_all_options_for_one_detection_are_blocked():
    cp = gpu_or_skip()
    cell = RegionCell((0, 1, 0, 1))
    vm = _map(box(8, -20, 12, 20))
    objects = (
        RegionObject("east", 20, 0.5, identity_proven=True, exact_position_asserted=True),
        RegionObject("west", -20, 0.5, identity_proven=True, exact_position_asserted=True),
    )
    detections = (MarkerDetection(90, 1), MarkerDetection(270, 1))
    evidence = _evidence(*objects)

    # Detection zero still has an unblocked compatible alternative.
    partial = _classify(cell, detections, evidence, vm, cp, masks=((True, True), (False, False)))
    assert partial.blocked is False

    # Detection one has an empty compatibility row; detection two has exactly
    # one compatible object, and the barrier blocks it from the entire cell.
    certified = _classify(cell, detections, evidence, vm, cp, masks=((False, False), (True, False)))
    assert certified.blocked is True
    assert certified.detection_index == 1
    assert certified.blocked_object_indices == (0,)


def test_projection_and_numeric_ambiguity_are_unresolved():
    cp = gpu_or_skip()
    cell = RegionCell((0, 1, 0, 1))
    vm = _map(box(8, -20, 12, 20))
    obj = RegionObject("aid", 20, 0.5, identity_proven=True, exact_position_asserted=True)
    detection = (MarkerDetection(90, 1),)
    for overrides in (
        {"projection_reliable": False},
        {"numeric_uncertainty": True},
        {"chart_coverage_complete": None},
    ):
        decision = _classify(cell, detection, _evidence(obj, **overrides), vm, cp)
        assert decision.blocked is False


def test_randomized_observer_witnesses_agree_with_gpu_certificates():
    import random

    cp = gpu_or_skip()
    rng = random.Random(2031)
    target = RegionObject("aid", 30, 0, identity_proven=True, exact_position_asserted=True)
    detection = (MarkerDetection(90, 2),)
    for _ in range(8):
        x0 = rng.uniform(-10, 0)
        x1 = rng.uniform(x0 + 0.1, 1)
        y0 = rng.uniform(-5, -2)
        y1 = rng.uniform(2, 5)
        cell = RegionCell((x0, x1, -0.5, 0.5))
        vm = _map(box(rng.uniform(5, 8), y0, rng.uniform(10, 15), y1))
        decision = _classify(cell, detection, _evidence(target), vm, cp)
        witnesses = [(rng.uniform(x0, x1), rng.uniform(-0.5, 0.5)) for _ in range(100)]
        los = tuple(
            vm.line_visibility(point, (target.east_m, target.north_m)) for point in witnesses
        )
        # CPU VectorMap witnesses are an independent soundness oracle: a GPU
        # certificate cannot coexist with even one clear or unknown witness.
        assert decision.blocked is True
        assert all(status == "blocked" for status in los)


def test_derived_tiles_stay_inside_nonrectangular_known_covered_land():
    outer = box(0, 0, 12, 12)
    hole = box(4, 4, 8, 8)
    land = Polygon(outer.exterior.coords, holes=[hole.exterior.coords])
    unknown = box(1, 1, 3, 11)
    coverage = box(0, 0, 10, 12)
    vm = _map(land, coverage=coverage, unknown=unknown)

    tiles = derive_certified_land_barrier_tiles(
        vm,
        (0.5, 9.5, 0.5, 11.5),
        tile_size_m=1,
        max_tiles=99,
    )

    assert tiles
    for x0, x1, y0, y1 in tiles:
        tile = box(x0, y0, x1, y1)
        assert vm.land_geometry.contains(tile)
        assert vm.land_geometry.boundary.disjoint(tile)
        assert coverage.covers(tile)
        assert hole.disjoint(tile)
        assert unknown.disjoint(tile)


def test_derived_tile_grid_rejects_max_tiles_overflow():
    vm = _map(box(0, 0, 10, 10))
    with pytest.raises(ValueError, match="max_tiles"):
        derive_certified_land_barrier_tiles(
            vm,
            (0, 10, 0, 10),
            tile_size_m=1,
            max_tiles=99,
        )
