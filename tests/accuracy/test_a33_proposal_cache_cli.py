from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from pyproj import Geod, Transformer

import bearing_localization.accuracy_cli as cli
from bearing_localization.accuracy_cli import search_v2_files
from bearing_localization.search.config import SearchConfig
from bearing_localization.search.config_serialization import write_search_config
from bearing_localization.search.proposals.geodesic_wedge import (
    generate_geographic_reverse_wedge_proposals_geodesic,
)
from maritime_localization_shared.contracts import CatalogV2, MapV2
from maritime_localization_shared.contracts.inputs_v2 import (
    CatalogRecordV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime_localization_shared.geospatial.coordinates import LocalFrame
from maritime_localization_shared.serialization import read_contract, write_contract

_TIMESTAMP = "2026-10-25T12:00:00Z"


def _fixture(tmp_path: Path, *, cache: Path | None = None, source_budget: int = 30):
    frame = LocalFrame((0.0, 0.0))
    observer = frame.to_geographic(50.0, 50.0)
    geod = Geod(ellps="WGS84")
    records = []
    measurements = []
    for index, bearing in enumerate((0.0, 90.0, 180.0, 270.0, 45.0)):
        longitude, latitude, _ = geod.fwd(*observer, bearing, 1_000.0)
        records.append(CatalogRecordV2(f"key-{index}", latitude, longitude))
        inverse_bearing, _, _ = geod.inv(*observer, longitude, latitude)
        measurements.append(MeasurementV2(inverse_bearing % 360.0, _TIMESTAMP, sigma_deg=0.5))
    catalog_path = tmp_path / "catalog.json"
    measurements_path = tmp_path / "measurements.json"
    map_path = tmp_path / "map.json"
    config_path = tmp_path / "config.json"
    output_path = tmp_path / "estimate.json"
    write_contract(catalog_path, CatalogV2(tuple(records), _TIMESTAMP, "cache-test", "1", "cache"))
    projected_crs = frame.projected_crs.to_string()
    to_projected = Transformer.from_crs("EPSG:4326", projected_crs, always_xy=True)
    origin_x, origin_y = to_projected.transform(*frame.origin_lonlat)
    ring = [
        [origin_x - 10_000.0, origin_y - 10_000.0],
        [origin_x + 10_000.0, origin_y - 10_000.0],
        [origin_x + 10_000.0, origin_y + 10_000.0],
        [origin_x - 10_000.0, origin_y + 10_000.0],
        [origin_x - 10_000.0, origin_y - 10_000.0],
    ]
    write_contract(
        map_path,
        MapV2(
            crs=projected_crs,
            source="cache-test",
            map_version="1",
            coverage_status="known",
            coverage_geometry_geojson=json.dumps(
                {"type": "Polygon", "coordinates": [ring]}, separators=(",", ":")
            ),
        ),
    )
    write_contract(measurements_path, MeasurementSetV2(tuple(measurements), trial_id="cache-test"))
    bounds = (origin_x - 2_000.0, origin_x + 2_000.0, origin_y - 2_000.0, origin_y + 2_000.0)
    config = SearchConfig(
        algorithm="wedge_geodesic",
        bounds_xy=bounds,
        max_range_m=1_500.0,
        candidate_budget=1_000,
        proposal_source_pair_budget=source_budget,
        proposal_cell_budget=100_000,
        proposal_chunk_size=3,
        proposal_cache_path=str(cache) if cache is not None else None,
    )
    write_search_config(config_path, config)
    return (
        measurements_path,
        catalog_path,
        map_path,
        config_path,
        output_path,
        projected_crs,
        config,
    )


def _run(paths, *, output: Path | None = None):
    measurements, catalog, map_path, config, default_output, projected_crs, _ = paths
    return search_v2_files(
        measurements,
        catalog,
        map_path,
        config,
        projected_crs,
        output or default_output,
        wedge_cell_size_m=100.0,
        refine=False,
    )


def _sidecar(output: Path) -> dict:
    return json.loads(output.with_suffix(".json.telemetry.json").read_text())


def test_fresh_and_loaded_proposal_cache_preserve_pool_and_allow_scoring_budget_change(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "proposal-cache"
    paths = _fixture(tmp_path, cache=cache)
    fresh = _run(paths)
    assert fresh.search_status == "completed"
    fresh_sidecar = _sidecar(paths[4])
    assert fresh_sidecar["diagnostics"]["proposal_cache"]["action"] == "generated_and_saved"
    assert fresh_sidecar["diagnostics"]["proposal_generation"]["generation_complete"] is True

    measurements, catalog, map_path, config_path, _, crs, config = paths
    same_output = tmp_path / "same-cache-estimate.json"
    same = search_v2_files(
        measurements,
        catalog,
        map_path,
        config_path,
        crs,
        same_output,
        wedge_cell_size_m=100.0,
        refine=False,
    )
    assert _sidecar(same_output)["diagnostics"]["proposal_cache"]["action"] == "loaded"
    assert same.hypotheses == fresh.hypotheses
    assert same.decision_status == fresh.decision_status
    assert same.search_status == fresh.search_status

    write_search_config(config_path, replace(config, candidate_budget=3))
    warm_output = tmp_path / "warm-estimate.json"
    warm = search_v2_files(
        measurements,
        catalog,
        map_path,
        config_path,
        crs,
        warm_output,
        wedge_cell_size_m=100.0,
        refine=False,
    )
    warm_sidecar = _sidecar(warm_output)
    assert warm_sidecar["diagnostics"]["proposal_cache"]["action"] == "loaded"
    assert (
        warm_sidecar["diagnostics"]["proposal_generation"]["source_pairs_processed"]
        == (fresh_sidecar["diagnostics"]["proposal_generation"]["source_pairs_processed"])
    )
    assert (
        warm_sidecar["diagnostics"]["proposal_generation"]["geodesic_samples_failed"]
        == (fresh_sidecar["diagnostics"]["proposal_generation"]["geodesic_samples_failed"])
    )
    assert warm.search_status == "budget_exhausted"
    assert warm_sidecar["diagnostics"]["exact_scoring"]["candidate_budget"] == 3
    assert warm_sidecar["request_timing"]["result_serialization_completed"] is True
    assert "truth" not in json.dumps(warm_sidecar).lower()


def test_cache_context_mismatch_and_corruption_fail_without_regeneration(tmp_path: Path) -> None:
    cache = tmp_path / "proposal-cache"
    paths = _fixture(tmp_path, cache=cache)
    _run(paths)
    measurements = read_contract(paths[0], MeasurementSetV2)
    changed = replace(
        measurements,
        measurements=(replace(measurements.measurements[0], bearing_deg=1.0),)
        + measurements.measurements[1:],
    )
    write_contract(paths[0], changed)
    with pytest.raises(ValueError, match="context mismatch"):
        _run(paths, output=tmp_path / "changed-output.json")
    assert not (tmp_path / "changed-output.json").exists()

    write_contract(paths[0], measurements)
    metadata = cache / "metadata.json"
    metadata.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt JSON"):
        _run(paths, output=tmp_path / "corrupt-output.json")
    assert not (tmp_path / "corrupt-output.json").exists()


def test_generation_config_or_source_change_rejects_existing_cache(tmp_path: Path, monkeypatch):
    cache = tmp_path / "proposal-cache"
    paths = _fixture(tmp_path, cache=cache)
    _run(paths)
    config = paths[6]
    write_search_config(paths[3], replace(config, proposal_solver_tolerance_deg=5e-8))
    with pytest.raises(ValueError, match="context mismatch"):
        _run(paths, output=tmp_path / "changed-config.json")

    write_search_config(paths[3], config)
    original_digest = cli._proposal_generation_implementation_sha256()
    changed_digest = hashlib.sha256((original_digest + "changed").encode()).hexdigest()
    monkeypatch.setattr(cli, "_proposal_generation_implementation_sha256", lambda: changed_digest)
    with pytest.raises(ValueError, match="context mismatch"):
        _run(paths, output=tmp_path / "changed-source.json")


def test_missing_sigma_uses_generated_default_and_floor_and_remains_in_pool(tmp_path: Path) -> None:
    cache = tmp_path / "proposal-cache"
    paths = _fixture(tmp_path, cache=cache)
    measurements = read_contract(paths[0], MeasurementSetV2)
    values = list(measurements.measurements)
    values[0] = replace(values[0], sigma_deg=None)
    values[1] = replace(values[1], sigma_deg=0.1)
    write_contract(paths[0], replace(measurements, measurements=tuple(values)))
    config = paths[6]
    write_search_config(
        paths[3],
        replace(config, generated_sigma_deg=0.4, sigma_floor_deg=0.3),
    )
    result = _run(paths)
    sidecar = _sidecar(paths[4])
    generation = sidecar["diagnostics"]["proposal_generation"]
    cache_diagnostics = sidecar["diagnostics"]["proposal_cache"]
    assert result.search_status == "completed"
    assert generation["source_pairs_total"] == 25
    assert generation["source_pairs_processed"] == 25
    effective = cache_diagnostics["generation_config"]["effective_observation_sigmas_deg"]
    assert effective[0] == [0, 0.4]
    assert effective[1] == [1, 0.3]


def test_source_pair_budget_and_failed_numerical_samples_survive_cache(tmp_path: Path, monkeypatch):
    cache = tmp_path / "budget-cache"
    paths = _fixture(tmp_path, cache=cache, source_budget=1)
    exhausted = _run(paths)
    first = _sidecar(paths[4])["diagnostics"]["proposal_generation"]
    assert exhausted.search_status == "budget_exhausted"
    assert first["generation_status"] == "budget_exhausted"
    assert first["source_pairs_processed"] == 1
    loaded = _run(paths, output=tmp_path / "loaded-budget.json")
    second = _sidecar(tmp_path / "loaded-budget.json")["diagnostics"]["proposal_generation"]
    assert loaded.search_status == "budget_exhausted"
    assert second["generation_status"] == first["generation_status"]
    assert second["source_pairs_processed"] == first["source_pairs_processed"]
    assert second["generation_budget_reason"] == first["generation_budget_reason"]

    failed_cache = tmp_path / "failed-cache"
    (tmp_path / "failed").mkdir()
    failed_paths = _fixture(tmp_path / "failed", cache=failed_cache)
    original_generator = generate_geographic_reverse_wedge_proposals_geodesic

    def failing_generator(*args, **kwargs):
        generated = original_generator(*args, **kwargs)
        return replace(generated, geodesic_samples_failed=generated.geodesic_samples_failed + 1)

    monkeypatch.setattr(
        cli, "generate_geographic_reverse_wedge_proposals_geodesic", failing_generator
    )
    failed = _run(failed_paths)
    failed_diagnostics = _sidecar(failed_paths[4])["diagnostics"]["proposal_generation"]
    assert failed.search_status == "failed"
    assert failed_diagnostics["generation_complete"] is False
    assert failed_diagnostics["geodesic_samples_failed"] == 1

    monkeypatch.setattr(
        cli, "generate_geographic_reverse_wedge_proposals_geodesic", original_generator
    )
    loaded_failed = _run(failed_paths, output=tmp_path / "loaded-failed.json")
    loaded_diagnostics = _sidecar(tmp_path / "loaded-failed.json")["diagnostics"][
        "proposal_generation"
    ]
    assert loaded_failed.search_status == "failed"
    assert loaded_diagnostics["geodesic_samples_failed"] == 1
    assert loaded_diagnostics["generation_complete"] is False


def test_proposal_implementation_hash_tracks_generation_source_only(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    target = tmp_path / "source"
    relative_paths = (
        "bearing_localization/search/proposals/geodesic_wedge.py",
        "bearing_localization/search/proposals/wedge.py",
        "maritime_localization_shared/geospatial/coordinates.py",
        "maritime_localization_shared/geospatial/reverse_bearing.py",
        "uv.lock",
    )
    for relative in relative_paths:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, destination)
    before = cli._proposal_generation_implementation_sha256(target)
    generator_path = target / relative_paths[0]
    generator_path.write_bytes(generator_path.read_bytes() + b"\n# changed source\n")
    after = cli._proposal_generation_implementation_sha256(target)
    assert before != after
