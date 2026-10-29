"""Event accounting uses fake streams; the optional GPU check uses two cells."""

import json
import os
import pstats
from pathlib import Path
from threading import Thread, get_ident
from types import SimpleNamespace

import numpy as np
import pytest

from maritime.search.gpu.resident import profiling
from maritime.search.gpu.resident.profiling import DeviceProfileCollector, ProfiledResidentDevice


class FakeStream:
    def __init__(self):
        self.time_ms = 0.0
        self.completed_ms = -1.0
        self.synchronizations = 0

    def synchronize(self):
        self.completed_ms = self.time_ms
        self.synchronizations += 1


class FakeEvent:
    def __init__(self):
        self.stream = None
        self.time_ms = None

    def record(self, stream):
        self.stream, self.time_ms = stream, stream.time_ms

    @property
    def done(self):
        assert self.stream is not None
        return self.time_ms <= self.stream.completed_ms


class FakeDevice:
    def __init__(self, device):
        self.device = device

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def fake_cp():
    return SimpleNamespace(
        cuda=SimpleNamespace(
            Event=FakeEvent,
            Device=FakeDevice,
            get_elapsed_time=lambda begin, end: end.time_ms - begin.time_ms,
        )
    )


def rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def test_nested_exclusive_accounting_and_flush_do_not_add_sync(tmp_path, monkeypatch):
    host_clock = [10.0]
    monkeypatch.setattr(profiling, "perf_counter", lambda: host_clock[0])
    stream = FakeStream()
    collector = DeviceProfileCollector(fake_cp(), stream, 1, tmp_path)
    collector.begin_batch()
    with collector.span("stage.score", "stage", {"cells": 2}):
        stream.time_ms += 1
        host_clock[0] += 0.1
        with collector.span("operation.predict_groups", "operation"):
            stream.time_ms += 1
            host_clock[0] += 0.1
            with collector.span("kernel.prediction.pp_pairs", "kernel"):
                stream.time_ms += 2
                host_clock[0] += 0.2
            stream.time_ms += 1
            host_clock[0] += 0.1
        stream.time_ms += 1
        host_clock[0] += 0.1
    assert collector.flush_ready() == 0
    assert stream.synchronizations == 0
    stream.completed_ms = stream.time_ms  # Represents the engine's already existing wait.
    assert collector.flush_ready() == 3
    assert stream.synchronizations == 0
    data = rows(collector.raw_path)
    assert [row["parent_id"] for row in data] == [None, 0, 1]
    assert [row["event_stream_ms"] for row in data] == [6, 4, 2]
    assert [row["event_stream_exclusive_ms"] for row in data] == [2, 2, 2]
    np.testing.assert_allclose([row["host_call_s"] for row in data], [0.6, 0.4, 0.2])
    np.testing.assert_allclose([row["host_exclusive_s"] for row in data], [0.2, 0.2, 0.2])
    assert all(row["event_stream_includes_host_launch_gaps"] for row in data)
    assert not any(row["event_stream_is_kernel_busy"] for row in data)
    result = collector.finish()
    assert stream.synchronizations == 1
    assert collector.finish() is result and stream.synchronizations == 1
    summary = result["summary_data"]
    assert summary["pending_spans"] == 0
    assert summary["root_and_exclusive_totals"]["event_stream_ms"] == 6
    assert summary["root_and_exclusive_totals"]["event_stream_exclusive_ms"] == 6
    assert summary["by_label"][0]["count"] == 1
    assert len(rows(result["raw_records"])) == 3
    assert not tuple(tmp_path.glob("*.pending"))


def test_sample_every_tenth_whole_batch_is_bounded(tmp_path):
    stream = FakeStream()
    collector = DeviceProfileCollector(fake_cp(), stream, 0, tmp_path, sample_every=10)
    for index in range(21):
        collector.begin_batch()
        with collector.span("stage.prune", "stage"):
            stream.time_ms += 1
            with collector.span("kernel.chart.prune_chart_geometry", "kernel"):
                stream.time_ms += 2
        with collector.span("stage.split", "stage"):
            stream.time_ms += 1
        stream.completed_ms = stream.time_ms
        collector.flush_ready()
    assert stream.synchronizations == 0
    report = collector.finish()
    data = rows(report["raw_records"])
    assert len(data) == 9
    assert {row["batch_index"] for row in data} == {0, 10, 20}
    assert report["summary_data"]["sampling"] == {
        "every_n_batches": 10,
        "prune_calls": 21,
        "sampled_batches": 3,
        "means_are_measured_samples_not_extrapolated": True,
    }
    assert report["summary_data"]["event_count"] == 18


def test_seed_uses_upcoming_batch_sample_and_errors_are_preserved(tmp_path):
    stream = FakeStream()
    collector = DeviceProfileCollector(fake_cp(), stream, 0, tmp_path, sample_every=2)
    collector.before_seed()
    assert collector.active and collector.batch_index == 0
    with pytest.raises(ValueError, match="same failure"):
        with collector.span("stage.seed", "stage"):
            stream.time_ms += 1
            raise ValueError("same failure")
    collector.begin_batch()
    assert collector.active and collector.batch_index == 0
    collector.before_seed()
    assert not collector.active and collector.batch_index == 1
    collector.finish()
    data = rows(collector.raw_path)
    assert data[0]["error"] == "ValueError"
    assert data[0]["label"] == "stage.seed"


def test_instance_module_proxy_preserves_calls_and_caches_functions(tmp_path):
    stream, cp = FakeStream(), fake_cp()
    collector = DeviceProfileCollector(cp, stream, 2, tmp_path)
    calls = []

    def kernel(grid, block, args, **kwargs):
        calls.append((grid, block, args, kwargs))
        args[0][...] += 3
        stream.time_ms += 4
        return "unchanged-return"

    gets = []
    module = SimpleNamespace(
        get_function=lambda name: (gets.append(name), kernel)[1], arbitrary_attribute="delegated"
    )
    proxy = profiling._ModuleProxy(module, collector, "prediction")
    array = np.zeros((2, 3))
    function = proxy.get_function("pp_pairs")
    assert proxy.get_function("pp_pairs") is function
    assert proxy.arbitrary_attribute == "delegated"
    assert function((2,), (128,), (array,), shared_mem=7) == "unchanged-return"
    assert gets == ["pp_pairs"]
    assert calls[0][:2] == ((2,), (128,)) and calls[0][2][0] is array
    assert calls[0][3] == {"shared_mem": 7}
    np.testing.assert_array_equal(array, np.full((2, 3), 3))
    collector.active = False
    function((2,), (128,), (array,))
    report = collector.finish()
    assert len(rows(report["raw_records"])) == 1
    row = rows(report["raw_records"])[0]
    assert row["label"] == "kernel.prediction.pp_pairs"
    assert row["dimensions"]["arrays"] == [{"index": 0, "shape": [2, 3], "dtype": "float64"}]
    assert row["event_stream_ms"] == 4


def test_nvtx_is_optional_and_unavailable_runtime_does_not_change_result(tmp_path):
    stream = FakeStream()
    collector = DeviceProfileCollector(fake_cp(), stream, 0, tmp_path, nvtx=True)
    with collector.span("stage.split", "stage"):
        stream.time_ms += 2
    result = collector.finish()
    assert result["summary_data"]["nvtx_error"]
    assert rows(result["raw_records"])[0]["event_stream_ms"] == 2


def test_host_profile_is_per_collector_and_writes_readable_stats(tmp_path):
    collector = DeviceProfileCollector(fake_cp(), FakeStream(), 0, tmp_path)
    collector.start_host_profile()
    sum(range(10))
    with pytest.raises(RuntimeError, match="already started"):
        collector.start_host_profile()
    result = collector.finish()
    statistics = pstats.Stats(result["host_profile"])
    assert statistics.get_stats_profile().func_profiles
    assert result["summary_data"]["host_profile_thread_ident"] is not None


def test_host_stats_can_be_saved_after_worker_thread_exits(tmp_path):
    collector = DeviceProfileCollector(fake_cp(), FakeStream(), 0, tmp_path)

    def worker_work():
        return sum(range(20))

    def worker():
        collector.start_host_profile()
        worker_work()

    thread = Thread(target=worker)
    thread.start()
    thread.join()
    result = collector.finish()
    assert result["summary_data"]["host_profile_thread_ident"] != get_ident()
    functions = pstats.Stats(result["host_profile"]).get_stats_profile().func_profiles
    assert "worker_work" in functions


def test_optional_nvtx_labels_balance_and_failures_do_not_replace_kernel_result(tmp_path):
    stream, cp = FakeStream(), fake_cp()
    labels = []
    cp.cuda.nvtx = SimpleNamespace(
        RangePush=lambda label: labels.append(label), RangePop=lambda: labels.append("pop")
    )
    collector = DeviceProfileCollector(cp, stream, 0, tmp_path)
    collector._nvtx = cp.cuda.nvtx
    with collector.span("stage.score", "stage"):
        with collector.span("assignment.cooperative", "assignment"):
            stream.time_ms += 2
    assert labels == [
        "resident.gpu0.stage.score",
        "resident.gpu0.assignment.cooperative",
        "pop",
        "pop",
    ]
    collector.finish()


@pytest.mark.parametrize("sample_every", [0, -1, 0.5, True])
def test_invalid_sampling_is_rejected(tmp_path, sample_every):
    with pytest.raises(ValueError, match="positive integer"):
        DeviceProfileCollector(fake_cp(), FakeStream(), 0, tmp_path, sample_every=sample_every)


def test_finishing_active_span_and_overwriting_existing_evidence_are_rejected(tmp_path):
    collector = DeviceProfileCollector(fake_cp(), FakeStream(), 0, tmp_path)
    with collector.span("stage.score", "stage"):
        with pytest.raises(RuntimeError, match="active"):
            collector.finish()
    collector.finish()
    with pytest.raises(FileExistsError):
        DeviceProfileCollector(fake_cp(), FakeStream(), 0, tmp_path)


@pytest.mark.skipif(
    os.environ.get("RESIDENT_PROFILE_GPU_TEST") != "1",
    reason="Explicit opt-in tiny GPU 0 parity check",
)
def test_tiny_gpu_profile_preserves_every_output(tmp_path):
    cp = pytest.importorskip("cupy")
    from pyproj import Geod, Transformer
    from shapely.geometry import box

    from maritime.geometry.vector_map import VectorMap
    from maritime.schemas.inputs_v2 import CatalogRecordV2, CatalogV2
    from maritime.search.gpu.resident.engine import ResidentConfig
    from maritime.search.gpu.resident.optimized import OptimizedResidentDevice
    from maritime.search.gpu.resident.preparation import prepare_world

    lon0, lat0 = -79.905, 32.77
    x, y = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform(lon0, lat0)
    coverage = box(x - 30000, y - 30000, x + 30000, y + 30000)
    vm = VectorMap(None, crs="EPSG:32618", coverage_geometry=coverage)
    records = []
    for index, bearing in enumerate((30.0, 90.0, 150.0)):
        lon, lat, _ = Geod(ellps="WGS84").fwd(lon0, lat0, bearing, 500 + 100 * index)
        records.append(CatalogRecordV2(str(index), lat, lon, normalized_type="physical_aid"))
    catalog = CatalogV2(tuple(records), "2023-07-15T12:00:30Z", "test", "1", "profile-parity")
    world = prepare_world(
        vm,
        catalog,
        chart_geometry=coverage,
        catalog_complete=True,
        catalog_objects_cover_envelope=True,
        identities_certified=True,
        exact_positions_asserted=True,
        nominal_offsets_asserted=True,
        max_projection_scale=1.02,
        azimuth_error_deg=1.0,
    )
    config = ResidentConfig(
        (x - 2, x + 2, y - 2, y + 2),
        (0,),
        object_capacity=16,
        numerical_validation_accepted=True,
        trusted_marker_labels=True,
        hard_bearing_bounds_asserted=True,
    )
    reference = OptimizedResidentDevice(world, config, 0)
    resident = ProfiledResidentDevice(world, config, 0, profile_dir=tmp_path)
    data = np.array([[x - 2, y - 2, x, y], [x, y, x + 2, y + 2]])

    def evaluate(device):
        with cp.cuda.Device(0), device.stream:
            cells = cp.asarray(data)
            bearings = cp.asarray([30.0, 90.0, 150.0])
            tolerances = cp.full(3, 1.0)
            output = {
                "seed": device.seed(0, 2, 2),
                "prune": device.prune(cells, bearings, tolerances),
                "split": device.split(cells),
                **device.score(cells, bearings, tolerances),
            }
            device.stream.synchronize()
            return {name: cp.asnumpy(array) for name, array in output.items()}

    expected, actual = evaluate(reference), evaluate(resident)
    assert expected.keys() == actual.keys()
    for name in expected:
        np.testing.assert_array_equal(actual[name], expected[name], err_msg=name)
    report = resident.finish_profile()
    labels = {row["label"] for row in rows(report["raw_records"])}
    assert {
        "stage.seed",
        "stage.prune",
        "stage.score",
        "stage.split",
        "operation.predict_groups",
        "assignment.cooperative",
        "kernel.chart.prune_chart_geometry",
        "kernel.prediction.pp_context",
        "kernel.prediction.pp_gather",
        "kernel.prediction.pp_pairs",
        "kernel.prediction.pp_compact",
        "kernel.prediction.pp_group_min",
        "kernel.prediction.pp_group_rep",
        "kernel.prediction.pp_group_angles",
        "kernel.prediction.pp_group_extent",
        "kernel.baseline.seed_cells",
        "kernel.baseline.candidate_scope",
    } <= labels
    assert report["summary_data"]["pending_spans"] == 0
