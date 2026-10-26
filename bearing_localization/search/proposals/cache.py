"""Bounded, context-keyed cache for geodesic wedge proposal results.

The context manifest is intentionally limited to hashes of measurements,
catalog, map, search configuration, and implementation. It accepts no truth or
evaluation fields. Proposal arrays are stored as numeric NPZ members with
pickle disabled; identifying strings appear once in strict versioned JSON.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

from .geodesic_wedge import (
    GeodesicWedgeProposal,
    GeodesicWedgeProposalResult,
)
from .wedge import WedgeObservation

_SCHEMA_VERSION = 1
_MIN_FREE_BYTES = 40 * 1024**3
_MAX_METADATA_BYTES = 512 * 1024**2
_MAX_ARRAY_BYTES = 8 * 1024**3
_MAX_PROPOSALS = 10_000_000
_MAX_WITNESS_REPORTS = 100_000_000
_MAX_WITNESS_CATALOG_REFS = 200_000_000
_ARRAY_NAMES = (
    "candidate_cells",
    "candidate_coordinates",
    "candidate_counts",
    "candidate_quality",
    "witness_offsets",
    "witness_report_positions",
    "witness_catalog_offsets",
    "witness_catalog_indices",
)
_ARRAY_DTYPES = {
    "candidate_cells": np.dtype(np.int64),
    "candidate_coordinates": np.dtype(np.float64),
    "candidate_counts": np.dtype(np.int64),
    "candidate_quality": np.dtype(np.float64),
    "witness_offsets": np.dtype(np.int64),
    "witness_report_positions": np.dtype(np.int64),
    "witness_catalog_offsets": np.dtype(np.int64),
    "witness_catalog_indices": np.dtype(np.int64),
}


@dataclass(frozen=True, slots=True)
class ProposalCacheContext:
    """Immutable digests identifying all inputs that affect proposal output.

    Only measurement, catalog, map, search configuration, and implementation
    digests are accepted. Truth and evaluator data are outside this schema.
    """

    measurement_sha256: str
    catalog_sha256: str
    map_sha256: str
    search_config_sha256: str
    implementation_sha256: str

    def __post_init__(self) -> None:
        for name, value in self.to_manifest().items():
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"{name} must be a lowercase SHA-256 hex digest.")

    def to_manifest(self) -> dict[str, str]:
        """Return the strict allow-listed context manifest."""
        return {
            "measurement_sha256": self.measurement_sha256,
            "catalog_sha256": self.catalog_sha256,
            "map_sha256": self.map_sha256,
            "search_config_sha256": self.search_config_sha256,
            "implementation_sha256": self.implementation_sha256,
        }

    @property
    def context_hash(self) -> str:
        """Return the stable SHA-256 key for this immutable context."""
        return _sha256_bytes(_canonical_json(self.to_manifest()))


def save_proposal_cache(
    cache_path: str | os.PathLike[str],
    result: GeodesicWedgeProposalResult,
    *,
    context: ProposalCacheContext,
) -> None:
    """Atomically publish one complete, write-once proposal cache directory.

    Existing destinations are rejected instead of being replaced, so readers
    never observe a partially replaced cache. The parent filesystem must retain
    at least 40 GiB free after a conservative pre-write estimate.
    """
    target = Path(cache_path)
    parent = target.parent
    if not isinstance(result, GeodesicWedgeProposalResult):
        raise TypeError("result must be a GeodesicWedgeProposalResult.")
    if not isinstance(context, ProposalCacheContext):
        raise TypeError("context must be a ProposalCacheContext.")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Proposal cache already exists: {target}")
    if not parent.is_dir():
        raise FileNotFoundError(f"Cache parent directory does not exist: {parent}")

    core, arrays = _encode_result(result, context)
    json_bytes = _canonical_json(core)
    if len(json_bytes) > _MAX_METADATA_BYTES:
        raise ValueError("Proposal cache metadata exceeds the configured size limit.")
    _check_disk_reserve(parent, _estimate_write_bytes(result, len(json_bytes)))

    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=parent))
    try:
        payload_path = temporary / "payload.npz"
        with payload_path.open("wb") as stream:
            np.savez_compressed(stream, allow_pickle=False, **cast(dict[str, Any], arrays))
            stream.flush()
            os.fsync(stream.fileno())
        payload_hash = _sha256_file(payload_path)
        envelope = {
            "metadata": core,
            "metadata_sha256": _sha256_bytes(json_bytes),
            "payload_sha256": payload_hash,
        }
        metadata_bytes = _canonical_json(envelope)
        if len(metadata_bytes) > _MAX_METADATA_BYTES:
            raise ValueError("Proposal cache metadata exceeds the configured size limit.")
        metadata_path = temporary / "metadata.json"
        with metadata_path.open("wb") as stream:
            stream.write(metadata_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        directory_fd = os.open(temporary, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        os.rename(temporary, target)
        parent_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def load_proposal_cache(
    cache_path: str | os.PathLike[str],
    *,
    context: ProposalCacheContext,
) -> GeodesicWedgeProposalResult:
    """Load and validate a complete cache for the exact supplied context."""
    if not isinstance(context, ProposalCacheContext):
        raise TypeError("context must be a ProposalCacheContext.")
    target = Path(cache_path)
    metadata_path = target / "metadata.json"
    payload_path = target / "payload.npz"
    if not target.is_dir() or not metadata_path.is_file() or not payload_path.is_file():
        raise ValueError(f"Incomplete proposal cache at {target}.")
    metadata_size = metadata_path.stat().st_size
    if metadata_size <= 0 or metadata_size > _MAX_METADATA_BYTES:
        raise ValueError("Proposal cache metadata has an invalid size.")
    try:
        envelope = json.loads(
            metadata_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Proposal cache metadata is corrupt JSON.") from exc
    if not isinstance(envelope, dict) or set(envelope) != {
        "metadata",
        "metadata_sha256",
        "payload_sha256",
    }:
        raise ValueError("Proposal cache metadata envelope has invalid fields.")
    core = envelope["metadata"]
    if not isinstance(core, dict):
        raise ValueError("Proposal cache metadata body must be an object.")
    expected_metadata_hash = _sha256_bytes(_canonical_json(core))
    if envelope["metadata_sha256"] != expected_metadata_hash:
        raise ValueError("Proposal cache metadata SHA-256 mismatch.")
    if envelope["payload_sha256"] != _sha256_file(payload_path):
        raise ValueError("Proposal cache payload SHA-256 mismatch.")
    _validate_metadata_schema(core)
    if core["context_manifest"] != context.to_manifest():
        raise ValueError("Proposal cache context mismatch.")
    if core["context_sha256"] != context.context_hash:
        raise ValueError("Proposal cache context hash mismatch.")
    arrays = _load_numeric_npz(payload_path, core)
    return _decode_result(core, arrays)


def _encode_result(
    result: GeodesicWedgeProposalResult,
    context: ProposalCacheContext,
) -> tuple[dict[str, Any], dict[str, NDArray[np.generic]]]:
    _validate_result(result)
    proposals = result.proposals
    observations = result.observations
    report_position = {item.report_index: index for index, item in enumerate(observations)}
    catalog_position = {key: index for index, key in enumerate(result.catalog_keys)}

    cells = np.asarray([(item.cell_i, item.cell_j) for item in proposals], dtype=np.int64).reshape(
        (-1, 2)
    )
    coordinates = np.asarray(
        [(item.east_m, item.north_m) for item in proposals], dtype=np.float64
    ).reshape((-1, 2))
    counts = np.asarray(
        [(item.vote_count, item.matching_count) for item in proposals], dtype=np.int64
    ).reshape((-1, 2))
    quality = np.asarray(
        [(item.normalized_angular_residual, item.evidence_score) for item in proposals],
        dtype=np.float64,
    ).reshape((-1, 2))
    witness_offsets = [0]
    report_positions: list[int] = []
    catalog_offsets = [0]
    catalog_indices: list[int] = []
    for proposal in proposals:
        for report_index, keys in proposal.witness_report_keys:
            report_positions.append(report_position[report_index])
            catalog_indices.extend(catalog_position[key] for key in keys)
            catalog_offsets.append(len(catalog_indices))
        witness_offsets.append(len(report_positions))

    arrays: dict[str, NDArray[np.generic]] = {
        "candidate_cells": cells,
        "candidate_coordinates": coordinates,
        "candidate_counts": counts,
        "candidate_quality": quality,
        "witness_offsets": np.asarray(witness_offsets, dtype=np.int64),
        "witness_report_positions": np.asarray(report_positions, dtype=np.int64),
        "witness_catalog_offsets": np.asarray(catalog_offsets, dtype=np.int64),
        "witness_catalog_indices": np.asarray(catalog_indices, dtype=np.int64),
    }
    core: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "context_manifest": context.to_manifest(),
        "context_sha256": context.context_hash,
        "proposal_count": len(proposals),
        "witness_report_count": len(report_positions),
        "witness_catalog_ref_count": len(catalog_indices),
        "proposal_ids": [item.candidate_id for item in proposals],
        "observations": [[item.report_index, item.bearing_deg] for item in observations],
        "catalog_keys": list(result.catalog_keys),
        "result": {
            "candidate_cells_total": result.candidate_cells_total,
            "source_pairs_total": result.source_pairs_total,
            "source_pairs_processed": result.source_pairs_processed,
            "source_pairs_failed": result.source_pairs_failed,
            "geodesic_samples_total": result.geodesic_samples_total,
            "geodesic_samples_failed": result.geodesic_samples_failed,
            "geodesic_samples_degenerate": result.geodesic_samples_degenerate,
            "generation_status": result.generation_status,
            "budget_reason": result.budget_reason,
            "frame_origin_lonlat": list(result.frame_origin_lonlat),
            "frame_crs": result.frame_crs,
            "cell_size_m": result.cell_size_m,
            "max_range_m": result.max_range_m,
            "angular_tolerance_deg": result.angular_tolerance_deg,
            "range_step_m": result.range_step_m,
            "angular_sample_gap_deg": result.angular_sample_gap_deg,
            "angular_sample_count": result.angular_sample_count,
            "solver_tolerance_deg": result.solver_tolerance_deg,
            "max_solver_residual_deg": result.max_solver_residual_deg,
        },
    }
    return core, arrays


def _decode_result(
    core: dict[str, Any],
    arrays: dict[str, NDArray[np.generic]],
) -> GeodesicWedgeProposalResult:
    result_meta = core["result"]
    observations = tuple(
        WedgeObservation(int(row[0]), float(row[1])) for row in core["observations"]
    )
    catalog_keys = tuple(core["catalog_keys"])
    proposals: list[GeodesicWedgeProposal] = []
    cells = cast(NDArray[np.int64], arrays["candidate_cells"])
    coordinates = cast(NDArray[np.float64], arrays["candidate_coordinates"])
    counts = cast(NDArray[np.int64], arrays["candidate_counts"])
    quality = cast(NDArray[np.float64], arrays["candidate_quality"])
    witness_offsets = cast(NDArray[np.int64], arrays["witness_offsets"])
    report_positions = cast(NDArray[np.int64], arrays["witness_report_positions"])
    catalog_offsets = cast(NDArray[np.int64], arrays["witness_catalog_offsets"])
    catalog_indices = cast(NDArray[np.int64], arrays["witness_catalog_indices"])
    for proposal_index, candidate_id in enumerate(core["proposal_ids"]):
        start = int(witness_offsets[proposal_index])
        stop = int(witness_offsets[proposal_index + 1])
        report_keys: list[tuple[int, tuple[str, ...]]] = []
        for witness_index in range(start, stop):
            report_index = observations[int(report_positions[witness_index])].report_index
            catalog_start = int(catalog_offsets[witness_index])
            catalog_stop = int(catalog_offsets[witness_index + 1])
            keys = tuple(
                catalog_keys[int(index)] for index in catalog_indices[catalog_start:catalog_stop]
            )
            report_keys.append((report_index, keys))
        witness_keys = tuple(sorted({key for _, keys in report_keys for key in keys}))
        proposals.append(
            GeodesicWedgeProposal(
                candidate_id=candidate_id,
                cell_i=int(cells[proposal_index, 0]),
                cell_j=int(cells[proposal_index, 1]),
                east_m=float(coordinates[proposal_index, 0]),
                north_m=float(coordinates[proposal_index, 1]),
                vote_count=int(counts[proposal_index, 0]),
                matching_count=int(counts[proposal_index, 1]),
                witness_keys=witness_keys,
                witness_report_keys=tuple(report_keys),
                normalized_angular_residual=float(quality[proposal_index, 0]),
                evidence_score=float(quality[proposal_index, 1]),
            )
        )
    proposal_result = GeodesicWedgeProposalResult(
        proposals=tuple(proposals),
        observations=observations,
        catalog_keys=catalog_keys,
        candidate_cells_total=result_meta["candidate_cells_total"],
        source_pairs_total=result_meta["source_pairs_total"],
        source_pairs_processed=result_meta["source_pairs_processed"],
        source_pairs_failed=result_meta["source_pairs_failed"],
        geodesic_samples_total=result_meta["geodesic_samples_total"],
        geodesic_samples_failed=result_meta["geodesic_samples_failed"],
        geodesic_samples_degenerate=result_meta["geodesic_samples_degenerate"],
        generation_status=result_meta["generation_status"],
        budget_reason=result_meta["budget_reason"],
        frame_origin_lonlat=tuple(result_meta["frame_origin_lonlat"]),
        frame_crs=result_meta["frame_crs"],
        cell_size_m=result_meta["cell_size_m"],
        max_range_m=result_meta["max_range_m"],
        angular_tolerance_deg=result_meta["angular_tolerance_deg"],
        range_step_m=result_meta["range_step_m"],
        angular_sample_gap_deg=result_meta["angular_sample_gap_deg"],
        angular_sample_count=result_meta["angular_sample_count"],
        solver_tolerance_deg=result_meta["solver_tolerance_deg"],
        max_solver_residual_deg=result_meta["max_solver_residual_deg"],
    )
    _validate_result(proposal_result)
    return proposal_result


def _validate_result(result: GeodesicWedgeProposalResult) -> None:
    proposals = result.proposals
    observations = result.observations
    catalog_keys = result.catalog_keys
    if not observations:
        raise ValueError("At least one observation is required for a proposal result.")
    if len(proposals) > _MAX_PROPOSALS:
        raise ValueError("Proposal count exceeds the cache safety limit.")
    if len({item.candidate_id for item in proposals}) != len(proposals):
        raise ValueError("Proposal candidate IDs must be unique.")
    if len({(item.cell_i, item.cell_j) for item in proposals}) != len(proposals):
        raise ValueError("Proposal grid cells must be unique.")
    if any(not isinstance(key, str) or not key for key in catalog_keys):
        raise ValueError("Catalog keys must be nonempty strings.")
    if tuple(sorted(catalog_keys)) != catalog_keys or len(set(catalog_keys)) != len(catalog_keys):
        raise ValueError("Catalog keys must be unique and sorted.")
    if any(not isinstance(item, WedgeObservation) for item in observations):
        raise ValueError("Observations must contain WedgeObservation records.")
    if any(not isinstance(item, GeodesicWedgeProposal) for item in proposals):
        raise ValueError("Proposals must contain GeodesicWedgeProposal records.")
    report_ids = tuple(item.report_index for item in observations)
    if report_ids != tuple(sorted(set(report_ids))):
        raise ValueError("Observation report indices must be unique and sorted.")
    if any(
        type(item.report_index) is not int
        or item.report_index < 0
        or not math.isfinite(item.bearing_deg)
        or not 0.0 <= item.bearing_deg < 360.0
        for item in observations
    ):
        raise ValueError("Observations contain invalid report indices or bearings.")
    if result.candidate_cells_total != len(proposals):
        raise ValueError("candidate_cells_total must equal the proposal count.")
    if result.source_pairs_total != len(observations) * len(catalog_keys):
        raise ValueError("source_pairs_total disagrees with observations and catalog.")
    for name in (
        "source_pairs_total",
        "source_pairs_processed",
        "source_pairs_failed",
        "geodesic_samples_total",
        "geodesic_samples_failed",
        "geodesic_samples_degenerate",
        "candidate_cells_total",
        "angular_sample_count",
    ):
        value = getattr(result, name)
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a nonnegative integer.")
    if result.source_pairs_processed > result.source_pairs_total:
        raise ValueError("source_pairs_processed exceeds source_pairs_total.")
    if result.source_pairs_failed > result.source_pairs_processed:
        raise ValueError("source_pairs_failed exceeds processed source pairs.")
    if (
        result.geodesic_samples_failed + result.geodesic_samples_degenerate
        > result.geodesic_samples_total
    ):
        raise ValueError("Geodesic sample counters are inconsistent.")
    if result.generation_status == "completed":
        if (
            result.budget_reason != "none"
            or result.source_pairs_processed != result.source_pairs_total
        ):
            raise ValueError("Completed generation requires all source pairs and no budget reason.")
    elif result.generation_status == "budget_exhausted":
        if result.budget_reason == "none":
            raise ValueError("Budget-exhausted generation requires a budget reason.")
        if result.budget_reason == "source_pairs" and (
            result.source_pairs_processed >= result.source_pairs_total
        ):
            raise ValueError("Source-pair budget status requires unprocessed source pairs.")
    else:
        raise ValueError("Unknown generation_status.")
    if result.budget_reason not in ("none", "source_pairs", "candidate_cells"):
        raise ValueError("Unknown budget_reason.")
    if result.generation_status == "completed" and result.source_pairs_total == 0:
        if proposals:
            raise ValueError("An empty source catalog cannot produce proposals.")
    origin_lon, origin_lat = result.frame_origin_lonlat
    if (
        not math.isfinite(origin_lon)
        or not math.isfinite(origin_lat)
        or not -180.0 <= origin_lon <= 180.0
        or not -90.0 <= origin_lat <= 90.0
    ):
        raise ValueError("frame_origin_lonlat is invalid.")
    if not isinstance(result.frame_crs, str) or not result.frame_crs:
        raise ValueError("frame_crs must be a nonempty string.")
    for name in (
        "cell_size_m",
        "max_range_m",
        "range_step_m",
        "angular_sample_gap_deg",
        "solver_tolerance_deg",
    ):
        value = getattr(result, name)
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(f"{name} must be finite and positive.")
    if (
        not math.isfinite(result.angular_tolerance_deg)
        or not 0.0 <= result.angular_tolerance_deg < 90.0
    ):
        raise ValueError("angular_tolerance_deg is invalid.")
    if not math.isfinite(result.max_solver_residual_deg) or result.max_solver_residual_deg < 0.0:
        raise ValueError("max_solver_residual_deg must be finite and nonnegative.")
    proposal_reports = {item.report_index for item in observations}
    proposal_keys = set(catalog_keys)
    catalog_position = _catalog_positions(catalog_keys)
    for item in proposals:
        if item.candidate_id != f"cell:{item.cell_i}:{item.cell_j}":
            raise ValueError("Proposal candidate ID does not match its cell.")
        if item.vote_count != len(item.witness_report_keys):
            raise ValueError("Proposal vote_count must equal witness report count.")
        report_order = tuple(index for index, _ in item.witness_report_keys)
        if report_order != tuple(sorted(set(report_order))):
            raise ValueError("Proposal witness report indices must be unique and sorted.")
        all_keys: set[str] = set()
        for report_index, keys in item.witness_report_keys:
            if report_index not in proposal_reports:
                raise ValueError("Proposal witness references an unknown report index.")
            if not keys or keys != tuple(sorted(set(keys))):
                raise ValueError("Witness catalog keys must be nonempty, unique, and sorted.")
            if not set(keys) <= proposal_keys:
                raise ValueError("Proposal witness references an unknown catalog key.")
            all_keys.update(keys)
        if item.witness_keys != tuple(sorted(all_keys)):
            raise ValueError("Proposal witness_keys do not equal the union of report witnesses.")
        if not 0 <= item.matching_count <= min(item.vote_count, len(item.witness_keys)):
            raise ValueError("Proposal matching_count is inconsistent with its witnesses.")
        report_graph = {
            report_index: [catalog_position[key] for key in keys]
            for report_index, keys in item.witness_report_keys
        }
        if _maximum_catalog_matching(report_graph) != item.matching_count:
            raise ValueError("Proposal matching_count disagrees with its witness graph.")
        if not math.isfinite(item.east_m) or not math.isfinite(item.north_m):
            raise ValueError("Proposal coordinates must be finite.")
        expected_east = (item.cell_i + 0.5) * result.cell_size_m
        expected_north = (item.cell_j + 0.5) * result.cell_size_m
        if not math.isclose(item.east_m, expected_east, rel_tol=0.0, abs_tol=1e-8):
            raise ValueError("Proposal east_m does not match its cell coordinates.")
        if not math.isclose(item.north_m, expected_north, rel_tol=0.0, abs_tol=1e-8):
            raise ValueError("Proposal north_m does not match its cell coordinates.")
        if (
            not math.isfinite(item.normalized_angular_residual)
            or not 0.0 <= item.normalized_angular_residual <= 1.0
            or not math.isfinite(item.evidence_score)
            or item.evidence_score < 0.0
        ):
            raise ValueError("Proposal residual and evidence values are invalid.")
    ranking = tuple(item.ranking_key for item in proposals)
    if ranking != tuple(sorted(ranking)):
        raise ValueError("Proposal order does not match deterministic ranking order.")


def _validate_metadata_schema(core: dict[str, Any]) -> None:
    expected_keys = {
        "schema_version",
        "context_manifest",
        "context_sha256",
        "proposal_count",
        "witness_report_count",
        "witness_catalog_ref_count",
        "proposal_ids",
        "observations",
        "catalog_keys",
        "result",
    }
    if set(core) != expected_keys:
        raise ValueError("Proposal cache metadata has invalid fields.")
    if type(core["schema_version"]) is not int or core["schema_version"] != _SCHEMA_VERSION:
        raise ValueError("Unsupported proposal cache schema version.")
    _validate_context_manifest(core["context_manifest"])
    context_sha256 = core["context_sha256"]
    if (
        not isinstance(context_sha256, str)
        or len(context_sha256) != 64
        or any(character not in "0123456789abcdef" for character in context_sha256)
        or context_sha256 != _sha256_bytes(_canonical_json(core["context_manifest"]))
    ):
        raise ValueError("Proposal cache context hash is invalid.")
    for field, maximum in (
        ("proposal_count", _MAX_PROPOSALS),
        ("witness_report_count", _MAX_WITNESS_REPORTS),
        ("witness_catalog_ref_count", _MAX_WITNESS_CATALOG_REFS),
    ):
        value = core[field]
        if type(value) is not int or not 0 <= value <= maximum:
            raise ValueError(f"Invalid {field} in proposal cache metadata.")
    if (
        not isinstance(core["proposal_ids"], list)
        or len(core["proposal_ids"]) != core["proposal_count"]
    ):
        raise ValueError("Proposal ID count does not match proposal_count.")
    if any(not isinstance(value, str) or not value for value in core["proposal_ids"]):
        raise ValueError("Proposal IDs must be nonempty strings.")
    if len(set(core["proposal_ids"])) != len(core["proposal_ids"]):
        raise ValueError("Proposal IDs must be unique.")
    if not isinstance(core["observations"], list) or not isinstance(core["catalog_keys"], list):
        raise ValueError("Observation and catalog metadata must be arrays.")
    if len(core["catalog_keys"]) > _MAX_WITNESS_CATALOG_REFS:
        raise ValueError("Catalog key count exceeds the cache safety limit.")
    if any(not isinstance(key, str) or not key for key in core["catalog_keys"]):
        raise ValueError("Catalog keys must be nonempty strings.")
    if core["catalog_keys"] != sorted(set(core["catalog_keys"])):
        raise ValueError("Catalog keys must be unique and sorted.")
    if any(
        not isinstance(row, list)
        or len(row) != 2
        or type(row[0]) is not int
        or row[0] < 0
        or type(row[1]) not in (int, float)
        or not math.isfinite(row[1])
        or not 0.0 <= row[1] < 360.0
        for row in core["observations"]
    ):
        raise ValueError("Invalid observation records in metadata.")
    report_indices = [row[0] for row in core["observations"]]
    if report_indices != sorted(set(report_indices)):
        raise ValueError("Observation report indices must be unique and sorted.")
    result = core["result"]
    if not isinstance(result, dict):
        raise ValueError("Result metadata must be an object.")
    expected_result_keys = {
        "candidate_cells_total",
        "source_pairs_total",
        "source_pairs_processed",
        "source_pairs_failed",
        "geodesic_samples_total",
        "geodesic_samples_failed",
        "geodesic_samples_degenerate",
        "generation_status",
        "budget_reason",
        "frame_origin_lonlat",
        "frame_crs",
        "cell_size_m",
        "max_range_m",
        "angular_tolerance_deg",
        "range_step_m",
        "angular_sample_gap_deg",
        "angular_sample_count",
        "solver_tolerance_deg",
        "max_solver_residual_deg",
    }
    if set(result) != expected_result_keys:
        raise ValueError("Proposal result metadata has invalid fields.")
    integer_fields = (
        "candidate_cells_total",
        "source_pairs_total",
        "source_pairs_processed",
        "source_pairs_failed",
        "geodesic_samples_total",
        "geodesic_samples_failed",
        "geodesic_samples_degenerate",
        "angular_sample_count",
    )
    for field in integer_fields:
        value = result[field]
        if type(value) is not int or value < 0:
            raise ValueError(f"Invalid result counter: {field}.")
    if result["candidate_cells_total"] != core["proposal_count"]:
        raise ValueError("Proposal count disagrees with result metadata.")
    if result["source_pairs_total"] != len(core["observations"]) * len(core["catalog_keys"]):
        raise ValueError("Source-pair counter disagrees with observation and catalog keys.")
    if result["source_pairs_processed"] > result["source_pairs_total"]:
        raise ValueError("Processed source-pair count exceeds total.")
    if result["source_pairs_failed"] > result["source_pairs_processed"]:
        raise ValueError("Failed source-pair count exceeds processed.")
    if (
        result["geodesic_samples_failed"] + result["geodesic_samples_degenerate"]
        > result["geodesic_samples_total"]
    ):
        raise ValueError("Geodesic sample counters are inconsistent.")
    if result["generation_status"] == "completed":
        if (
            result["budget_reason"] != "none"
            or result["source_pairs_processed"] != result["source_pairs_total"]
        ):
            raise ValueError("Completed generation has inconsistent budget counters.")
    elif result["generation_status"] == "budget_exhausted":
        if result["budget_reason"] not in ("source_pairs", "candidate_cells"):
            raise ValueError("Budget-exhausted generation has invalid budget reason.")
        if (
            result["budget_reason"] == "source_pairs"
            and result["source_pairs_processed"] >= result["source_pairs_total"]
        ):
            raise ValueError("Source-pair budget status has no remaining source pairs.")
    else:
        raise ValueError("Unknown generation status.")
    if result["budget_reason"] not in ("none", "source_pairs", "candidate_cells"):
        raise ValueError("Unknown budget reason.")
    origin = result["frame_origin_lonlat"]
    if (
        not isinstance(origin, list)
        or len(origin) != 2
        or any(type(value) not in (int, float) or not math.isfinite(value) for value in origin)
        or not -180.0 <= origin[0] <= 180.0
        or not -90.0 <= origin[1] <= 90.0
    ):
        raise ValueError("Invalid frame origin in result metadata.")
    if not isinstance(result["frame_crs"], str) or not result["frame_crs"]:
        raise ValueError("Invalid frame CRS in result metadata.")
    for field in (
        "cell_size_m",
        "max_range_m",
        "range_step_m",
        "angular_sample_gap_deg",
        "solver_tolerance_deg",
    ):
        value = result[field]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"Invalid result parameter: {field}.")
    for field in ("angular_tolerance_deg", "max_solver_residual_deg"):
        value = result[field]
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"Invalid result parameter: {field}.")
    if not 0.0 <= result["angular_tolerance_deg"] < 90.0:
        raise ValueError("Invalid angular tolerance in result metadata.")
    if result["max_solver_residual_deg"] < 0.0:
        raise ValueError("Invalid solver residual in result metadata.")
    if core["witness_report_count"] > _MAX_WITNESS_REPORTS:
        raise ValueError("Witness report count exceeds the cache safety limit.")
    if core["witness_catalog_ref_count"] > _MAX_WITNESS_CATALOG_REFS:
        raise ValueError("Witness catalog reference count exceeds the safety limit.")


def _validate_context_manifest(manifest: Any) -> None:
    expected = {
        "measurement_sha256",
        "catalog_sha256",
        "map_sha256",
        "search_config_sha256",
        "implementation_sha256",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected:
        raise ValueError("Context manifest has fields outside the cache allowlist.")
    for key, value in manifest.items():
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError(f"Invalid context digest: {key}.")


def _load_numeric_npz(path: Path, core: dict[str, Any]) -> dict[str, NDArray[np.generic]]:
    expected_files = {f"{name}.npy" for name in _ARRAY_NAMES}
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            if (
                len(infos) != len(expected_files)
                or {item.filename for item in infos} != expected_files
            ):
                raise ValueError("Proposal cache NPZ has unexpected members.")
            if sum(item.file_size for item in infos) > _MAX_ARRAY_BYTES:
                raise ValueError("Proposal cache arrays exceed the decompression size limit.")
            expected_shapes = {
                "candidate_cells": (core["proposal_count"], 2),
                "candidate_coordinates": (core["proposal_count"], 2),
                "candidate_counts": (core["proposal_count"], 2),
                "candidate_quality": (core["proposal_count"], 2),
                "witness_offsets": (core["proposal_count"] + 1,),
                "witness_report_positions": (core["witness_report_count"],),
                "witness_catalog_offsets": (core["witness_report_count"] + 1,),
                "witness_catalog_indices": (core["witness_catalog_ref_count"],),
            }
            for info in infos:
                name = info.filename[:-4]
                with archive.open(info, "r") as stream:
                    version = np.lib.format.read_magic(stream)
                    if version == (1, 0):
                        shape, fortran, dtype = np.lib.format.read_array_header_1_0(stream)
                    elif version == (2, 0):
                        shape, fortran, dtype = np.lib.format.read_array_header_2_0(stream)
                    else:
                        raise ValueError(f"Unsupported NPY version for {name}.")
                    header_bytes = stream.tell()
                if shape != expected_shapes[name] or fortran:
                    raise ValueError(f"Invalid shape or layout for proposal array {name}.")
                if dtype != _ARRAY_DTYPES[name] or dtype.hasobject:
                    raise ValueError(f"Invalid or unsafe dtype for proposal array {name}.")
                byte_count = math.prod(shape) * dtype.itemsize
                if byte_count > _MAX_ARRAY_BYTES or info.file_size != header_bytes + byte_count:
                    raise ValueError(f"Invalid decompressed size for proposal array {name}.")
        with np.load(path, allow_pickle=False) as npz:
            if set(npz.files) != set(_ARRAY_NAMES):
                raise ValueError("Proposal cache NPZ array names are invalid.")
            arrays = {name: np.asarray(npz[name]) for name in _ARRAY_NAMES}
    except (OSError, zipfile.BadZipFile, EOFError, ValueError) as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError("Proposal cache NPZ is corrupt or incomplete.") from exc
    _validate_arrays(arrays, core)
    return arrays


def _validate_arrays(
    arrays: dict[str, NDArray[np.generic]],
    core: dict[str, Any],
) -> None:
    for name in _ARRAY_NAMES:
        array = arrays[name]
        if array.dtype != _ARRAY_DTYPES[name] or array.dtype.hasobject:
            raise ValueError(f"Proposal array {name} has an invalid dtype.")
        if array.dtype.kind == "f" and not np.all(np.isfinite(array)):
            raise ValueError(f"Proposal array {name} contains nonfinite values.")
    cells = cast(NDArray[np.int64], arrays["candidate_cells"])
    coordinates = cast(NDArray[np.float64], arrays["candidate_coordinates"])
    counts = cast(NDArray[np.int64], arrays["candidate_counts"])
    quality = cast(NDArray[np.float64], arrays["candidate_quality"])
    witness_offsets = cast(NDArray[np.int64], arrays["witness_offsets"])
    report_positions = cast(NDArray[np.int64], arrays["witness_report_positions"])
    catalog_offsets = cast(NDArray[np.int64], arrays["witness_catalog_offsets"])
    catalog_indices = cast(NDArray[np.int64], arrays["witness_catalog_indices"])
    if np.any(witness_offsets[1:] < witness_offsets[:-1]) or witness_offsets[0] != 0:
        raise ValueError("Invalid witness ragged offsets.")
    if int(witness_offsets[-1]) != len(report_positions):
        raise ValueError("Witness ragged offsets do not cover report references.")
    if np.any(catalog_offsets[1:] < catalog_offsets[:-1]) or catalog_offsets[0] != 0:
        raise ValueError("Invalid witness catalog offsets.")
    if int(catalog_offsets[-1]) != len(catalog_indices):
        raise ValueError("Witness catalog offsets do not cover catalog references.")
    if np.any(report_positions < 0) or np.any(report_positions >= len(core["observations"])):
        raise ValueError("Witness report position is out of bounds.")
    if np.any(catalog_indices < 0) or np.any(catalog_indices >= len(core["catalog_keys"])):
        raise ValueError("Witness catalog index is out of bounds.")
    if np.any(counts < 0) or np.any(counts[:, 1] > counts[:, 0]):
        raise ValueError("Proposal counters are invalid.")
    if np.any(counts[:, 0] != witness_offsets[1:] - witness_offsets[:-1]):
        raise ValueError("Proposal vote counts disagree with witness offsets.")
    if np.any(quality[:, 0] < 0.0) or np.any(quality[:, 0] > 1.0) or np.any(quality[:, 1] < 0.0):
        raise ValueError("Proposal residual or evidence values are out of bounds.")
    if len(cells) and len({(int(row[0]), int(row[1])) for row in cells}) != len(cells):
        raise ValueError("Proposal cell indices must be unique.")
    for candidate_id, cell in zip(core["proposal_ids"], cells, strict=True):
        if candidate_id != f"cell:{int(cell[0])}:{int(cell[1])}":
            raise ValueError("Proposal ID does not match candidate cell.")
    for start, stop in zip(witness_offsets[:-1], witness_offsets[1:], strict=True):
        selected = report_positions[int(start) : int(stop)]
        if len(selected) != len(set(int(value) for value in selected)):
            raise ValueError("A proposal repeats a witness report.")
    for start, stop in zip(catalog_offsets[:-1], catalog_offsets[1:], strict=True):
        selected = catalog_indices[int(start) : int(stop)]
        indices = [int(value) for value in selected]
        if not indices or len(indices) != len(set(indices)):
            raise ValueError("Witness catalog rows must be nonempty and unique.")
        if indices != sorted(indices):
            raise ValueError("Witness catalog keys are not in canonical order.")
    cell_size_m = core["result"]["cell_size_m"]
    expected_coordinates = np.column_stack(
        ((cells[:, 0] + 0.5) * cell_size_m, (cells[:, 1] + 0.5) * cell_size_m)
    )
    if not np.allclose(coordinates, expected_coordinates, rtol=0.0, atol=1e-8):
        raise ValueError("Proposal coordinates do not match their candidate cells.")
    ranked_keys: list[tuple[int, int, float, float, int, int]] = []
    for proposal_index, (start, stop) in enumerate(
        zip(witness_offsets[:-1], witness_offsets[1:], strict=True)
    ):
        graph: dict[int, list[int]] = {}
        previous_report = -1
        for witness_index in range(int(start), int(stop)):
            report_position = int(report_positions[witness_index])
            if report_position <= previous_report:
                raise ValueError("Witness report rows are not in canonical report order.")
            previous_report = report_position
            key_start = int(catalog_offsets[witness_index])
            key_stop = int(catalog_offsets[witness_index + 1])
            graph[report_position] = [int(value) for value in catalog_indices[key_start:key_stop]]
        matching = _maximum_catalog_matching(graph)
        if int(counts[proposal_index, 1]) != matching:
            raise ValueError("Proposal matching_count disagrees with witness graph.")
        ranked_keys.append(
            (
                -matching,
                -int(counts[proposal_index, 0]),
                float(quality[proposal_index, 0]),
                -float(quality[proposal_index, 1]),
                int(cells[proposal_index, 0]),
                int(cells[proposal_index, 1]),
            )
        )
    if ranked_keys != sorted(ranked_keys):
        raise ValueError("Proposal array order does not match deterministic ranking order.")


def _catalog_positions(catalog_keys: tuple[str, ...]) -> dict[str, int]:
    """Build the stable key-to-position index once per result validation."""
    return {key: position for position, key in enumerate(catalog_keys)}


def _maximum_catalog_matching(report_to_keys: dict[int, list[int]]) -> int:
    source_to_report: dict[int, int] = {}

    def augment(report_position: int, seen: set[int]) -> bool:
        for key_index in report_to_keys[report_position]:
            if key_index in seen:
                continue
            seen.add(key_index)
            previous = source_to_report.get(key_index)
            if previous is None or augment(previous, seen):
                source_to_report[key_index] = report_position
                return True
        return False

    return sum(1 for report_position in sorted(report_to_keys) if augment(report_position, set()))


def _estimate_write_bytes(result: GeodesicWedgeProposalResult, json_size: int) -> int:
    proposal_count = len(result.proposals)
    witness_reports = sum(len(item.witness_report_keys) for item in result.proposals)
    witness_catalog = sum(
        len(keys) for proposal in result.proposals for _, keys in proposal.witness_report_keys
    )
    array_bytes = (
        proposal_count * (2 * 8 + 2 * 8 + 2 * 8 + 2 * 8)
        + (proposal_count + 1) * 8
        + witness_reports * 8
        + (witness_reports + 1) * 8
        + witness_catalog * 8
    )
    return 2 * array_bytes + 2 * json_size + 1024 * 1024


def _check_disk_reserve(parent: Path, estimated_bytes: int) -> None:
    free_bytes = shutil.disk_usage(parent).free
    if free_bytes - estimated_bytes < _MIN_FREE_BYTES:
        raise OSError(
            f"Proposal cache write would violate the 40 GiB free-space reserve "
            f"(free={free_bytes}, estimate={estimated_bytes})."
        )


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field in proposal cache: {key}.")
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "ProposalCacheContext",
    "load_proposal_cache",
    "save_proposal_cache",
]
