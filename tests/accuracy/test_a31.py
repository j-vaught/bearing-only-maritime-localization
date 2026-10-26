"""Tests for search telemetry serialization and CPU/GPU clock separation."""

from __future__ import annotations

import json
import sys

import pytest

from bearing_localization.search.telemetry import SearchTelemetry


class StepClock:
    def __init__(self, step: float = 0.25) -> None:
        self.value = 0.0
        self.step = step
        self.calls = 0

    def __call__(self) -> float:
        value = self.value
        self.value += self.step
        self.calls += 1
        return value


def test_cpu_stages_and_zero_work_serialize_with_fixed_schema() -> None:
    wall = StepClock()
    gpu = StepClock(0.5)
    telemetry = SearchTelemetry(clock=wall, gpu_clock=gpu)

    with telemetry.stage("search"):
        with telemetry.stage("proposals"):
            telemetry.record_candidates(generated=0, evaluated=0, retained=0)

    data = json.loads(telemetry.to_json())
    assert data["objective_version"] == "bearing_set_map_v1"
    assert data["counts"] == {"generated": 0, "evaluated": 0, "retained": 0}
    assert [stage["name"] for stage in data["stages"]] == ["proposals", "search"]
    assert data["stages"][0]["parent"] == "search"
    assert data["stages"][0]["gpu_s"] is None
    assert gpu.calls == 0
    assert "observer" not in data and "truth" not in data
    assert "observer" not in telemetry.to_json() and "truth" not in telemetry.to_json()


def test_gpu_clock_is_only_read_for_explicit_gpu_stage() -> None:
    wall = StepClock(0.1)
    gpu = StepClock(0.4)
    telemetry = SearchTelemetry(
        backend="gpu", device_ids=(2,), clock=wall, gpu_clock=gpu, synchronize_gpu=True
    )

    with telemetry.stage("cpu_preparation"):
        pass
    assert gpu.calls == 0

    with telemetry.stage("gpu_kernel", gpu=True, device_id=2):
        pass
    stage = telemetry.as_dict()["stages"][1]
    assert stage["gpu_s"] == pytest.approx(0.4)
    assert stage["device_id"] == 2
    assert gpu.calls == 2


def test_counters_transfers_memory_and_exception_messages_are_sanitized() -> None:
    telemetry = SearchTelemetry(clock=StepClock())
    telemetry.record_candidates(generated=4, evaluated=3, retained=1)
    telemetry.record_cap_hit("candidate_budget")
    telemetry.record_unresolved_region("unknown_visibility", count=2)
    telemetry.record_transfer(64, "host_to_device", device_id=2)
    telemetry.record_memory(cpu_bytes=1024, gpu_bytes=2048)

    error = ValueError("truth observer coordinates should not be retained")
    with pytest.raises(ValueError):
        with telemetry.stage("assignment"):
            raise error

    report = telemetry.as_dict()
    assert report["counts"] == {"generated": 4, "evaluated": 3, "retained": 1}
    assert report["cap_hits"] == {"candidate_budget": 1}
    assert report["unresolved_regions"] == {"unknown_visibility": 2}
    assert report["transfers"] == [{"direction": "host_to_device", "device_id": 2, "bytes": 64}]
    assert report["memory"] == {"cpu_bytes": 1024, "gpu_bytes": 2048}
    assert report["exceptions"] == [{"type": "valueerror", "stage": "assignment"}]
    assert "coordinates" not in telemetry.to_json()
    json.dumps(report, allow_nan=False)


def test_gpu_stage_requires_cupy_when_no_test_clock_is_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry = SearchTelemetry(clock=StepClock())
    monkeypatch.setitem(sys.modules, "cupy", None)
    with pytest.raises(RuntimeError, match="optional CuPy"):
        with telemetry.stage("gpu_kernel", gpu=True, device_id=2):
            pass
    assert telemetry._stage_stack.get() == ()


def test_telemetry_rejects_observer_and_truth_labels() -> None:
    telemetry = SearchTelemetry(clock=StepClock())
    with pytest.raises(ValueError, match="cannot contain observer or truth"):
        telemetry.record_unresolved_region("observer_location")
    with pytest.raises(ValueError, match="cannot contain observer or truth"):
        telemetry.record_cap_hit("truth_dependent_cap")
