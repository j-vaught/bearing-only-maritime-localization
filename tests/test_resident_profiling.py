"""Event accounting uses fake streams; the optional GPU check uses two cells."""

import json
import os
import pstats
import weakref
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

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

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


def fake_profiled_device(monkeypatch, profile_dir=None):
    """Construct instrumentation against CPU-only stand-ins, without CUDA imports."""
    calls = {"construction": 0, "prediction_module": 0, "chart_module": 0, "kernel": 0}
    stream = FakeStream()

    def kernel(grid, block, args, **kwargs):
        calls["kernel"] += 1
        stream.time_ms += 1

    baseline = SimpleNamespace(get_function=lambda name: kernel)
    prediction = SimpleNamespace(get_function=lambda name: kernel)
    chart = SimpleNamespace(get_function=lambda name: kernel)

    def initialize(self, world, config, device, **options):
        calls["construction"] += 1
        self.cp, self.stream, self.device = fake_cp(), stream, device
        self.world, self.config, self.module = world, config, baseline
        self.map_args, self.object_args = (object(),), (object(),)
        self.assignment = kernel
        self.use_parallel_prediction = True
        self.use_independent_chart_pruning = True
        self.use_cooperative_matching = False
        self.use_true_bearings = True

    def prediction_module(self):
        calls["prediction_module"] += 1
        self._parallel_prediction_module = prediction
        return prediction

    def chart_module(self, true_bearings):
        calls["chart_module"] += 1
        self._independent_chart_modules = {true_bearings: chart}
        return chart

    monkeypatch.setattr(profiling.OptimizedResidentDevice, "__init__", initialize)
    monkeypatch.setattr(profiling.parallel_prediction, "_module", prediction_module)
    monkeypatch.setattr(profiling.chart_pruning, "module_for", chart_module)
    resident = ProfiledResidentDevice(
        SimpleNamespace(objects=[]),
        SimpleNamespace(object_capacity=16),
        0,
        profile_dir=profile_dir,
        sample_every=10,
    )
    return resident, calls, (baseline, prediction, chart, kernel)


def test_reusable_profiles_keep_buffers_modules_and_separate_observations(tmp_path, monkeypatch):
    resident, calls, raw = fake_profiled_device(monkeypatch)
    buffers = resident.map_args, resident.object_args
    assert resident.profile is None
    with pytest.raises(RuntimeError, match="begin_search"):
        resident.finish_profile()

    profiles = []
    for name in ("first", "second"):
        resident.begin_search(tmp_path / name / "search")
        collector = resident._require_profile()
        assert collector.prune_calls == 0
        collector.begin_batch()
        resident.module.get_function("seed_cells")((1,), (128,), ())
        resident._parallel_prediction_module.get_function("pp_pairs")((1,), (128,), ())
        resident._independent_chart_modules[True].get_function("prune")((1,), (128,), ())
        resident.assignment((1,), (128,), ())
        report = resident.end_search()
        assert resident.finish_profile() is report
        profiles.append(report)
        assert (resident.map_args, resident.object_args) == buffers
        assert resident.module._module is raw[0]
        assert resident._parallel_prediction_module._module is raw[1]
        assert resident._independent_chart_modules[True]._module is raw[2]
        assert resident.assignment._kernel is raw[3]
        assert collector._finished is report
        assert report["summary_data"]["sampling"]["prune_calls"] == 1
        records = rows(report["raw_records"])
        assert len(records) == 4
        assert [row["id"] for row in records] == [0, 1, 2, 3]
        assert {row["batch_index"] for row in records} == {0}
    assert calls == {"construction": 1, "prediction_module": 1, "chart_module": 1, "kernel": 8}
    assert profiles[0]["raw_records"] != profiles[1]["raw_records"]
    assert len(rows(profiles[0]["raw_records"])) == 4


def test_profile_lifecycle_rejects_unfinished_and_existing_outputs(tmp_path, monkeypatch):
    output = tmp_path / "first" / "search"
    resident, _, _ = fake_profiled_device(monkeypatch, output.parent / "profile")
    initial = resident.profile
    resident.begin_search(output)
    assert resident.profile is initial
    with pytest.raises(RuntimeError, match="Finish"):
        resident.begin_search(tmp_path / "second" / "search")
    resident._require_profile().begin_batch()
    with pytest.raises(RuntimeError, match="Finish"):
        resident.begin_search(output)
    report = resident.end_search()
    with pytest.raises(FileExistsError):
        resident.begin_search(output)
    assert resident.finish_profile() is report
    resident.begin_search(tmp_path / "second" / "search")
    assert resident.profile is not initial
    resident.end_search()


def test_initial_custom_profile_directory_is_preserved(tmp_path, monkeypatch):
    custom = tmp_path / "custom-profiling-location"
    first_search = tmp_path / "first" / "search"
    resident, _, _ = fake_profiled_device(monkeypatch, custom)
    initial = resident.profile
    resident.begin_search(first_search)
    assert resident.profile is initial
    assert resident._require_profile().profile_dir == custom
    with pytest.raises(RuntimeError, match="Finish"):
        resident.begin_search(tmp_path / "second" / "search")
    resident.end_search()
    second_search = tmp_path / "second" / "search"
    resident.begin_search(second_search)
    assert resident._require_profile().profile_dir == second_search.parent / "profile"
    resident.end_search()


def test_profile_finalization_duration_is_measured_once(tmp_path, monkeypatch):
    resident, _, _ = fake_profiled_device(monkeypatch)
    resident.begin_search(tmp_path / "first" / "search")
    collector = resident._require_profile()
    original_finish = collector.finish
    clock = [10.0]
    monkeypatch.setattr(profiling, "perf_counter", lambda: clock[0])

    def finish():
        clock[0] += 0.75
        return original_finish()

    monkeypatch.setattr(collector, "finish", finish)
    report = resident.end_search()
    assert report["profile_finish_s"] == 0.75
    clock[0] = 100.0
    assert resident.finish_profile() is report
    assert report["profile_finish_s"] == 0.75
    assert resident.end_search() is report
    assert report["profile_finish_s"] == 0.75


def test_profile_close_drops_raw_and_proxy_references_without_recounting(tmp_path, monkeypatch):
    resident, _, raw = fake_profiled_device(monkeypatch)
    resident.begin_search(tmp_path / "first" / "search")
    collector = resident._require_profile()
    collector.begin_batch()
    resident.module.get_function("seed_cells")((1,), (128,), ())
    report = resident.end_search()
    saved_report = json.loads(json.dumps(report))
    proxy_reference = weakref.ref(resident.module)
    collector_reference = weakref.ref(collector)
    # Retain the instance and deliberately add the method cycle used by the
    # cooperative assignment path. Explicit close must still release its fields.
    resident._profile_raw_assignment = resident._cooperative_assignment
    del collector, raw
    resident.close()
    assert resident._closed
    assert resident.profile is None
    assert proxy_reference() is None
    assert collector_reference() is None
    for name in (
        "map_args",
        "object_args",
        "barriers",
        "module",
        "assignment",
        "_parallel_prediction_module",
        "_independent_chart_modules",
        "_profile_raw_module",
        "_profile_raw_prediction_module",
        "_profile_raw_chart_module",
        "_profile_raw_assignment",
    ):
        assert name not in resident.__dict__
    assert report == saved_report
    synchronizations = resident.stream.synchronizations
    resident.close()
    assert resident.stream.synchronizations == synchronizations
    with pytest.raises(RuntimeError, match="closed"):
        resident.begin_search(tmp_path / "second" / "search")


def test_profile_close_finalizes_pending_collector(tmp_path, monkeypatch):
    resident, _, _ = fake_profiled_device(monkeypatch)
    resident.begin_search(tmp_path / "first" / "search")
    collector = resident._require_profile()
    collector.begin_batch()
    resident.module.get_function("seed_cells")((1,), (128,), ())
    assert collector._pending
    resident.close()
    report = collector._finished
    assert report is not None
    assert report["profile_finish_s"] >= 0
    assert report["summary_data"]["pending_spans"] == 0
    assert report["summary_data"]["sampling"]["prune_calls"] == 1
    assert len(rows(report["raw_records"])) == 1
    assert resident.profile is None
    assert resident._closed


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
