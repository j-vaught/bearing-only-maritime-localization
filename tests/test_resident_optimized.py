"""Opt-in integration keeps the baseline candidate-score contract."""

import numpy as np
import pytest
from pyproj import Geod, Transformer
from shapely.geometry import box

from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import CatalogRecordV2, CatalogV2
from maritime.search.gpu.resident.engine import ResidentConfig, ResidentDevice
from maritime.search.gpu.resident.optimized import OptimizedResidentDevice
from maritime.search.gpu.resident.preparation import prepare_world


@pytest.mark.parametrize("unknown", [False, True])
@pytest.mark.parametrize("tie_exact_order", [None, (1, 0), (0, 1)])
def test_parallel_score_matches_serial_baseline(unknown, tie_exact_order):
    cp = pytest.importorskip("cupy")
    if cp.cuda.runtime.getDeviceCount() <= 3:
        pytest.skip("GPU3 required to preserve the ongoing run")
    geod = Geod(ellps="WGS84")
    x, y = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform(-79.905, 32.77)
    rng = np.random.default_rng(1234)
    rows = []
    for i in range(48):
        lon, lat, _ = geod.fwd(
            -79.905, 32.77, float(rng.uniform(0.0, 360.0)), float(rng.uniform(200.0, 1500.0))
        )
        rows.append(CatalogRecordV2(f"aid-{i:03d}", lat, lon, normalized_type="physical_aid"))
    coverage = box(x - 30000, y - 30000, x + 30000, y + 30000)
    vm = VectorMap(
        None,
        crs="EPSG:32618",
        coverage_geometry=coverage,
        unknown_geometry=box(x + 100, y - 200, x + 150, y + 200) if unknown else None,
    )
    world = prepare_world(
        vm,
        CatalogV2(tuple(rows), "2023-07-15T12:00:30Z", "test", "1", "test"),
        chart_geometry=coverage,
        catalog_complete=True,
        catalog_objects_cover_envelope=True,
        identities_certified=True,
        exact_positions_asserted=True,
        nominal_offsets_asserted=True,
        max_projection_scale=2.0,
        azimuth_error_deg=30.0,
    )
    cfg = ResidentConfig(
        (x - 2, x + 2, y - 2, y + 2),
        (3,),
        object_capacity=64,
        trusted_marker_labels=True,
        hard_bearing_bounds_asserted=True,
        numerical_validation_accepted=True,
    )
    baseline = ResidentDevice(world, cfg, 3)
    optimized = OptimizedResidentDevice(world, cfg, 3)
    if tie_exact_order is not None:
        from types import MethodType

        def fixed_prediction(self, name, n, args):
            if name != "predict_groups":
                return ResidentDevice._launch(self, name, n, args)
            for destination in args[20:24]:
                destination.fill(0)
            args[20][:, 0], args[20][:, 1] = 89.0, 91.0
            args[22][:, 0], args[22][:, 1] = 0, 1
            args[23][:, 0], args[23][:, 1] = tie_exact_order
            args[24].fill(2)
            args[25].fill(0)
            args[26].fill(0)

        baseline._launch = MethodType(fixed_prediction, baseline)
        optimized._launch = MethodType(fixed_prediction, optimized)
    with cp.cuda.Device(3):
        cells = cp.asarray(
            [[x - 1, y - 1, x + 1, y + 1], [x, y, x + 2, y + 2], [x - 2, y - 2, x, y]]
        )
        obs = cp.asarray([0.0, 90.0, 180.0, 270.0])
        tol = cp.full(4, 8.0)
        if tie_exact_order is not None:
            obs, tol = cp.asarray([90.0]), cp.asarray([2.0])
        with baseline.stream:
            old = baseline.score(cells, obs, tol)
            baseline.stream.synchronize()
        with optimized.stream:
            new = optimized.score(cells, obs, tol)
            optimized.stream.synchronize()
        if tie_exact_order is not None and not unknown:
            expected_status = 0 if tie_exact_order[0] else 2
            assert cp.asnumpy(old["status"]).tolist() == [expected_status] * 3
        for key in ("status", "flags", "matched_records"):
            np.testing.assert_array_equal(cp.asnumpy(old[key]), cp.asnumpy(new[key]))
        np.testing.assert_allclose(
            cp.asnumpy(old["residual_deg"]),
            cp.asnumpy(new["residual_deg"]),
            atol=1e-8,
            rtol=0,
            equal_nan=True,
        )
