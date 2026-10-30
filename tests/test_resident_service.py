"""CPU-only IPC and supervisor checks. No localization or CUDA work executes."""

import importlib.util
import os
import subprocess
import sys
import time
from contextlib import nullcontext
from pathlib import Path

import pytest

from maritime.search.gpu.resident import service


def _fake_worker(connection, parent_connection, world, config, output_root):
    parent_connection.close()
    os.setsid()
    connection.send({"stage": "ready", "pid": os.getpid()})
    count = 0
    while True:
        request = connection.recv()
        if request == {"command": "close"}:
            break
        if request["bearings"] == [97]:
            descendant = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            (output_root / "descendant.pid").write_text(str(descendant.pid))
            os._exit(0)
        if request["bearings"] == [99]:
            time.sleep(30)
        elif request["bearings"] == [98]:
            connection.send({"stage": "failed", "error": "fake failure"})
            break
        else:
            connection.send(
                {
                    "stage": "completed",
                    "summary": {"complete": True},
                    "metadata": {
                        "pid": os.getpid(),
                        "request_index": count,
                        "keys": sorted(request),
                    },
                }
            )
            count += 1
    connection.close()


def _startup_failure(connection, parent_connection, world, config, output_root):
    parent_connection.close()
    os.setsid()
    raise RuntimeError("fake startup failure")


@pytest.fixture
def fake_service(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_serve", _fake_worker)
    worker = service.PersistentSearchService(object(), {}, tmp_path)
    yield worker
    worker.close(force=True)


def test_one_pid_serves_repeated_truth_free_requests(fake_service, tmp_path):
    first = fake_service.search([10], 0.5, tmp_path, timeout_s=2)
    second = fake_service.search([20, 30], 0.5, tmp_path, timeout_s=2)
    assert first["metadata"]["pid"] == second["metadata"]["pid"] == fake_service.pid
    assert [first["metadata"]["request_index"], second["metadata"]["request_index"]] == [0, 1]
    assert first["metadata"]["keys"] == ["bearings", "command", "directory", "tolerances"]
    fake_service.close()
    assert not fake_service.process.is_alive()


def test_timeout_stops_worker_and_forbids_restart(fake_service, tmp_path):
    with pytest.raises(TimeoutError, match="Search exceeded"):
        fake_service.search([99], 0.5, tmp_path, timeout_s=0.05)
    assert fake_service.poisoned
    assert not fake_service.process.is_alive()
    with pytest.raises(RuntimeError, match="automatic restart is forbidden"):
        fake_service.start()


def test_worker_failure_forbids_further_requests(fake_service, tmp_path):
    with pytest.raises(RuntimeError, match="fake failure"):
        fake_service.search([98], 0.5, tmp_path, timeout_s=2)
    assert fake_service.poisoned
    assert not fake_service.process.is_alive()


def test_resource_limit_callback_stops_worker(fake_service, tmp_path):
    def ceiling():
        raise RuntimeError("resource ceiling")

    with pytest.raises(RuntimeError, match="resource ceiling"):
        fake_service.search([99], 0.5, tmp_path, timeout_s=2, progress_check=ceiling)
    assert fake_service.poisoned
    assert not fake_service.process.is_alive()


def test_startup_exit_cleans_up(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_serve", _startup_failure)
    worker = service.PersistentSearchService(object(), {}, tmp_path)
    with pytest.raises(RuntimeError, match="without a response"):
        worker.start(timeout_s=2)
    assert worker.poisoned
    assert not worker.process.is_alive()


def _runner():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "suite_service_checks", root / "docs/verification/b-location-suite/run_suite.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resource_guard_exempts_only_exact_owned_context():
    runner = _runner()
    snapshot = "0, GPU, 11000, 3000, 0\n1, GPU, 11000, 200, 0\n2, GPU, 11000, 200, 0"
    indexed = "0, uuid0\n1, uuid1\n2, uuid2"
    assert runner.busy_devices(snapshot, indexed, "uuid0, 123", owned_worker_pid=123) == []
    assert runner.busy_devices(snapshot, indexed, "uuid0, 456", owned_worker_pid=123) == [0]
    assert runner.busy_devices(
        snapshot, indexed, "uuid0, 123\nuuid0, 456", owned_worker_pid=123
    ) == [0]
    assert runner.busy_devices(snapshot, indexed, "", owned_worker_pid=123) == [0]


def test_resource_guard_keeps_first_start_policy():
    runner = _runner()
    indexed = "0, uuid0\n1, uuid1\n2, uuid2"
    snapshot = "0, GPU, 11000, 200, 0\n1, GPU, 11000, 200, 0\n2, GPU, 11000, 200, 0"
    assert runner.busy_devices(snapshot, indexed, "") == []
    assert runner.busy_devices(snapshot, indexed, "uuid1, 456") == [1]


class _Connection:
    def __init__(self, requests=()):
        self.requests = iter(requests)
        self.responses = []
        self.closed = False

    def recv(self):
        try:
            return next(self.requests)
        except StopIteration:
            raise EOFError from None

    def send(self, value):
        self.responses.append(value)

    def close(self):
        self.closed = True


@pytest.fixture
def fake_real_loop(monkeypatch, tmp_path):
    from maritime.search.gpu.resident import engine, profiling

    calls = []
    instances = []

    class FakeSession:
        def __init__(self, world, config, *, device_factory):
            self.world, self.config, self.device_factory = world, config, device_factory
            self.device = None
            self.closed = False
            self.last_lifecycle_results = {}
            instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def request(self, world, config):
            assert world is self.world and config is self.config
            return nullcontext(self)

        def execute_workers(self, worker, output_dir, stop):
            reused = self.device is not None
            if self.device is None:
                self.device = self.device_factory(self.world, self.config, 0)
            worker(0, self.device)
            self.last_device_diagnostics = {
                0: {"device": 0, "created": not reused, "reused": reused}
            }

    class FakeDevice:
        def __init__(self, world, config, device, *, sample_every):
            self.map_token = object()

    def fake_run(world, config, bearings, tolerances, output_dir, *, session):
        reused = session.device is not None
        if session.device is None:
            session.device = session.device_factory(world, config, 0)
        calls.append((bearings.tolist(), tolerances.tolist(), output_dir, session.device.map_token))
        session.last_lifecycle_results = {
            0: {"profile_finish_s": 0.012, "raw_records": str(output_dir.parent / "profile")}
        }
        return {
            "complete": True,
            "elapsed_s": 0.01,
            "resident_session": {
                "devices": [{"device": 0, "created": not reused, "reused": reused}]
            },
        }

    monkeypatch.setattr(service.os, "setsid", lambda: None)
    monkeypatch.setattr(engine, "ResidentSearchSession", FakeSession)
    monkeypatch.setattr(engine, "run_resident_search", fake_run)
    monkeypatch.setattr(profiling, "ProfiledResidentDevice", FakeDevice)
    return calls, instances


def test_real_service_loop_reuses_session_and_isolates_requests(fake_real_loop, tmp_path):
    calls, instances = fake_real_loop
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    requests = [
        {"command": "search", "bearings": [10], "tolerances": [0.5], "directory": str(first)},
        {
            "command": "search",
            "bearings": [20, 30],
            "tolerances": [0.4, 0.5],
            "directory": str(second),
        },
        {"command": "close"},
    ]
    connection, parent = _Connection(requests), _Connection()
    service._serve(
        connection, parent, object(), {"bounds_en_m": (0, 1, 0, 1), "devices": (0,)}, tmp_path
    )
    assert parent.closed and connection.closed and instances[0].closed
    assert len(instances) == 1
    assert [call[0] for call in calls] == [[10], [20, 30]]
    assert calls[0][3] is calls[1][3]
    metadata = [response["metadata"] for response in connection.responses[1:]]
    assert [row["gpu_buffers_reused"] for row in metadata] == [False, True]
    assert [row["cold_start"] for row in metadata] == [True, False]
    assert [row["profile_finish_s"] for row in metadata] == [0.012, 0.012]
    for directory in (first, second):
        assert (directory / "search-process-summary.json").is_file()


def test_preload_creates_device_without_search_and_first_search_reuses_it(fake_real_loop, tmp_path):
    calls, instances = fake_real_loop
    preload, run = tmp_path / "preload", tmp_path / "run"
    preload.mkdir()
    run.mkdir()
    connection = _Connection(
        [
            {"command": "prepare", "directory": str(preload)},
            {"command": "search", "directory": str(run), "bearings": [10], "tolerances": [0.5]},
            {"command": "close"},
        ]
    )
    service._serve(
        connection,
        _Connection(),
        object(),
        {"bounds_en_m": (0, 1, 0, 1), "devices": (0,)},
        tmp_path,
    )
    assert [r["stage"] for r in connection.responses] == ["ready", "prepared", "completed"]
    assert connection.responses[1]["search_executed"] is False
    assert connection.responses[1]["devices"][0]["created"]
    assert len(calls) == 1
    assert calls[0][3] is instances[0].device.map_token
    assert connection.responses[2]["metadata"]["gpu_buffers_reused"]
    assert not connection.responses[2]["metadata"]["cold_start"]


def test_real_service_loop_rejects_truth_fields_before_search(fake_real_loop, tmp_path):
    calls, instances = fake_real_loop
    request = {
        "command": "search",
        "bearings": [10],
        "tolerances": [0.5],
        "directory": str(tmp_path),
        "truth": {"latitude_deg": 32.77},
    }
    connection, parent = _Connection([request]), _Connection()
    with pytest.raises(ValueError, match="Unexpected search request fields"):
        service._serve(
            connection, parent, object(), {"bounds_en_m": (0, 1, 0, 1), "devices": (0,)}, tmp_path
        )
    assert calls == []
    assert parent.closed and connection.closed and instances[0].closed
    assert connection.responses[-1]["stage"] == "failed"


def test_real_service_loop_closes_cleanly_on_parent_disconnect(fake_real_loop, tmp_path):
    calls, instances = fake_real_loop
    connection, parent = _Connection(), _Connection()
    service._serve(
        connection, parent, object(), {"bounds_en_m": (0, 1, 0, 1), "devices": (0,)}, tmp_path
    )
    assert calls == []
    assert instances[0].closed and connection.closed and parent.closed
    assert [row["stage"] for row in connection.responses] == ["ready"]


def test_suite_stops_after_poison_without_simulating_later_sites(monkeypatch, tmp_path):
    import argparse

    runner = _runner()
    rows = [
        {"site_id": f"B{i}", "port": f"Port {i}", "latitude_deg": "0", "longitude_deg": "0"}
        for i in range(1, 25)
    ]
    settings = {
        "map": "fake-map",
        "catalog": "fake-catalog",
        "measurements": "fake-measurements",
        "search": {"batch_cells": 512, "roots_per_task": 8},
        "marker_tolerance_deg": 0.5,
    }
    original_read = runner.read
    simulated = []

    def fake_read(path):
        if path == runner.REFERENCE:
            return {"settings": settings}
        if path == runner.SIM_SETTINGS:
            return {"sensor": {}, "seed": 11}
        return original_read(path)

    class FakeService:
        def __init__(self, *args):
            self.pid = None
            self.poisoned = False
            self.ready = False
            self.closed = False
            self.startup_s = 0.0

        def start(self):
            pass

        def search(self, *args, **kwargs):
            self.poisoned = True
            raise TimeoutError("fake worker timeout")

        def close(self):
            self.closed = True

    def fake_simulate(row, *args):
        simulated.append(row["site_id"])
        return [10] * 16, 16

    monkeypatch.setattr(runner, "read", fake_read)
    monkeypatch.setattr(runner, "sites", lambda: rows)
    monkeypatch.setattr(runner, "digest", lambda path: "fake-hash")
    monkeypatch.setattr(runner, "gpu_snapshot", lambda: "fake-gpu")
    monkeypatch.setattr(runner, "resource_guard", lambda **kwargs: {})
    monkeypatch.setattr(
        runner,
        "prepare",
        lambda *args, **kwargs: (
            object(),
            object(),
            object(),
            {
                "cache_paths": [],
                "shared_preparation_s": 0.0,
                "base_cache_hit": True,
                "object_cache_hit": True,
            },
        ),
    )
    monkeypatch.setattr(runner, "normalized_catalog_alignment", lambda *args: {"passed": True})
    monkeypatch.setattr(runner, "simulate", fake_simulate)
    monkeypatch.setattr(service, "PersistentSearchService", FakeService)
    args = argparse.Namespace(
        output=tmp_path,
        site_ids="B1,B2",
        search_timeout=1,
        minimum_positive_aids=16,
        refresh_aid_view=False,
        normalized_catalog_check=True,
        dry_run=False,
        preflight_only=False,
    )
    runner.run_suite(args)
    assert simulated == ["B1"]
    assert not (tmp_path / "B2").exists()
    assert original_read(tmp_path / "status.json")["stage"] == "halted_worker_failure"
    assert original_read(tmp_path / "status.json")["remaining_sites"] == ["B2"]
    assert (tmp_path / "cache-after.json").is_file()
    assert original_read(tmp_path / "B1/result.json")["status"] == "timeout"


def test_unexpected_worker_exit_cleans_remaining_descendants(fake_service, tmp_path):
    with pytest.raises(RuntimeError, match="without a response"):
        fake_service.search([97], 0.5, tmp_path, timeout_s=2)
    assert (tmp_path / "descendant.pid").exists()
    assert service._live_group_members(fake_service.pid) == []
    assert fake_service.poisoned
