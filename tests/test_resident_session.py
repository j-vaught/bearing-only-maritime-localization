"""Lifecycle-only fake-device checks. No geometry queries or searches execute."""

from contextlib import nullcontext
from dataclasses import replace
from threading import Event, Lock, Thread, get_ident
from types import SimpleNamespace
from typing import cast

import numpy as np
import pytest

from maritime.search.gpu.resident import engine
from maritime.search.gpu.resident.engine import ResidentConfig
from maritime.search.gpu.resident.preparation import PreparedWorld
from maritime.search.gpu.resident.session import ResidentSearchSession


def config():
    return ResidentConfig((0.0, 1.0, 0.0, 1.0), (0, 1))


class FakeDevice:
    def __init__(self, device):
        self.device = device
        self.owner = get_ident()
        self.calls = []
        self.closed = False
        self.map_buffer = object()
        self.cp = SimpleNamespace(cuda=SimpleNamespace(Device=lambda _: nullcontext()))
        self.stream = self

    def __enter__(self):
        assert self.owner == get_ident()
        return self

    def __exit__(self, *_):
        pass

    def synchronize(self):
        assert self.owner == get_ident()

    def begin_search(self, path):
        self.calls.append(("begin", path, get_ident()))

    def end_search(self):
        self.calls.append(("end", get_ident()))
        return {"device": self.device, "ended": True}

    def close(self):
        assert self.owner == get_ident()
        self.closed = True


def make_session():
    made = []
    lock = Lock()

    def factory(world, configuration, device):
        resident = FakeDevice(device)
        with lock:
            made.append(resident)
        return resident

    world, configuration = cast(PreparedWorld, object()), config()
    session = ResidentSearchSession(world, configuration, device_factory=factory)
    return session, world, configuration, made


def test_reuses_map_objects_and_owner_threads_across_isolated_requests(tmp_path):
    session, world, configuration, made = make_session()
    assert not made  # Session creation never initializes CUDA or a map.
    observations = []
    for index in range(2):

        def worker(device, resident):
            observations.append((index, device, resident.map_buffer, get_ident()))

        with session.request(world, configuration):
            session.execute_workers(worker, tmp_path / str(index), Event())
            assert all(d["reused"] == bool(index) for d in session.last_device_diagnostics.values())
            assert set(session.last_lifecycle_results) == {0, 1}
    assert len(made) == 2
    assert session.completed_requests == 2
    for device in configuration.devices:
        rows = [r for r in observations if r[1] == device]
        assert rows[0][2] is rows[1][2]
        assert rows[0][3] == rows[1][3]
    session.close()
    assert session.closed and all(r.closed for r in made)
    session.close()


def test_rejects_different_world_configuration_and_closed_requests(tmp_path):
    session, world, configuration, made = make_session()
    for bad_world, bad_config in (
        (cast(PreparedWorld, object()), configuration),
        (world, replace(configuration, roots_per_task=64)),
    ):
        with pytest.raises(ValueError, match="original world and configuration"):
            engine.run_resident_search(
                bad_world,
                bad_config,
                np.array([]),
                np.array([]),
                tmp_path / "never",
                session=session,
            )
        assert not (tmp_path / "never").exists()
        assert not made and not session.closed
    session.close()
    with pytest.raises(RuntimeError, match="closed"), session.request(world, configuration):
        pass


def test_rejects_concurrent_requests_and_close():
    session, world, configuration, _ = make_session()
    entered, release = Event(), Event()

    def hold():
        with session.request(world, configuration):
            entered.set()
            assert release.wait(5)

    thread = Thread(target=hold)
    thread.start()
    assert entered.wait(5)
    try:
        with (
            pytest.raises(RuntimeError, match="already active"),
            session.request(world, configuration),
        ):
            pass
        with pytest.raises(RuntimeError, match="active request"):
            session.close()
    finally:
        release.set()
        thread.join(5)
        session.close()
    assert not thread.is_alive()


def test_failure_waits_for_other_workers_and_closes_on_owner_threads(tmp_path):
    session, world, configuration, made = make_session()
    other_entered, other_finished = Event(), Event()
    stop = Event()

    def worker(device, resident):
        if device == 0:
            assert other_entered.wait(5)
            raise RuntimeError("deliberate lifecycle failure")
        other_entered.set()
        assert stop.wait(5)
        other_finished.set()

    with pytest.raises(RuntimeError, match="deliberate lifecycle failure"):
        with session.request(world, configuration):
            session.execute_workers(worker, tmp_path, stop)
    assert other_finished.is_set()
    assert session.closed and all(r.closed for r in made)
    assert all([c[0] for c in r.calls] == ["begin", "end"] for r in made)
    assert session.completed_requests == 0


def test_wrapper_forwards_each_observation_and_output_without_traversal(monkeypatch, tmp_path):
    session, world, configuration, made = make_session()
    seen = []

    def fake_run(world_arg, config_arg, bearings, tolerances, output_dir, **kwargs):
        seen.append((bearings.copy(), tolerances.copy(), output_dir, kwargs["session"]))
        return {"complete": True}

    monkeypatch.setattr(engine, "_run_resident_search", fake_run)
    for value in (10.0, 20.0):
        engine.run_resident_search(
            world,
            configuration,
            np.array([value]),
            np.array([0.5]),
            tmp_path / str(value),
            session=session,
        )
    assert [s[0].tolist() for s in seen] == [[10.0], [20.0]]
    assert seen[0][2] != seen[1][2]
    assert all(s[3] is session for s in seen)
    assert not made
    session.close()


def test_larger_default_root_packet_fits_default_batch():
    configuration = config()
    assert configuration.roots_per_task == configuration.batch_cells == 128


def test_close_drops_buffer_fields_despite_assignment_cycle_on_owner_thread(tmp_path):
    """Resource fields disappear immediately even when a caller retains the device."""
    import weakref

    from maritime.search.gpu.resident.engine import ResidentDevice

    released = []
    references = []
    residents = []

    class Buffer:
        pass

    class OwnedDevice(FakeDevice, ResidentDevice):
        def close(self):
            ResidentDevice.close(self)
            self.closed = True

    def factory(world, configuration, device):
        resident = OwnedDevice(device)
        resident._closed = False
        for field in (
            "map_args",
            "object_args",
            "barriers",
            "module",
            "_parallel_prediction_module",
            "_independent_chart_modules",
        ):
            buffer = Buffer()
            references.append(weakref.ref(buffer))
            weakref.finalize(
                buffer, lambda owner=resident.owner: released.append((owner, get_ident()))
            )
            setattr(resident, field, buffer)
        resident.assignment = resident.close  # Deliberate bound-method self cycle.
        residents.append(resident)  # Keep residents alive after session.close().
        return resident

    world, configuration = cast(PreparedWorld, object()), config()
    session = ResidentSearchSession(world, configuration, device_factory=factory)
    with session.request(world, configuration):
        session.execute_workers(lambda *_: None, tmp_path, Event())
    assert all(reference() is not None for reference in references)
    session.close()
    assert all(reference() is None for reference in references)
    assert len(released) == 12
    assert all(owner == releasing_thread for owner, releasing_thread in released)
    assert all(resident._closed and resident.closed for resident in residents)
    assert all(not hasattr(resident, "assignment") for resident in residents)
    session.close()
