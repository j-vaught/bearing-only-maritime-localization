"""CPU-only IPC and supervisor checks. No localization or CUDA work executes."""

import os
import subprocess
import sys
import time
from contextlib import nullcontext
from types import SimpleNamespace

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
    # These workers exercise IPC only and never access inherited CUDA state.
    monkeypatch.setattr(service, "sys", SimpleNamespace(modules={}))
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
    monkeypatch.setattr(service, "sys", SimpleNamespace(modules={}))
    monkeypatch.setattr(service, "_serve", _startup_failure)
    worker = service.PersistentSearchService(object(), {}, tmp_path)
    with pytest.raises(RuntimeError, match="without a response"):
        worker.start(timeout_s=2)
    assert worker.poisoned
    assert not worker.process.is_alive()


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


def test_unexpected_worker_exit_cleans_remaining_descendants(fake_service, tmp_path):
    with pytest.raises(RuntimeError, match="without a response"):
        fake_service.search([97], 0.5, tmp_path, timeout_s=2)
    assert (tmp_path / "descendant.pid").exists()
    assert service._live_group_members(fake_service.pid) == []
    assert fake_service.poisoned


def test_service_rejects_cuda_initialized_supervisor(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "sys", SimpleNamespace(modules={"cupy": object()}))
    with pytest.raises(RuntimeError, match="before forking"):
        service.PersistentSearchService(object(), {}, tmp_path)
