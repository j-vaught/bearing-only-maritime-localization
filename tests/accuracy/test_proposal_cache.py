"""Safety and exactness checks for the proposal cache."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from pyproj import Geod

import maritime.search.proposals.cache as cache_module
from maritime.geometry.coordinates import LocalFrame
from maritime.search.proposals.cache import (
    ProposalCacheContext,
    load_proposal_cache,
    save_proposal_cache,
)
from maritime.search.proposals.geodesic_wedge import (
    GeodesicWedgeProposal,
    GeodesicWedgeProposalResult,
    generate_geographic_reverse_wedge_proposals_geodesic,
)
from maritime.search.proposals.wedge import (
    GeographicWedgeCatalogObject,
    WedgeObservation,
)

_GEOD = Geod(ellps="WGS84")


def _context(suffix: str = "") -> ProposalCacheContext:
    def digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    return ProposalCacheContext(
        measurement_sha256=digest("measurements" + suffix),
        catalog_sha256=digest("catalog" + suffix),
        map_sha256=digest("map" + suffix),
        search_config_sha256=digest("config" + suffix),
        implementation_sha256=digest("implementation" + suffix),
    )


def _tiny_real_result(*, max_source_pairs: int | None = None) -> GeodesicWedgeProposalResult:
    origin = (-76.3, 37.0)
    catalog: list[GeographicWedgeCatalogObject] = []
    observations: list[WedgeObservation] = []
    for index, azimuth in enumerate((20.0, 135.0, 260.0)):
        longitude, latitude, _ = _GEOD.fwd(*origin, azimuth, 1_500.0 + 500.0 * index)
        catalog.append(GeographicWedgeCatalogObject(f"key-{index}", longitude, latitude))
        bearing, _, _ = _GEOD.inv(*origin, longitude, latitude)
        observations.append(WedgeObservation(index, float(bearing) % 360.0))
    return generate_geographic_reverse_wedge_proposals_geodesic(
        observations,
        catalog,
        frame=LocalFrame(origin),
        max_range_m=3_000.0,
        cell_size_m=500.0,
        angular_tolerance_deg=0.5,
        max_source_pairs=max_source_pairs,
        max_candidate_cells=10_000,
        source_chunk_size=4,
    )


def _seeded_mock_result() -> GeodesicWedgeProposalResult:
    generator = random.Random(13013)
    cell_i, cell_j = -3, 4
    cell_size = 250.0
    bearing = generator.uniform(0.0, 359.999)
    residual = generator.random()
    evidence = generator.random()
    proposal = GeodesicWedgeProposal(
        candidate_id=f"cell:{cell_i}:{cell_j}",
        cell_i=cell_i,
        cell_j=cell_j,
        east_m=(cell_i + 0.5) * cell_size,
        north_m=(cell_j + 0.5) * cell_size,
        vote_count=1,
        matching_count=1,
        witness_keys=("catalog-a",),
        witness_report_keys=((17, ("catalog-a",)),),
        normalized_angular_residual=residual,
        evidence_score=evidence,
    )
    return GeodesicWedgeProposalResult(
        proposals=(proposal,),
        observations=(WedgeObservation(17, bearing),),
        catalog_keys=("catalog-a", "catalog-b"),
        candidate_cells_total=1,
        source_pairs_total=2,
        source_pairs_processed=2,
        source_pairs_failed=generator.randrange(2),
        geodesic_samples_total=8,
        geodesic_samples_failed=1,
        geodesic_samples_degenerate=2,
        generation_status="completed",
        budget_reason="none",
        frame_origin_lonlat=(-76.3, 37.0),
        frame_crs="EPSG:32618",
        cell_size_m=cell_size,
        max_range_m=1_000.0,
        angular_tolerance_deg=0.5,
        range_step_m=125.0,
        angular_sample_gap_deg=0.1,
        angular_sample_count=7,
        solver_tolerance_deg=1e-7,
        max_solver_residual_deg=2e-8,
    )


def _tamper_payload_offsets(cache_path: Path) -> None:
    payload = cache_path / "payload.npz"
    with np.load(payload, allow_pickle=False) as archive:
        arrays = {name: np.asarray(archive[name]).copy() for name in archive.files}
    arrays["witness_offsets"][0] = 1
    with payload.open("wb") as stream:
        np.savez_compressed(stream, **cast(dict[str, Any], arrays))
    metadata_path = cache_path / "metadata.json"
    envelope = json.loads(metadata_path.read_text())
    envelope["payload_sha256"] = hashlib.sha256(payload.read_bytes()).hexdigest()
    envelope["metadata_sha256"] = hashlib.sha256(
        cache_module._canonical_json(envelope["metadata"])
    ).hexdigest()
    metadata_path.write_bytes(cache_module._canonical_json(envelope))


def test_seeded_mock_roundtrip_preserves_every_result_field(tmp_path: Path) -> None:
    result = _seeded_mock_result()
    cache_path = tmp_path / "mock-cache"

    save_proposal_cache(cache_path, result, context=_context())

    assert load_proposal_cache(cache_path, context=_context()) == result


def test_real_generator_roundtrip_preserves_order_ids_and_ragged_witnesses(
    tmp_path: Path,
) -> None:
    result = _tiny_real_result()
    assert result.proposals
    cache_path = tmp_path / "generated-cache"

    save_proposal_cache(cache_path, result, context=_context())
    loaded = load_proposal_cache(cache_path, context=_context())

    assert loaded == result
    assert loaded.proposal_ids == result.proposal_ids
    assert loaded.proposals[0].witness_report_keys == result.proposals[0].witness_report_keys
    with np.load(cache_path / "payload.npz", allow_pickle=False) as archive:
        assert set(archive.files) == set(cache_module._ARRAY_NAMES)
        assert all(archive[name].dtype != object for name in archive.files)


def test_empty_catalog_result_roundtrips(tmp_path: Path) -> None:
    result = generate_geographic_reverse_wedge_proposals_geodesic(
        (WedgeObservation(0, 20.0),),
        (),
        frame=LocalFrame((-76.3, 37.0)),
        max_range_m=1_000.0,
    )
    assert result.proposals == ()
    cache_path = tmp_path / "empty-cache"

    save_proposal_cache(cache_path, result, context=_context())
    assert load_proposal_cache(cache_path, context=_context()) == result


def test_budget_exhausted_result_roundtrips_with_counters_and_reason(tmp_path: Path) -> None:
    result = _tiny_real_result(max_source_pairs=1)
    assert result.generation_status == "budget_exhausted"
    assert result.budget_reason == "source_pairs"
    assert result.source_pairs_processed == 1
    cache_path = tmp_path / "budget-cache"

    save_proposal_cache(cache_path, result, context=_context())
    assert load_proposal_cache(cache_path, context=_context()) == result


def test_context_mismatch_rejects_cache_instead_of_reusing_it(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache"
    save_proposal_cache(cache_path, _seeded_mock_result(), context=_context())

    with pytest.raises(ValueError, match="context mismatch"):
        load_proposal_cache(cache_path, context=_context("changed"))


def test_context_schema_does_not_accept_truth_or_evaluation_fields() -> None:
    with pytest.raises(TypeError):
        ProposalCacheContext(**{**_context().to_manifest(), "truth_sha256": "0" * 64})


def test_failed_generation_status_is_rejected_as_invalid_cache_metadata(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache"
    save_proposal_cache(cache_path, _seeded_mock_result(), context=_context())
    metadata_path = cache_path / "metadata.json"
    envelope = json.loads(metadata_path.read_text())
    envelope["metadata"]["result"]["generation_status"] = "failed"
    envelope["metadata_sha256"] = hashlib.sha256(
        cache_module._canonical_json(envelope["metadata"])
    ).hexdigest()
    metadata_path.write_bytes(cache_module._canonical_json(envelope))

    with pytest.raises(ValueError, match="Unknown generation status"):
        load_proposal_cache(cache_path, context=_context())


def test_payload_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache"
    save_proposal_cache(cache_path, _seeded_mock_result(), context=_context())
    with (cache_path / "payload.npz").open("ab") as stream:
        stream.write(b"tamper")

    with pytest.raises(ValueError, match="payload SHA-256 mismatch"):
        load_proposal_cache(cache_path, context=_context())


def test_invalid_ragged_offsets_are_rejected_after_valid_hashes(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache"
    save_proposal_cache(cache_path, _seeded_mock_result(), context=_context())
    _tamper_payload_offsets(cache_path)

    with pytest.raises(ValueError, match="ragged offsets"):
        load_proposal_cache(cache_path, context=_context())


@pytest.mark.parametrize("missing", ["metadata.json", "payload.npz"])
def test_incomplete_cache_fails_clearly(tmp_path: Path, missing: str) -> None:
    cache_path = tmp_path / "incomplete"
    save_proposal_cache(cache_path, _seeded_mock_result(), context=_context())
    (cache_path / missing).unlink()

    with pytest.raises(ValueError, match="Incomplete proposal cache"):
        load_proposal_cache(cache_path, context=_context())


def test_write_refuses_to_breach_free_space_reserve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = tmp_path / "cache"
    monkeypatch.setattr(cache_module, "_MIN_FREE_BYTES", 10**18)

    with pytest.raises(OSError, match="40 GiB free-space reserve"):
        save_proposal_cache(cache_path, _seeded_mock_result(), context=_context())
    assert not cache_path.exists()


def test_existing_cache_is_write_once(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache"
    result = _seeded_mock_result()
    save_proposal_cache(cache_path, result, context=_context())

    with pytest.raises(FileExistsError, match="already exists"):
        save_proposal_cache(cache_path, result, context=_context())


def test_result_validation_builds_catalog_lookup_once_for_many_proposals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _tiny_real_result()
    template = base.proposals[0]
    cell_size = base.cell_size_m
    proposals = tuple(
        replace(
            template,
            candidate_id=f"cell:{index}:0",
            cell_i=index,
            cell_j=0,
            east_m=(index + 0.5) * cell_size,
            north_m=0.5 * cell_size,
        )
        for index in range(64)
    )
    result = replace(base, proposals=proposals, candidate_cells_total=len(proposals))
    original = cache_module._catalog_positions
    calls = 0

    def counted_catalog_positions(catalog_keys: tuple[str, ...]) -> dict[str, int]:
        nonlocal calls
        calls += 1
        return original(catalog_keys)

    monkeypatch.setattr(cache_module, "_catalog_positions", counted_catalog_positions)
    cache_module._validate_result(result)

    assert calls == 1
