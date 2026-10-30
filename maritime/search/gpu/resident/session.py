"""Reusable device owners with isolated sequential search requests.

Constructing a session initializes neither CUDA nor a map. Each device is
created lazily on a dedicated thread and remains there until session closure.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from threading import Event, Lock, get_ident
from time import perf_counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from .engine import ResidentConfig
    from .preparation import PreparedWorld


class ResidentSearchSession:
    """Keep one prepared world and device configuration alive between requests.

    A request must pass the original world object and an equal configuration.
    The caller must keep that prepared world immutable for the session lifetime.
    Failed requests close the session rather than risk reusing partial state.
    """

    def __init__(
        self,
        world: PreparedWorld,
        config: ResidentConfig,
        *,
        device_factory: Any | None = None,
    ) -> None:
        if device_factory is None:
            from .engine import ResidentDevice

            device_factory = ResidentDevice
        if not callable(device_factory):
            raise TypeError("device_factory must construct a resident device.")
        self.world, self.config = world, config
        self._factory = device_factory
        self._lock = Lock()
        self._closed = False
        self._residents: dict[int, Any] = {}
        self._executors = {
            device: ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"resident-{device}")
            for device in config.devices
        }
        self.last_lifecycle_results: dict[int, Any] = {}
        self.last_device_diagnostics: dict[int, dict[str, Any]] = {}
        self.completed_requests = 0
        self._request_thread: int | None = None

    @property
    def closed(self) -> bool:
        return self._closed

    @contextmanager
    def request(self, world: PreparedWorld, config: ResidentConfig):
        """Reject concurrent/incompatible requests before creating output files."""
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A resident session request or close is already active.")
        try:
            if self._closed:
                raise RuntimeError("The resident session is closed.")
            if world is not self.world or config != self.config:
                raise ValueError("Resident session requires the original world and configuration.")
            self.last_lifecycle_results = {}
            self.last_device_diagnostics = {}
            self._request_thread = get_ident()
            try:
                yield self
            except BaseException:
                self._shutdown()
                raise
            else:
                self.completed_requests += 1
        finally:
            self._request_thread = None
            self._lock.release()

    def _run_device(self, worker: Any, device: int, output_dir: Path) -> None:
        resident = self._residents.get(device)
        created = resident is None
        started = perf_counter()
        if created:
            resident = self._factory(self.world, self.config, device)
            self._residents[device] = resident
        cp = resident.cp
        with cp.cuda.Device(device), resident.stream:
            if created:
                resident.stream.synchronize()
            self.last_device_diagnostics[device] = {
                "device": device,
                "created": created,
                "reused": not created,
                "initialization_s": perf_counter() - started if created else 0.0,
                "owner_thread_id": get_ident(),
            }
            begin = getattr(resident, "begin_search", None)
            end = getattr(resident, "end_search", None)
            if begin is not None:
                begin(output_dir)
            try:
                worker(device, resident)
            finally:
                resident.stream.synchronize()
                if end is not None:
                    self.last_lifecycle_results[device] = end()

    def execute_workers(self, worker: Any, output_dir: Path, stop: Event) -> None:
        """Run the existing traversal on each device's retained owner thread."""
        if self._request_thread != get_ident():
            raise RuntimeError("Worker execution requires the active request owner.")
        futures = [
            executor.submit(self._run_device, worker, device, output_dir)
            for device, executor in self._executors.items()
        ]
        first_error: BaseException | None = None
        for future in as_completed(futures):
            try:
                future.result()
            except BaseException as error:
                stop.set()
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error

    def _release_device(self, device: int) -> None:
        resident = self._residents.pop(device, None)
        if resident is None:
            return
        cp = resident.cp
        with cp.cuda.Device(device), resident.stream:
            resident.stream.synchronize()
            close = getattr(resident, "close", None)
            if close is not None:
                close()
            # Releasing the final reference here keeps destruction on its owner.
            del resident

    def _shutdown(self) -> None:
        self._closed = True
        futures = [
            executor.submit(self._release_device, device)
            for device, executor in self._executors.items()
        ]
        try:
            for future in futures:
                future.result()
        finally:
            for executor in self._executors.values():
                executor.shutdown(wait=True, cancel_futures=True)
            self._executors.clear()

    def close(self) -> None:
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("Cannot close a resident session during an active request.")
        try:
            if not self._closed:
                self._shutdown()
        finally:
            self._lock.release()

    def __enter__(self) -> ResidentSearchSession:
        if self._closed:
            raise RuntimeError("The resident session is closed.")
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
