from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pyproj import Geod, Transformer

from bearing_localization.search.config import SearchConfig
from bearing_localization.search.gpu.search_adapter import (
    GpuCandidateScorer,
    score_candidate_positions_batched,
)
from bearing_localization.search.scoring import CandidateScorer, score_candidate_positions
from bearing_localization.search.telemetry import SearchTelemetry
from maritime_localization_shared.contracts.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MapV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime_localization_shared.geospatial.vector_map import VectorMap


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
from bearing_localization.search.gpu.search_adapter import GpuCandidateScorer
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
    import bearing_localization.search.gpu.search_adapter as adapter

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
    import bearing_localization.search.gpu.search_adapter as adapter

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
