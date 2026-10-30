"""Integration checks for the opt-in experimental GPU candidate pipeline."""

from __future__ import annotations

import pytest
from pyproj import Geod, Transformer
from shapely.geometry import box

cp = pytest.importorskip("cupy")
try:
    _GPU_COUNT = cp.cuda.runtime.getDeviceCount()
except Exception as exc:
    pytest.skip(f"CUDA device unavailable: {exc}", allow_module_level=True)
if _GPU_COUNT < 1:
    pytest.skip("CUDA device unavailable", allow_module_level=True)

from maritime.geometry.vector_map import VectorMap  # noqa: E402
from maritime.schemas.inputs_v2 import (  # noqa: E402
    CatalogRecordV2,
    CatalogV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime.search.config import SearchConfig  # noqa: E402
from maritime.search.gpu.experimental_pipeline import score_candidate_positions_experimental  # noqa: E402
from maritime.search.gpu.search_adapter import GpuCandidateScorer  # noqa: E402
from maritime.search.scoring import CandidateScorer  # noqa: E402


def _fixture():
    crs = "EPSG:32618"
    origin = (500_000.0, 4_095_000.0)
    to_geo = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    to_map = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    lon0, lat0 = to_geo.transform(*origin)
    geod = Geod(ellps="WGS84")

    # The eastern target is hidden behind the island; the other three are clear.
    targets_xy = (
        (1_500.0, 0.0),
        (0.0, 1_400.0),
        (-1_300.0, 0.0),
        (0.0, -1_600.0),
    )
    records = []
    measurements = []
    for index, (east, north) in enumerate(targets_xy):
        lon, lat = to_geo.transform(origin[0] + east, origin[1] + north)
        records.append(
            CatalogRecordV2(
                key=f"target-{index}",
                latitude_deg=lat,
                longitude_deg=lon,
                raw_type="vessel",
                normalized_type="vessel",
            )
        )
        azimuth, _, _ = geod.inv(lon0, lat0, lon, lat)
        measurements.append(
            MeasurementV2(
                bearing_deg=azimuth % 360.0,
                timestamp_utc="2026-10-26T12:00:00Z",
                sigma_deg=0.1,
                object_type="vessel",
            )
        )
    catalog = CatalogV2(tuple(records), "2026-10-26T12:00:00Z", "synthetic", "1", "pipeline-test")
    vector_map = VectorMap(
        box(origin[0] + 400.0, origin[1] - 100.0, origin[0] + 600.0, origin[1] + 100.0),
        crs=crs,
        coverage_geometry=box(
            origin[0] - 5_000.0, origin[1] - 5_000.0, origin[0] + 5_000.0, origin[1] + 5_000.0
        ),
        unknown_geometry=box(
            origin[0] - 2_200.0, origin[1] + 1_800.0, origin[0] - 1_800.0, origin[1] + 2_200.0
        ),
    )
    config = SearchConfig(
        algorithm="pairwise",
        bounds_xy=(origin[0] - 2_500, origin[0] + 2_500, origin[1] - 2_500, origin[1] + 2_500),
        max_range_m=3_000.0,
        candidate_budget=128,
    )
    measurement_set = MeasurementSetV2(tuple(measurements), trial_id="experimental-pipeline")
    return measurement_set, catalog, vector_map, config, origin, to_map


def _assert_cpu_parity(measurements, catalog, vector_map, config, positions, result):
    unique = []
    seen = set()
    for east, north in positions:
        key = round(float(east), 9), round(float(north), 9)
        if key not in seen:
            seen.add(key)
            unique.append(key)
    cpu = CandidateScorer(measurements, catalog, vector_map, config)
    expected = tuple(cpu.evaluate(position) for position in unique)
    assert result.stable_candidate_ids == tuple(range(len(unique)))
    assert tuple(status for _, status in result.candidate_outcomes) == tuple(
        evaluation.status for evaluation in expected
    )
    assert result.run.candidate_count == len(unique)
    actual_by_position = {(h.east_m, h.north_m): h for h in result.run.hypotheses}
    for position, evaluation in zip(unique, expected, strict=True):
        if evaluation.hypothesis is None:
            continue
        actual = actual_by_position[position]
        assert actual.score == pytest.approx(evaluation.hypothesis.score, rel=1e-9, abs=1e-8)
        assert tuple((a.report_index, a.catalog_key) for a in actual.assignments) == tuple(
            (a.report_index, a.catalog_key) for a in evaluation.hypothesis.assignments
        )


def test_experimental_pipeline_preserves_ids_outcomes_and_cpu_objectives():
    measurements, catalog, vector_map, config, origin, _ = _fixture()
    unknown_position = (origin[0] - 2_000.0, origin[1] + 2_000.0)
    land_position = (origin[0] + 500.0, origin[1])
    positions = [
        origin,
        (origin[0] + 1e-10, origin[1]),  # Nine-decimal stable-ID deduplication.
        land_position,
        unknown_position,
        (origin[0] + 100.0, origin[1] + 100.0),
    ]
    scorer = GpuCandidateScorer(
        measurements, catalog, vector_map, config, device_ids=(0,), batch_size=2
    )
    result = score_candidate_positions_experimental(scorer, positions, batch_size=2)
    _assert_cpu_parity(measurements, catalog, vector_map, config, positions, result)
    assert result.candidate_outcomes == (
        (0, "scored"),
        (1, "invalid_water"),
        (2, "unresolved_coverage"),
        (3, "scored"),
    )
    primary = next(item for item in result.run.hypotheses if (item.east_m, item.north_m) == origin)
    matched_keys = {assignment.catalog_key for assignment in primary.assignments}
    assert "target-0" not in matched_keys


def test_ransac_experimental_opt_in_uses_pipeline_and_reports_diagnostics():
    from tools.run_gpu_pair_ransac import run_ransac

    measurements, catalog, vector_map, config, _, _ = _fixture()
    scorer = GpuCandidateScorer(
        measurements, catalog, vector_map, config, device_ids=(0,), batch_size=8
    )

    def callback(*_: object) -> None:
        return None

    run, diagnostics = run_ransac(
        measurements,
        scorer,
        iterations=2_000,
        seed=17,
        batch_size=128,
        cell_size_m=250.0,
        progress=callback,
        consensus_score_limit=8,
        experimental_batched_pipeline=True,
    )
    assert diagnostics["experimental_batched_pipeline"] == 1
    assert diagnostics["pipeline_candidate_ids"] == list(range(run.candidate_count))
    assert len(diagnostics["pipeline_candidate_outcomes"]) == run.candidate_count
    assert diagnostics["pipeline_full_gpu"] == 0
    assert diagnostics["pipeline_cpu_verified_costs_only"] == 1
