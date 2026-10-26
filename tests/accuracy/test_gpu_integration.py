from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from pyproj import Geod, Transformer

from maritime.accuracy_cli import SearchV2Session, search_v2_files
from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MapV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime.search.config import SearchConfig
from maritime.search.config_serialization import write_search_config
from maritime.search.gpu.search_adapter import (
    GpuCandidateScorer,
    score_candidate_positions_batched,
)
from maritime.search.scoring import CandidateScorer, score_candidate_positions
from maritime.search.telemetry import SearchTelemetry
from maritime.serialization import write_contract


def _fixture() -> tuple[
    MeasurementSetV2, CatalogV2, VectorMap, SearchConfig, list[tuple[float, float]]
]:
    timestamp = "2026-10-25T12:00:00Z"
    longitude, latitude = -76.0, 37.0
    geod = Geod(ellps="WGS84")
    records = []
    measurements = []
    for index, (bearing, distance) in enumerate(((20.0, 650.0), (138.0, 900.0), (265.0, 1100.0))):
        lon, lat, _ = geod.fwd(longitude, latitude, bearing, distance)
        records.append(
            CatalogRecordV2(
                key=f"target-{index}",
                latitude_deg=lat,
                longitude_deg=lon,
                raw_type="vessel",
                normalized_type="vessel",
            )
        )
        actual_bearing, _, _ = geod.inv(longitude, latitude, lon, lat)
        measurements.append(
            MeasurementV2(
                bearing_deg=actual_bearing % 360.0,
                timestamp_utc=timestamp,
                sigma_deg=0.05,
                object_type="vessel",
            )
        )
    catalog = CatalogV2(tuple(records), timestamp, "tiny-fixture", "1", "tiny-catalog")
    ring = [[-77.0, 36.0], [-75.0, 36.0], [-75.0, 38.0], [-77.0, 38.0], [-77.0, 36.0]]
    map_input = MapV2(
        crs="EPSG:4326",
        source="tiny-fixture",
        map_version="1",
        coverage_status="known",
        coverage_geometry_geojson=json.dumps({"type": "Polygon", "coordinates": [ring]}),
    )
    vector_map = VectorMap.from_map_v2(map_input, target_crs="EPSG:32618")
    x, y = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform(
        longitude, latitude
    )
    config = SearchConfig(
        algorithm="grid",
        backend="cpu",
        bounds_xy=(x - 100, x + 100, y - 100, y + 100),
        max_range_m=3_000.0,
        candidate_budget=25,
    )
    candidates = [
        (x + dx, y + dy) for dy in (-75.0, -25.0, 25.0, 75.0) for dx in (-75.0, -25.0, 25.0, 75.0)
    ]
    return (
        MeasurementSetV2(tuple(measurements), trial_id="a36-fixed"),
        catalog,
        vector_map,
        config,
        candidates,
    )


def test_cpu_import_of_adapter_does_not_load_cupy() -> None:
    source = """
import builtins
real_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'cupy' or name.startswith('cupy.'):
        raise AssertionError('CuPy imported during CPU-only module import')
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
from maritime.search.gpu.search_adapter import GpuCandidateScorer
assert GpuCandidateScorer.__name__ == 'GpuCandidateScorer'
"""
    root = Path(__file__).resolve().parents[2]
    subprocess.run(
        [sys.executable, "-c", source], cwd=root, check=True, capture_output=True, text=True
    )


def test_batched_helper_preserves_cpu_grid_order_budget_and_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    measurements, catalog, vector_map, config, positions = _fixture()
    cpu_scorer = CandidateScorer(measurements, catalog, vector_map, config)
    gpu_scorer = GpuCandidateScorer(
        measurements, catalog, vector_map, config, device_ids=(0,), fallback_on_error=True
    )
    # Exercise the fixed-stream runner on CPU fallback while preserving its
    # batching, deduplication, candidate IDs, and accounting contract.
    monkeypatch.setattr(gpu_scorer, "_gpu_disabled", True)
    expected = score_candidate_positions(
        cpu_scorer, positions, candidate_limit=9, certificate_scope="finite_candidate_set"
    )
    actual = score_candidate_positions_batched(
        gpu_scorer,
        [*positions[:4], positions[3], *positions[4:]],
        batch_size=3,
        candidate_limit=9,
        certificate_scope="finite_candidate_set",
    )
    assert actual.candidate_count == expected.candidate_count == 9
    assert actual.outcome_counts == expected.outcome_counts
    assert actual.localization.search_status == expected.localization.search_status
    assert actual.localization.budget_limit == expected.localization.budget_limit
    assert actual.localization.budget_used == expected.localization.budget_used
    assert actual.localization.hypotheses == expected.localization.hypotheses


@pytest.mark.parametrize("device_ids", [(0,), (0, 1), (0, 1, 2, 3)])
def test_real_gpu_candidate_batch_matches_cpu_full_result(
    device_ids: tuple[int, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    cp = pytest.importorskip("cupy", reason="locked GPU extra is required")
    if cp.cuda.runtime.getDeviceCount() < len(device_ids):
        pytest.skip("requested GPU device count is unavailable")
    measurements, catalog, vector_map, cpu_config, positions = _fixture()
    # frozen slotted dataclasses lack __dict__ in supported Python versions.
    from dataclasses import replace

    gpu_config = replace(cpu_config, backend="gpu")
    cpu = score_candidate_positions(
        CandidateScorer(measurements, catalog, vector_map, cpu_config),
        positions,
        candidate_limit=12,
    )
    telemetry = SearchTelemetry(
        objective_version=gpu_config.objective_version,
        backend="gpu",
        device_ids=device_ids,
    )
    scorer = GpuCandidateScorer(
        measurements,
        catalog,
        vector_map,
        gpu_config,
        device_ids=device_ids,
        telemetry=telemetry,
        batch_size=3,
    )
    import maritime.search.gpu.search_adapter as adapter

    original_predict = adapter.predict_visible_groups
    contexts = []

    def record_context(*args, **kwargs):
        contexts.append(kwargs.get("prepared_context"))
        return original_predict(*args, **kwargs)

    monkeypatch.setattr(adapter, "predict_visible_groups", record_context)
    gpu = score_candidate_positions_batched(
        scorer,
        positions,
        batch_size=3,
        candidate_limit=12,
    )
    assert gpu.candidate_count == cpu.candidate_count == 12
    assert gpu.outcome_counts == cpu.outcome_counts
    assert gpu.localization.hypotheses == cpu.localization.hypotheses
    assert gpu.localization.search_status == cpu.localization.search_status
    assert gpu.localization.budget_limit == cpu.localization.budget_limit
    assert gpu.localization.budget_used == cpu.localization.budget_used
    assert contexts
    assert all(context is scorer._prediction_context for context in contexts)
    report = telemetry.as_dict()
    assert report["backend"] == "gpu"
    assert set(report["device_ids"]) == set(device_ids)
    assert report["transfers"]
    assert {item["direction"] for item in report["transfers"]} == {
        "host_to_device",
        "device_to_host",
    }
    assert any(stage["name"] == "gpu_cost_and_cpu_verify" for stage in report["stages"])
    assert any(stage["name"] == "gpu_cpu_authority" for stage in report["stages"])


def test_gpu_error_fails_closed_or_disables_accelerator_for_cpu_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import maritime.search.gpu.search_adapter as adapter

    measurements, catalog, vector_map, config, candidates = _fixture()
    scorer = GpuCandidateScorer(
        measurements,
        catalog,
        vector_map,
        config,
        device_ids=(0,),
        fallback_on_error=False,
    )

    def fail(*args, **kwargs):
        raise RuntimeError("injected accelerator worker failure")

    monkeypatch.setattr(adapter, "iter_candidate_likelihood_costs_multi_gpu", fail)
    with pytest.raises(RuntimeError, match="injected accelerator worker failure"):
        scorer.evaluate_many(candidates[:2])

    fallback = GpuCandidateScorer(
        measurements,
        catalog,
        vector_map,
        config,
        device_ids=(0,),
        fallback_on_error=True,
    )
    monkeypatch.setattr(adapter, "iter_candidate_likelihood_costs_multi_gpu", fail)
    got = fallback.evaluate_many(candidates[:2])
    expected = tuple(
        CandidateScorer(measurements, catalog, vector_map, config).evaluate(p)
        for p in candidates[:2]
    )
    assert tuple(e.hypothesis for e in got) == tuple(e.hypothesis for e in expected)
    assert fallback._gpu_disabled
    assert (
        fallback.evaluate_many(candidates[2:3])[0].hypothesis
        == CandidateScorer(measurements, catalog, vector_map, config)
        .evaluate(candidates[2])
        .hypothesis
    )


def _write_static_session_fixture(tmp_path: Path):
    measurements_a, catalog, _vector_map, config, _positions = _fixture()
    measurements_b = MeasurementSetV2(
        tuple(
            replace(item, bearing_deg=(item.bearing_deg + 1.0) % 360.0)
            for item in measurements_a.measurements
        ),
        trial_id="a36-session-b",
    )
    measurements_a = MeasurementSetV2(measurements_a.measurements, trial_id="a36-session-a")
    map_input = MapV2(
        crs="EPSG:4326",
        source="tiny-fixture",
        map_version="1",
        coverage_status="known",
        coverage_geometry_geojson=json.dumps(
            {
                "type": "Polygon",
                "coordinates": [
                    [[-77.0, 36.0], [-75.0, 36.0], [-75.0, 38.0], [-77.0, 38.0], [-77.0, 36.0]]
                ],
            },
            separators=(",", ":"),
        ),
    )
    paths = {
        "measurements_a": tmp_path / "measurements-a.json",
        "measurements_b": tmp_path / "measurements-b.json",
        "catalog": tmp_path / "catalog.json",
        "map": tmp_path / "map.json",
        "config_cpu": tmp_path / "config-cpu.json",
        "config_gpu": tmp_path / "config-gpu.json",
    }
    write_contract(paths["measurements_a"], measurements_a)
    write_contract(paths["measurements_b"], measurements_b)
    write_contract(paths["catalog"], catalog)
    write_contract(paths["map"], map_input)
    write_search_config(paths["config_cpu"], replace(config, backend="cpu"))
    write_search_config(paths["config_gpu"], replace(config, backend="gpu"))
    return paths


def _session_sidecar(output_path: Path) -> dict:
    return json.loads(output_path.with_suffix(output_path.suffix + ".telemetry.json").read_text())


@pytest.mark.parametrize("device_ids", [(0,), (0, 1), (0, 1, 2, 3)])
def test_gpu_static_session_matches_fresh_cpu_and_gpu_requests(
    tmp_path: Path, device_ids: tuple[int, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    cp = pytest.importorskip("cupy", reason="locked GPU extra is required")
    if cp.cuda.runtime.getDeviceCount() < len(device_ids):
        pytest.skip("requested GPU device count is unavailable")
    paths = _write_static_session_fixture(tmp_path)
    session = SearchV2Session(paths["catalog"], paths["map"], "EPSG:32618")
    static_snapshot = session._snapshot
    static_ids = (
        id(static_snapshot.catalog),
        id(static_snapshot.map_input),
        id(static_snapshot.vector_map),
    )
    requests = (
        ("a", paths["measurements_a"]),
        ("b", paths["measurements_b"]),
    )
    warm_results = {}
    warm_sidecars = {}
    gpu_memory_by_request = {}
    configurations = (("cpu", (), paths["config_cpu"]), ("gpu", device_ids, paths["config_gpu"]))
    for backend, selected_devices, config_path in configurations:
        for request_name, measurement_path in requests:
            output_path = tmp_path / f"warm-{backend}-{request_name}.json"
            warm_results[backend, request_name] = session.run_request(
                measurement_path,
                config_path,
                output_path,
                device_ids=selected_devices,
                algorithm="grid",
                spacing_m=20.0,
                refine=True,
                refine_modes_count=2,
                refine_initial_step_m=25.0,
                refine_iterations=20,
            )
            warm_sidecars[backend, request_name] = _session_sidecar(output_path)
            if backend == "gpu":
                gpu_memory_by_request[request_name] = {}
                for device_id in selected_devices:
                    with cp.cuda.Device(device_id):
                        pool = cp.get_default_memory_pool()
                        gpu_memory_by_request[request_name][device_id] = (
                            pool.total_bytes(),
                            pool.used_bytes(),
                        )
            assert session._snapshot is static_snapshot
            assert (
                id(session._snapshot.catalog),
                id(session._snapshot.map_input),
                id(session._snapshot.vector_map),
            ) == static_ids
            assert not hasattr(session, "measurements")
            assert not hasattr(session, "scorer")

    fresh_results = {}
    fresh_sidecars = {}
    for backend, selected_devices, config_path in configurations:
        for request_name, measurement_path in requests:
            output_path = tmp_path / f"fresh-{backend}-{request_name}.json"
            fresh_results[backend, request_name] = search_v2_files(
                measurement_path,
                paths["catalog"],
                paths["map"],
                config_path,
                "EPSG:32618",
                output_path,
                device_ids=selected_devices,
                algorithm="grid",
                spacing_m=20.0,
                refine=True,
                refine_modes_count=2,
                refine_initial_step_m=25.0,
                refine_iterations=20,
            )
            fresh_sidecars[backend, request_name] = _session_sidecar(output_path)

    for request_name, _measurement_path in requests:
        cpu_public = warm_sidecars["cpu", request_name]["diagnostics"]["public_evidence"]
        for backend, selected_devices, _config_path in configurations:
            warm = warm_results[backend, request_name]
            fresh = fresh_results[backend, request_name]
            warm_public = warm_sidecars[backend, request_name]["diagnostics"]["public_evidence"]
            fresh_public = fresh_sidecars[backend, request_name]["diagnostics"]["public_evidence"]
            assert warm.hypotheses == fresh.hypotheses
            assert tuple(item.score for item in warm.hypotheses) == tuple(
                item.score for item in fresh.hypotheses
            )
            assert warm.decision_status == fresh.decision_status
            assert warm.search_status == fresh.search_status
            assert warm.budget_limit == fresh.budget_limit
            assert warm.budget_used == fresh.budget_used
            assert warm_public["mode_evidence"] == fresh_public["mode_evidence"]
            assert warm_public["calibration_context"] == fresh_public["calibration_context"]
            assert (
                warm_public["candidate_outcome_counts"] == fresh_public["candidate_outcome_counts"]
            )
            assert (
                warm_sidecars[backend, request_name]["measurement_sha256"]
                == (fresh_sidecars[backend, request_name]["measurement_sha256"])
            )
            telemetry = warm_sidecars[backend, request_name]["telemetry"]
            assert telemetry["device_ids"] == list(selected_devices)
            if backend == "gpu":
                assert telemetry["transfers"]
                gpu_stages = [
                    stage for stage in telemetry["stages"] if stage["name"] == "cost_kernel"
                ]
                assert gpu_stages
                assert all(stage.get("gpu_s") is not None for stage in gpu_stages)
                used_devices = {stage["device_id"] for stage in gpu_stages}
                assert used_devices
                assert used_devices <= set(selected_devices)
                fresh_telemetry = fresh_sidecars[backend, request_name]["telemetry"]
                fresh_gpu_stages = [
                    stage for stage in fresh_telemetry["stages"] if stage["name"] == "cost_kernel"
                ]
                fresh_used_devices = {stage["device_id"] for stage in fresh_gpu_stages}
                assert fresh_used_devices
                assert fresh_used_devices <= set(selected_devices)
                assert warm_public["mode_evidence"] == cpu_public["mode_evidence"]
                assert (
                    warm_public["calibration_context"]["search_execution_sha256"]
                    != cpu_public["calibration_context"]["search_execution_sha256"]
                )
            cpu_result = warm_results["cpu", request_name]
            gpu_result = warm_results["gpu", request_name]
            assert cpu_result.hypotheses == gpu_result.hypotheses
            assert tuple(item.score for item in cpu_result.hypotheses) == tuple(
                item.score for item in gpu_result.hypotheses
            )
            assert cpu_result.decision_status == gpu_result.decision_status
            assert cpu_result.search_status == gpu_result.search_status
            assert cpu_result.budget_limit == gpu_result.budget_limit
            assert cpu_result.budget_used == gpu_result.budget_used
            gpu_context = dict(
                warm_sidecars["gpu", request_name]["diagnostics"]["public_evidence"][
                    "calibration_context"
                ]
            )
            cpu_context = dict(cpu_public["calibration_context"])
            gpu_context.pop("search_execution_sha256")
            cpu_context.pop("search_execution_sha256")
            assert gpu_context == cpu_context
            assert warm_public["candidate_outcome_counts"] == cpu_public["candidate_outcome_counts"]

    assert warm_results["gpu", "a"].hypotheses != warm_results["gpu", "b"].hypotheses
    assert (
        warm_sidecars["cpu", "a"]["measurement_sha256"]
        != warm_sidecars["cpu", "b"]["measurement_sha256"]
    )
    assert warm_results["cpu", "a"].hypotheses != warm_results["cpu", "b"].hypotheses
    assert warm_results["cpu", "a"].report_count == warm_results["gpu", "a"].report_count
    assert warm_results["cpu", "b"].report_count == warm_results["gpu", "b"].report_count
    assert session._snapshot is static_snapshot
    for device_id in device_ids:
        first_total, first_used = gpu_memory_by_request["a"][device_id]
        second_total, second_used = gpu_memory_by_request["b"][device_id]
        assert first_used == second_used == 0
        assert second_total <= first_total + 64 * 1024 * 1024


def test_gpu_static_session_rejects_changed_cache_input_before_output(tmp_path: Path) -> None:
    paths = _write_static_session_fixture(tmp_path)
    session = SearchV2Session(paths["catalog"], paths["map"], "EPSG:32618")
    paths["map"].write_bytes(paths["map"].read_bytes() + b" ")
    output_path = tmp_path / "must-not-exist.json"
    with pytest.raises(ValueError, match="MapV2 changed after SearchV2Session"):
        session.run_request(
            paths["measurements_a"],
            paths["config_gpu"],
            output_path,
            device_ids=(0,),
            algorithm="grid",
            spacing_m=100.0,
            refine=False,
        )
    assert not output_path.exists()
