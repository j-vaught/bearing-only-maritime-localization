"""Persistent truth-free search subprocess with bounded supervisor polling."""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path
from threading import Event
from typing import Any


def _serve(
    connection: Any, parent_connection: Any, world: Any, config: Any, output_root: Path
) -> None:
    """Only prepared geometry, fixed search settings and bearing vectors enter."""
    parent_connection.close()
    os.setsid()
    from contextlib import redirect_stderr, redirect_stdout

    import numpy as np

    from .engine import ResidentConfig, ResidentSearchSession, run_resident_search
    from .profiling import ProfiledResidentDevice

    def factory(prepared: Any, settings: Any, device: int) -> Any:
        return ProfiledResidentDevice(prepared, settings, device, sample_every=10)

    config = ResidentConfig(**config)
    completed = 0
    try:
        with ResidentSearchSession(world, config, device_factory=factory) as session:
            connection.send({"stage": "ready", "pid": os.getpid()})
            while True:
                try:
                    request = connection.recv()
                except EOFError:
                    break
                if request == {"command": "close"}:
                    break
                if set(request) == {"command", "directory"} and request["command"] == "prepare":
                    directory = Path(request["directory"]).resolve()
                    if (
                        not directory.is_relative_to(output_root.resolve())
                        or not directory.is_dir()
                    ):
                        raise ValueError("Preparation directory must exist under suite output root")
                    started = time.perf_counter()
                    with session.request(world, config):
                        session.execute_workers(
                            lambda device, resident: None, directory / "search", Event()
                        )
                    connection.send(
                        {
                            "stage": "prepared",
                            "worker_pid": os.getpid(),
                            "elapsed_s": time.perf_counter() - started,
                            "devices": list(session.last_device_diagnostics.values()),
                            "search_executed": False,
                        }
                    )
                    continue
                fields = {"command", "bearings", "tolerances", "directory"}
                if set(request) not in (fields, fields | {"sharing"}):
                    raise ValueError("Unexpected search request fields")
                if request["command"] != "search":
                    raise ValueError("Unknown search request")
                directory = Path(request["directory"]).resolve()
                if not directory.is_relative_to(output_root.resolve()) or not directory.is_dir():
                    raise ValueError("Search directory must exist under the suite output root")
                started = time.perf_counter()
                with (directory / "search.log").open("w", buffering=1) as log:
                    with redirect_stdout(log), redirect_stderr(log):
                        summary = run_resident_search(
                            world,
                            config,
                            np.asarray(request["bearings"], dtype=np.float64),
                            np.asarray(request["tolerances"], dtype=np.float64),
                            directory / "search",
                            session=session,
                            **({"sharing": request["sharing"]} if "sharing" in request else {}),
                        )
                        api_wall_s = time.perf_counter() - started
                        profiles = list(session.last_lifecycle_results.values())
                        profile_finish_by_device = {
                            str(device): report["profile_finish_s"]
                            for device, report in session.last_lifecycle_results.items()
                        }
                        diagnostics = summary["resident_session"]["devices"]
                        buffers_reused = bool(diagnostics) and all(
                            device["reused"] for device in diagnostics
                        )
                metadata = {
                    "worker_pid": os.getpid(),
                    "request_index": completed,
                    "gpu_buffers_reused": buffers_reused,
                    "cold_start": any(device["created"] for device in diagnostics),
                    "api_wall_s": api_wall_s,
                    "profile_finish_s": max(profile_finish_by_device.values(), default=0.0),
                    "profile_finish_s_by_device": profile_finish_by_device,
                    "summary": summary,
                    "profiles": profiles,
                }
                (directory / "search-process-summary.json").write_text(
                    json.dumps(metadata, indent=2) + "\n"
                )
                completed += 1
                connection.send({"stage": "completed", "summary": summary, "metadata": metadata})
    except BaseException as error:
        try:
            connection.send(
                {"stage": "failed", "error": str(error), "traceback": traceback.format_exc()}
            )
        except (BrokenPipeError, EOFError, OSError):
            pass
        raise
    finally:
        connection.close()


class PersistentSearchService:
    """One process owns GPU allocations until explicit close or a failed request."""

    def __init__(self, world: Any, config: dict[str, Any], output_root: Path) -> None:
        if "cupy" in sys.modules:
            raise RuntimeError("Supervisor must not import CuPy before forking")
        self.world, self.config, self.output_root = world, config, output_root
        self.process: Any = None
        self.connection: Any = None
        self.ready = False
        self.poisoned = False
        self.startup_s = 0.0

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process is not None else None

    def start(self, timeout_s: float = 60) -> None:
        if self.poisoned:
            raise RuntimeError("Persistent search worker failed; automatic restart is forbidden")
        if self.process is not None:
            if not self.process.is_alive():
                self.poisoned = True
                raise RuntimeError("Persistent search worker exited")
            return
        context = mp.get_context("fork")
        self.connection, child = context.Pipe()
        self.process = context.Process(
            target=_serve, args=(child, self.connection, self.world, self.config, self.output_root)
        )
        started = time.perf_counter()
        self.process.start()
        child.close()
        try:
            response = self._receive(timeout_s)
            if response.get("stage") != "ready" or response.get("pid") != self.pid:
                raise RuntimeError(f"Persistent worker startup failed. {response}")
            self.ready = True
            self.startup_s = time.perf_counter() - started
        except BaseException:
            self.poisoned = True
            self.close(force=True)
            raise

    def _receive(self, timeout_s: float) -> dict[str, Any]:
        if not self.connection.poll(timeout_s):
            raise TimeoutError("Persistent search worker response timed out")
        try:
            return self.connection.recv()
        except EOFError:
            raise RuntimeError("Persistent search worker exited without a response") from None

    def prepare(self, directory: Path, *, timeout_s: float = 120) -> dict[str, Any]:
        """Upload map/catalog inputs without seeding or searching any cells."""
        self.start()
        try:
            self.connection.send({"command": "prepare", "directory": str(directory)})
            response = self._receive(timeout_s)
            if response.get("stage") != "prepared" or response.get("search_executed") is not False:
                raise RuntimeError(f"Persistent worker preparation failed. {response}")
            return response
        except BaseException:
            self.poisoned = True
            self.close(force=True)
            raise

    def search(
        self,
        bearings: list[float],
        tolerance: float,
        directory: Path,
        *,
        timeout_s: float,
        progress_check: Any = None,
        sharing: bool | None = None,
    ) -> dict[str, Any]:
        self.start()
        if self.poisoned:
            raise RuntimeError("Persistent worker is unavailable after a failed request")
        started = time.perf_counter()
        try:
            request = {
                "command": "search",
                "bearings": bearings,
                "tolerances": [tolerance] * len(bearings),
                "directory": str(directory),
            }
            if sharing is not None:
                request["sharing"] = sharing
            self.connection.send(request)
            while not self.connection.poll(0.2):
                if not self.process.is_alive():
                    raise RuntimeError("Persistent search worker exited during search")
                if time.perf_counter() - started > timeout_s:
                    raise TimeoutError(f"Search exceeded {timeout_s} seconds")
                if progress_check is not None:
                    progress_check()
            response = self._receive(0)
            if progress_check is not None:
                progress_check()
            if response.get("stage") != "completed":
                raise RuntimeError(f"Persistent worker search failed. {response}")
            return response
        except BaseException:
            self.poisoned = True
            self.close(force=True)
            raise

    def close(self, *, force: bool = False) -> None:
        process = self.process
        if process is None:
            return
        if process.is_alive() and not force and self.ready:
            try:
                self.connection.send({"command": "close"})
            except (BrokenPipeError, EOFError, OSError):
                pass
            process.join(5)
        if process.is_alive():
            # Before the ready handshake, setsid may not have happened yet.
            if self.ready:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                process.terminate()
            process.join(5)
        if process.is_alive():
            if self.ready:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.join(10)
        if process.is_alive():
            raise RuntimeError("Persistent search worker could not be stopped")
        if self.ready:
            # A worker can exit before a subprocess in its established group.
            # Only a successful ready handshake authorizes signaling that group.
            for sig, timeout_s in ((signal.SIGTERM, 5), (signal.SIGKILL, 10)):
                if not _live_group_members(process.pid):
                    break
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    break
                deadline = time.perf_counter() + timeout_s
                while _live_group_members(process.pid) and time.perf_counter() < deadline:
                    time.sleep(0.05)
            if _live_group_members(process.pid):
                raise RuntimeError("Persistent search worker group still has live descendants")
        if self.connection is not None:
            self.connection.close()
        self.ready = False


def _live_group_members(group: int) -> list[int]:
    output = subprocess.check_output(["ps", "-eo", "pid=,pgid=,stat="], text=True)
    return [
        int(fields[0])
        for line in output.splitlines()
        if (fields := line.split()) and int(fields[1]) == group and not fields[2].startswith("Z")
    ]
