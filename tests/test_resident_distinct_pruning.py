"""Synthetic whole-cell graph witnesses and exact injective GPU pruning checks.

The C++ harness compiles the production matching routine with a one-lane test
adapter; production calls always execute CUDA. GPU cases use GPU0 only and never
invoke search, profiling, real catalogs, or cache preparation.
"""

import ctypes
import inspect
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from pyproj import Geod, Transformer
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import maximum_bipartite_matching
from shapely.geometry import box

from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import CatalogRecordV2, CatalogV2
from maritime.search.gpu.resident import distinct_pruning
from maritime.search.gpu.resident.chart_pruning import (
    _source,
    prune_chart_geometry,
    prune_distinct_chart_geometry,
    solve_distinct_chart_graph,
)
from maritime.search.gpu.resident.engine import ResidentConfig, ResidentDevice
from maritime.search.gpu.resident.preparation import prepare_world

PROJECT = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True)
INVERSE = Transformer.from_crs("EPSG:32618", "EPSG:4326", always_xy=True)
GEOD = Geod(ellps="WGS84")
ORIGIN = PROJECT.transform(-79.905, 32.77)


def _packed(edges):
    rows, columns = edges.shape
    packed = np.zeros((columns, (rows + 31) // 32), dtype=np.uint32)
    for row, column in zip(*np.nonzero(edges), strict=True):
        packed[column, row // 32] |= np.uint32(1) << np.uint32(row % 32)
    return packed


@pytest.fixture(scope="module")
def compiled_matcher(tmp_path_factory):
    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("The shared-source CPU matcher check requires a C++ compiler.")
    directory = tmp_path_factory.mktemp("distinct-host-check")
    source = Path(distinct_pruning.__file__).with_suffix(".cu")
    harness = directory / "matcher.cpp"
    library = directory / "matcher.so"
    harness.write_text(
        "#include <math.h>\n#include <vector>\n"
        "#define RESIDENT_DISTINCT_HOST_TEST 1\n"
        f'#include "{source}"\n'
        'extern "C" int solve_graph(const unsigned int* graph,int rows,int cols,'
        "int ids,int* matched) {"
        "int words=(rows+31)/32;std::vector<int> owner(cols),parent(cols),rm(rows);"
        "std::vector<unsigned int> front(3*words);"
        "return distinct_match_graph(graph,rows,cols,words,ids,0,owner.data(),parent.data(),"
        "rm.data(),front.data(),front.data()+words,front.data()+2*words,matched);}\n"
        'extern "C" int physical_edge(double angle,double extent,double report,double tolerance) {'
        "return distinct_physical_edge(angle,0.,extent,report,tolerance,0.);}\n"
    )
    subprocess.run(
        [compiler, "-shared", "-fPIC", "-O2", "-std=c++11", str(harness), "-o", str(library)],
        check=True,
        capture_output=True,
        text=True,
    )
    loaded = ctypes.CDLL(str(library))
    loaded.solve_graph.argtypes = [
        np.ctypeslib.ndpointer(dtype=np.uint32, flags="C_CONTIGUOUS"),
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
    ]
    loaded.solve_graph.restype = ctypes.c_int
    loaded.physical_edge.argtypes = [ctypes.c_double] * 4
    loaded.physical_edge.restype = ctypes.c_int
    return loaded


def _host_match(compiled_matcher, edges, *, identities=True):
    matched = ctypes.c_int(-1)
    reason = compiled_matcher.solve_graph(
        _packed(edges), *edges.shape, int(identities), ctypes.byref(matched)
    )
    return reason, matched.value


def test_shared_matcher_detects_hall_deficiency_with_individually_supported_reports(
    compiled_matcher,
):
    graph = np.array([[1, 0, 0], [1, 0, 0], [0, 1, 1]], dtype=bool)
    assert graph.any(axis=1).all() and graph.shape[1] >= graph.shape[0]
    assert _host_match(compiled_matcher, graph)[0] == 4


def test_shared_matcher_finds_injection_requiring_reassignment(compiled_matcher):
    graph = np.array([[1, 1, 0], [1, 0, 0], [0, 1, 1]], dtype=bool)
    assert _host_match(compiled_matcher, graph) == (0, 3)


def test_shared_matcher_matches_independent_maximum_matching_reference(compiled_matcher):
    rng = np.random.default_rng(20261029)
    for rows in range(10):
        for columns in range(11):
            for _ in range(3):
                edges = rng.random((rows, columns)) < 0.3
                reason, matched = _host_match(compiled_matcher, edges)
                reference = maximum_bipartite_matching(csr_matrix(edges), perm_type="column")
                possible = np.count_nonzero(reference >= 0) == rows
                assert (reason == 0) == possible
                if possible:
                    assert matched == rows
    # Cover all eight report words, including the sign bit of the last word.
    assert _host_match(compiled_matcher, np.eye(256, dtype=bool)) == (0, 256)


def test_shared_matcher_preserves_identity_gate_and_individual_proofs(compiled_matcher):
    hall = np.array([[1, 0, 0], [1, 0, 0], [0, 1, 1]], dtype=bool)
    assert _host_match(compiled_matcher, hall, identities=False)[0] == -1
    hall[2] = False
    assert _host_match(compiled_matcher, hall, identities=False)[0] == 2
    assert _host_match(compiled_matcher, np.ones((3, 2), dtype=bool))[0] == 3


def test_group_extent_gap_does_not_create_a_physical_positive_edge(compiled_matcher):
    report, tolerance = 359.85, 0.01
    object_bearings = [0.0, 0.1, 0.2]
    nominal_group_extent = 0.2
    nominal_residual = abs((report + 180) % 360 - 180)
    assert nominal_residual <= nominal_group_extent + tolerance
    assert not any(
        compiled_matcher.physical_edge(angle, 0.0, report, tolerance) for angle in object_bearings
    )


def test_source_assembly_and_runtime_have_no_host_numeric_fallback():
    for true_bearings in (False, True):
        source = _source(true_bearings)
        assert source.count("void gather_distinct_chart_objects(") == 1
        assert source.count("void build_distinct_chart_incidence(") == 1
        assert source.count("void match_distinct_chart_incidence(") == 1
        assert f"#define DISTINCT_TRUE_BEARINGS {int(true_bearings)}" in source
    source = inspect.getsource(distinct_pruning)
    for prohibited in (".item(", "asnumpy(", ".get(", "scipy", "_host_match"):
        assert prohibited not in source


def _record(key, bearing, distance=500.0, *, kind="physical_aid", origin=ORIGIN, metadata=()):
    lon, lat = INVERSE.transform(*origin)
    longitude, latitude, _ = GEOD.fwd(lon, lat, bearing, distance)
    return CatalogRecordV2(key, latitude, longitude, normalized_type=kind, metadata=tuple(metadata))


def _gpu_resident(
    records,
    *,
    capacity=32,
    budget=2_000_000_000,
    exact=True,
    complete=True,
    identities=True,
    origin=ORIGIN,
    land=None,
):
    cp = pytest.importorskip("cupy")
    try:
        if cp.cuda.runtime.getDeviceCount() < 1:
            pytest.skip("GPU0 unavailable")
    except cp.cuda.runtime.CUDARuntimeError:
        pytest.skip("CUDA runtime unavailable")
    x, y = origin
    catalog = CatalogV2(tuple(records), "2023-07-15T12:00:30Z", "synthetic", "1", "distinct-test")
    world = prepare_world(
        VectorMap(
            land,
            crs="EPSG:32618",
            coverage_geometry=box(x - 100000, y - 100000, x + 100000, y + 100000)
            if land is not None
            else None,
        ),
        catalog,
        chart_geometry=box(x - 100000, y - 100000, x + 100000, y + 100000),
        catalog_complete=complete,
        catalog_objects_cover_envelope=complete,
        identities_certified=identities,
        exact_positions_asserted=exact,
        nominal_offsets_asserted=True,
        max_projection_scale=2.0,
        azimuth_error_deg=30.0,
    )
    config = ResidentConfig(
        (x - 1, x + 1, y - 1, y + 1),
        (0,),
        radius_m=1000.0,
        object_capacity=capacity,
        device_workspace_budget_bytes=budget,
        trusted_marker_labels=True,
        hard_bearing_bounds_asserted=True,
        numerical_validation_accepted=True,
    )
    return cp, ResidentDevice(world, config, 0)


def _gpu_prune(cp, resident, bearings, *, tolerance=0.01, side=0.01, origin=ORIGIN):
    x, y = origin
    with cp.cuda.Device(0), resident.stream:
        cells = cp.asarray(
            [[x - side / 2, y - side / 2, x + side / 2, y + side / 2]], dtype=cp.float64
        )
        observations = cp.asarray(bearings, dtype=cp.float64)
        tolerances = cp.full(len(bearings), tolerance, dtype=cp.float64)
        details = prune_distinct_chart_geometry(
            resident, cells, observations, tolerances, return_details=True
        )
        resident.stream.synchronize()
        return details, cells, observations, tolerances


def test_gpu_hall_deficiency_adds_proof_beyond_individual_support_and_count():
    records = [_record("north", 0.0), _record("east-a", 90.0), _record("east-b", 90.0, 700.0)]
    cp, resident = _gpu_resident(records)
    details, cells, obs, tols = _gpu_prune(cp, resident, [0.0, 0.001, 90.0])
    with cp.cuda.Device(0), resident.stream:
        legacy = prune_chart_geometry(resident, cells, obs, tols, distinct_markers=False)
        resident.stream.synchronize()
        assert int(legacy[0].item()) == 0
        assert int(details["states"][0].item()) == 4
        assert int(details["graph"]["counts"][0].item()) == 3
        assert bool(details["proof_valid"][0].item())


def test_gpu_possible_injection_and_cell_corner_witness_are_retained():
    records = [_record("north-a", 0.0), _record("north-b", 0.001, 700.0), _record("east", 90.0)]
    cp, resident = _gpu_resident(records)
    details, *_ = _gpu_prune(cp, resident, [0.0, 0.001, 90.0])
    assert int(details["states"][0].item()) == 0
    assert int(details["matched_counts"][0].item()) == 3
    # Reports originate at a corner, rather than the cell center.
    side = 200.0
    x, y = ORIGIN
    lon, lat = INVERSE.transform(x - side / 2, y + side / 2)
    bearings = [GEOD.inv(lon, lat, row.longitude_deg, row.latitude_deg)[0] % 360 for row in records]
    details, *_ = _gpu_prune(cp, resident, bearings, side=side)
    assert int(details["states"][0].item()) == 0


@pytest.mark.parametrize(
    "case", ["object_capacity", "workspace", "unknown_position", "chart_scope"]
)
def test_gpu_capacity_and_unknown_bounds_retain_without_truncation(case):
    records = [_record("north", 0.0), _record("east-a", 90.0), _record("east-b", 90.0, 700.0)]
    cp, resident = _gpu_resident(
        records,
        capacity=2 if case == "object_capacity" else 32,
        budget=1 if case == "workspace" else 2_000_000_000,
        exact=case != "unknown_position",
        complete=case != "chart_scope",
    )
    details, *_ = _gpu_prune(cp, resident, [0.0, 0.001, 90.0])
    assert int(details["states"][0].item()) == 0
    assert not bool(details["proof_valid"][0].item())
    assert int(details["flags"][0].item()) & (2 if case in ("object_capacity", "workspace") else 1)


def test_gpu_missing_identity_certificate_keeps_individual_but_disables_hall():
    records = [_record("north", 0.0), _record("east-a", 90.0), _record("east-b", 90.0, 700.0)]
    cp, resident = _gpu_resident(records, identities=False)
    details, *_ = _gpu_prune(cp, resident, [0.0, 0.001, 90.0])
    assert int(details["states"][0].item()) == 0
    assert int(details["flags"][0].item()) == 1
    details, *_ = _gpu_prune(cp, resident, [180.0])
    assert int(details["states"][0].item()) == 2
    assert bool(details["proof_valid"][0].item())


def test_gpu_unsupported_true_bearing_domain_retains():
    origin = (2_000_000.0, 3_600_000.0)
    cp, resident = _gpu_resident([_record("aid", 0.0, origin=origin)], origin=origin)
    details, *_ = _gpu_prune(cp, resident, [180.0], origin=origin)
    assert int(details["states"][0].item()) == 0
    assert int(details["flags"][0].item()) & 4


def test_gpu_unknown_kind_is_a_possible_marker_and_group_extent_gap_is_not():
    cp, resident = _gpu_resident([_record("unknown", 0.0, kind="unknown")])
    details, *_ = _gpu_prune(cp, resident, [0.0])
    assert int(details["states"][0].item()) == 0
    records = [_record(f"aid-{i}", angle) for i, angle in enumerate([0.0, 0.1, 0.2])]
    cp, resident = _gpu_resident(records)
    details, *_ = _gpu_prune(cp, resident, [359.85])
    assert int(details["states"][0].item()) == 2


def test_gpu_chart_only_certificate_overrides_vessel_unknown_support():
    records = [
        _record("aid", 0.0, metadata=(("position_uncertainty_m", 0.0),)),
        _record("vessel", 90.0, kind="vessel"),
    ]
    cp, resident = _gpu_resident(records, exact=False)
    # Parent supplies these marker-only setup certificates on optimized devices.
    resident.chart_position_support_complete = True
    resident.marker_identities_complete = True
    resident.marker_proofs = True
    resident.max_uncertainty = 0.0
    details, *_ = _gpu_prune(cp, resident, [180.0])
    assert int(details["states"][0].item()) == 2
    cp, resident = _gpu_resident([_record("unknown", 0.0, kind="unknown")], exact=False)
    resident.chart_position_support_complete = False
    resident.marker_proofs = True
    details, *_ = _gpu_prune(cp, resident, [180.0])
    assert int(details["states"][0].item()) == 0


def test_gpu_nonzero_target_uncertainty_does_not_use_nominal_land_barrier():
    x, y = ORIGIN
    land = box(x + 200, y - 1000, x + 300, y + 1000)
    aid = _record("east", 90.0, metadata=(("position_uncertainty_m", 800.0),))
    cp, resident = _gpu_resident([aid], exact=False, land=land)
    details, *_ = _gpu_prune(cp, resident, [90.0])
    assert int(details["states"][0].item()) == 0
    assert int(details["graph"]["counts"][0].item()) == 1


@pytest.mark.parametrize("reports", [1, 3, 33, 65, 128, 256])
def test_gpu_cooperative_matcher_agrees_with_independent_graph_reference(reports):
    cp, resident = _gpu_resident([])
    rng = np.random.default_rng(20261029 + reports)
    columns = reports + 7
    edges = rng.random((8, reports, columns)) < min(0.5, 3 / reports)
    # Ensure complete witnesses at all bitset sizes and a clean Hall violation.
    edges[0] = False
    edges[0, np.arange(reports), np.arange(reports)] = True
    if reports > 1:
        edges[1] = False
        edges[1, :2, 0] = True
        for row in range(2, reports):
            edges[1, row, row] = True
    expected = [
        np.count_nonzero(maximum_bipartite_matching(csr_matrix(graph), perm_type="column") >= 0)
        == reports
        for graph in edges
    ]
    with cp.cuda.Device(0), resident.stream:
        graph = dict(
            incidence=cp.asarray(np.asarray([_packed(e) for e in edges])),
            object_ids=cp.zeros((len(edges), columns), dtype=cp.int32),
            counts=cp.full(len(edges), columns, dtype=cp.int32),
            flags=cp.zeros(len(edges), dtype=cp.int32),
            base_states=cp.zeros(len(edges), dtype=cp.int32),
            identity_proofs=True,
            reports=reports,
            workspace_bytes=1_000_000,
        )
        result = solve_distinct_chart_graph(resident, graph)
        resident.stream.synchronize()
        np.testing.assert_array_equal(cp.asnumpy(result["feasible"]) == 1, expected)
        assert bool(cp.all(result["flags"] == 0).item())


def test_gpu_reviewed_projected_mode_and_safe_zero_uncertainty_barrier():
    records = [_record("north", 0.0), _record("east-a", 90.0), _record("east-b", 90.0, 700.0)]
    cp, resident = _gpu_resident(records)
    _, cells, obs, tols = _gpu_prune(cp, resident, [0.0, 0.001, 90.0])
    with cp.cuda.Device(0), resident.stream:
        details = prune_distinct_chart_geometry(
            resident, cells, obs, tols, true_bearings=False, return_details=True
        )
        resident.stream.synchronize()
        assert int(details["states"][0].item()) == 4
    x, y = ORIGIN
    cp, resident = _gpu_resident(
        [_record("east", 90.0)], land=box(x + 200, y - 1000, x + 300, y + 1000)
    )
    details, *_ = _gpu_prune(cp, resident, [90.0])
    assert int(details["states"][0].item()) == 2


def test_gpu_report_capacity_is_retained_without_truncating_observations():
    cp, resident = _gpu_resident([_record("aid", 0.0)])
    details, *_ = _gpu_prune(cp, resident, np.zeros(257))
    assert int(details["states"][0].item()) == 0
    assert int(details["flags"][0].item()) & 2


@pytest.mark.parametrize("strided_name", ["cells", "bearings", "tolerances"])
def test_gpu_strided_inputs_are_rejected_before_kernel_launch(strided_name):
    cp, resident = _gpu_resident([_record("a", 0.0), _record("b", 0.001, 700.0)])
    details, cells, bearings, tolerances = _gpu_prune(cp, resident, [0.0, 0.001])
    assert int(details["states"][0].item()) == 0
    with cp.cuda.Device(0), resident.stream:
        values = dict(cells=cells, bearings=bearings, tolerances=tolerances)
        if strided_name == "cells":
            values[strided_name] = cp.repeat(cells, 2, axis=1)[:, ::2]
        else:
            values[strided_name] = cp.repeat(values[strided_name], 2)[::2]
        assert not values[strided_name].flags.c_contiguous
        with pytest.raises(ValueError, match="C-contiguous"):
            prune_chart_geometry(resident, **values)


def test_gpu_wrong_device_array_is_rejected_before_kernel_launch():
    cp, resident = _gpu_resident([_record("a", 0.0)])
    if cp.cuda.runtime.getDeviceCount() < 2:
        pytest.skip("Two devices are required for wrong-device input validation.")
    _, cells, bearings, tolerances = _gpu_prune(cp, resident, [0.0])
    with cp.cuda.Device(1):
        foreign_bearings = cp.asarray([0.0], dtype=cp.float64)
    with cp.cuda.Device(0), resident.stream:
        with pytest.raises(ValueError, match="bearings must reside on resident.device"):
            prune_chart_geometry(resident, cells, foreign_bearings, tolerances)


def test_gpu_wrong_current_device_is_rejected_before_kernel_launch():
    cp, resident = _gpu_resident([_record("a", 0.0)])
    if cp.cuda.runtime.getDeviceCount() < 2:
        pytest.skip("Two devices are required for current-context validation.")
    _, cells, bearings, tolerances = _gpu_prune(cp, resident, [0.0])
    with cp.cuda.Device(1):
        with pytest.raises(ValueError, match="resident.device current"):
            prune_chart_geometry(resident, cells, bearings, tolerances)


@pytest.mark.parametrize(
    "strided_name", ["incidence", "object_ids", "counts", "flags", "base_states"]
)
def test_gpu_public_solver_rejects_strided_graph_arrays(strided_name):
    cp, resident = _gpu_resident([_record("a", 0.0), _record("b", 0.001, 700.0)])
    _, cells, bearings, tolerances = _gpu_prune(cp, resident, [0.0, 0.001])
    with cp.cuda.Device(0), resident.stream:
        graph = distinct_pruning.build_distinct_chart_graph(
            resident, cp.repeat(cells, 3, axis=0), bearings, tolerances
        )
        value = graph[strided_name]
        axis = value.ndim - 1
        selector = (slice(None),) * axis + (slice(None, None, 2),)
        graph[strided_name] = cp.repeat(value, 2, axis=axis)[selector]
        assert not graph[strided_name].flags.c_contiguous
        with pytest.raises(ValueError, match="C-contiguous"):
            solve_distinct_chart_graph(resident, graph)


@pytest.mark.parametrize("count", [-1, 3])
def test_gpu_public_solver_invalid_counts_retain_without_column_reads(count):
    cp, resident = _gpu_resident([_record("a", 0.0), _record("b", 0.001, 700.0)])
    _, cells, bearings, tolerances = _gpu_prune(cp, resident, [0.0, 0.001])
    with cp.cuda.Device(0), resident.stream:
        graph = distinct_pruning.build_distinct_chart_graph(resident, cells, bearings, tolerances)
        graph["counts"].fill(count)
        result = solve_distinct_chart_graph(resident, graph)
        resident.stream.synchronize()
        assert int(result["flags"][0].item()) & 4
        assert int(result["feasible"][0].item()) == -1
        assert not bool(result["proof_valid"][0].item())


def test_gpu_public_solver_validates_dtype_shape_and_device_contract():
    cp, resident = _gpu_resident([_record("a", 0.0)])
    _, cells, bearings, tolerances = _gpu_prune(cp, resident, [0.0])
    with cp.cuda.Device(0), resident.stream:
        graph = distinct_pruning.build_distinct_chart_graph(resident, cells, bearings, tolerances)
        invalid = dict(graph, counts=graph["counts"].astype(cp.int64))
        with pytest.raises(TypeError, match="counts must be a device array with dtype"):
            solve_distinct_chart_graph(resident, invalid)
        invalid = dict(graph, flags=cp.zeros((1, 1), dtype=cp.int32))
        with pytest.raises(ValueError, match="flags must have shape"):
            solve_distinct_chart_graph(resident, invalid)
    if cp.cuda.runtime.getDeviceCount() >= 2:
        with cp.cuda.Device(1):
            foreign_flags = cp.zeros(1, dtype=cp.int32)
            with pytest.raises(ValueError, match="resident.device current"):
                solve_distinct_chart_graph(resident, graph)
        with cp.cuda.Device(0):
            with pytest.raises(ValueError, match="flags must reside on resident.device"):
                solve_distinct_chart_graph(resident, dict(graph, flags=foreign_flags))
