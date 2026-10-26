"""Streaming pairwise geometric proposal generation for bearing localization."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from hashlib import sha256
from itertools import combinations
from math import atan2, cos, degrees, hypot, isfinite, radians, sin
from typing import Literal

import numpy as np
from pyproj import Geod

from maritime.geometry.coordinates import (
    LocalFrame,
    geodesic_bearing_distance,
)
from maritime.geometry.spatial_index import (
    GeographicSpatialIndex,
    SpatialIndex2D,
)
from maritime.schemas.inputs_v2 import CatalogV2, MeasurementSetV2


@dataclass(frozen=True, slots=True)
class PairwiseObservation:
    """One true-north bearing with a stable caller-assigned report index."""

    report_index: int
    bearing_deg: float

    def __post_init__(self) -> None:
        if type(self.report_index) is not int or self.report_index < 0:
            raise ValueError("report_index must be a nonnegative integer.")
        if not isfinite(self.bearing_deg) or not 0.0 <= self.bearing_deg < 360.0:
            raise ValueError("bearing_deg must be finite and in [0, 360).")


@dataclass(frozen=True, slots=True)
class PairwiseCatalogObject:
    """One catalog feature in a shared planar meter frame."""

    key: str
    east_m: float
    north_m: float

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key.strip():
            raise ValueError("key must be a nonempty stable string.")
        if not isfinite(self.east_m) or not isfinite(self.north_m):
            raise ValueError("Catalog coordinates must be finite.")


@dataclass(frozen=True, slots=True)
class PairwiseProposal:
    """An intersection from two reports and two distinct catalog features."""

    proposal_id: str
    observation_indices: tuple[int, int]
    catalog_keys: tuple[str, str]
    east_m: float
    north_m: float
    ranges_m: tuple[float, float]
    coordinate_model: Literal["planar", "local_planar_approximation"] = "planar"


@dataclass(frozen=True, slots=True)
class PairwiseProposalBatch:
    """A finite proposal chunk and an explicit generation status."""

    proposals: tuple[PairwiseProposal, ...]
    generated_count: int
    generation_status: Literal["generation_incomplete", "completed", "budget_exhausted"]

    @property
    def generation_complete(self) -> bool:
        return self.generation_status == "completed"


def _proposal_id(obs_pair: tuple[int, int], key_pair: tuple[str, str]) -> str:
    payload = json.dumps([obs_pair, key_pair], ensure_ascii=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _geodesic_polar_local_xy(
    origin_lonlat: tuple[float, float],
    point_lonlat: tuple[float, float],
    geod: Geod,
) -> tuple[float, float]:
    """Project a point into an ellipsoidal AEQD frame using geodesic polar coordinates."""
    azimuth_deg, _, distance_m = geod.inv(
        origin_lonlat[0], origin_lonlat[1], point_lonlat[0], point_lonlat[1]
    )
    azimuth_rad = radians(azimuth_deg)
    return distance_m * sin(azimuth_rad), distance_m * cos(azimuth_rad)


def _geodesic_polar_to_geographic(
    origin_lonlat: tuple[float, float],
    east_m: float,
    north_m: float,
    geod: Geod,
) -> tuple[float, float]:
    """Invert ellipsoidal AEQD coordinates without constructing a CRS transformer."""
    distance_m = hypot(east_m, north_m)
    if distance_m == 0.0:
        return origin_lonlat
    azimuth_deg = degrees(atan2(east_m, north_m)) % 360.0
    longitude_deg, latitude_deg, _ = geod.fwd(
        origin_lonlat[0], origin_lonlat[1], azimuth_deg, distance_m
    )
    return float(longitude_deg), float(latitude_deg)


def generate_pairwise_proposals(
    observations: Sequence[PairwiseObservation],
    catalog: Sequence[PairwiseCatalogObject],
    *,
    max_range_m: float,
    batch_size: int = 1_024,
    max_proposals: int | None = None,
    min_crossing_angle_deg: float = 0.1,
) -> Iterator[PairwiseProposalBatch]:
    """Stream all positive-range pair intersections within the inclusive range.

    Catalog pairs are conservatively limited to separation ``2 * max_range_m``
    through an uncapped spatial radius query. Reports and catalog objects are
    ordered by their stable indices/keys, so shuffled inputs yield the same
    proposal IDs and coordinates. A finite proposal budget returns an explicit
    ``budget_exhausted`` final batch if another valid proposal exists.
    """
    if not isfinite(max_range_m) or max_range_m <= 0.0:
        raise ValueError("max_range_m must be finite and positive.")
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("batch_size must be a positive integer.")
    if max_proposals is not None and (type(max_proposals) is not int or max_proposals < 1):
        raise ValueError("max_proposals must be a positive integer or None.")
    if not isfinite(min_crossing_angle_deg) or not 0.0 <= min_crossing_angle_deg < 90.0:
        raise ValueError("min_crossing_angle_deg must be finite and in [0, 90).")
    obs = tuple(sorted(observations, key=lambda item: item.report_index))
    objects = tuple(sorted(catalog, key=lambda item: item.key))
    if any(not isinstance(item, PairwiseObservation) for item in obs):
        raise TypeError("observations must contain PairwiseObservation records.")
    if any(not isinstance(item, PairwiseCatalogObject) for item in objects):
        raise TypeError("catalog must contain PairwiseCatalogObject records.")
    if len({item.report_index for item in obs}) != len(obs):
        raise ValueError("report_index values must be unique.")
    if len({item.key for item in objects}) != len(objects):
        raise ValueError("catalog keys must be unique.")
    if len(obs) < 2:
        raise ValueError("At least two distinct observations are required.")
    if len(objects) < 2:
        yield PairwiseProposalBatch((), 0, "completed")
        return

    points = [(item.east_m, item.north_m) for item in objects]
    index = SpatialIndex2D(points, cell_size_m=max_range_m, keys=[item.key for item in objects])
    directions = {
        item.report_index: np.asarray(
            [np.sin(radians(item.bearing_deg)), np.cos(radians(item.bearing_deg))], dtype=float
        )
        for item in obs
    }
    minimum_sine = np.sin(np.deg2rad(min_crossing_angle_deg))
    pending: list[PairwiseProposal] = []
    generated = 0
    stop = False

    for observation_i, observation_j in combinations(obs, 2):
        oi, oj = observation_i.report_index, observation_j.report_index
        ui, uj = directions[oi], directions[oj]
        matrix = np.column_stack((ui, -uj))
        determinant = float(np.linalg.det(matrix))
        if abs(determinant) < minimum_sine:
            continue
        for i, source in enumerate(objects):
            for neighbor_key in index.query_radius_keys(
                source.east_m, source.north_m, 2.0 * max_range_m
            ):
                if neighbor_key == source.key:
                    continue
                j = index.index_for_key(neighbor_key)
                target = objects[j]
                # Stable sorted key order means every unordered catalog pair is visited once.
                if target.key <= source.key:
                    continue
                for assigned_i, assigned_j in ((source, target), (target, source)):
                    delta = np.asarray(
                        [
                            assigned_i.east_m - assigned_j.east_m,
                            assigned_i.north_m - assigned_j.north_m,
                        ],
                        dtype=float,
                    )
                    try:
                        range_i, range_j = np.linalg.solve(matrix, delta)
                    except np.linalg.LinAlgError:
                        continue
                    if not (0.0 < range_i <= max_range_m and 0.0 < range_j <= max_range_m):
                        continue
                    position = np.asarray([assigned_i.east_m, assigned_i.north_m]) - range_i * ui
                    if max_proposals is not None and generated >= max_proposals:
                        stop = True
                        break
                    key_pair = (assigned_i.key, assigned_j.key)
                    pending.append(
                        PairwiseProposal(
                            proposal_id=_proposal_id((oi, oj), key_pair),
                            observation_indices=(oi, oj),
                            catalog_keys=key_pair,
                            east_m=float(position[0]),
                            north_m=float(position[1]),
                            ranges_m=(float(range_i), float(range_j)),
                        )
                    )
                    generated += 1
                    if len(pending) >= batch_size:
                        yield PairwiseProposalBatch(
                            tuple(pending), generated, "generation_incomplete"
                        )
                        pending.clear()
                if stop:
                    break
            if stop:
                break
        if stop:
            break

    status: Literal["completed", "budget_exhausted"] = "budget_exhausted" if stop else "completed"
    yield PairwiseProposalBatch(tuple(pending), generated, status)


def generate_geographic_pairwise_proposals(
    measurements: MeasurementSetV2,
    catalog: CatalogV2,
    *,
    output_origin_lonlat: tuple[float, float],
    max_range_m: float,
    batch_size: int = 1_024,
    max_proposals: int | None = None,
    min_crossing_angle_deg: float = 0.1,
) -> Iterator[PairwiseProposalBatch]:
    """Create geodesically pruned, local planar proposals for v2 inputs.

    Each distinct catalog pair gets its own AEQD frame centered at its
    geodesic midpoint. True-north bearings are interpreted as local planar
    directions in that midpoint frame, so outputs remain proposals rather
    than exact geographic intersections. Candidates are transformed to the
    caller's output frame and have WGS84 ranges checked before emission. The
    final geographic objective must still score/refine them exactly.
    """
    if not isinstance(measurements, MeasurementSetV2):
        raise TypeError("measurements must be a MeasurementSetV2.")
    if not isinstance(catalog, CatalogV2):
        raise TypeError("catalog must be a CatalogV2.")
    if not isfinite(max_range_m) or max_range_m <= 0.0:
        raise ValueError("max_range_m must be finite and positive.")
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("batch_size must be a positive integer.")
    if max_proposals is not None and (type(max_proposals) is not int or max_proposals < 1):
        raise ValueError("max_proposals must be a positive integer or None.")
    if not isfinite(min_crossing_angle_deg) or not 0.0 <= min_crossing_angle_deg < 90.0:
        raise ValueError("min_crossing_angle_deg must be finite and in [0, 90).")
    output_frame = LocalFrame(output_origin_lonlat)
    ordered_measurements = sorted(
        measurements.measurements,
        key=lambda item: (
            item.bearing_deg,
            item.timestamp_utc,
            item.object_type or "",
            item.sigma_deg if item.sigma_deg is not None else -1.0,
            item.angular_half_width_deg if item.angular_half_width_deg is not None else -1.0,
        ),
    )
    observations = tuple(
        PairwiseObservation(index, item.bearing_deg)
        for index, item in enumerate(ordered_measurements)
    )
    records = tuple(sorted(catalog.records, key=lambda item: item.key))
    geo_index = GeographicSpatialIndex(
        [(record.longitude_deg, record.latitude_deg) for record in records],
        keys=[record.key for record in records],
    )
    geod = Geod(ellps="WGS84")
    minimum_sine = np.sin(np.deg2rad(min_crossing_angle_deg))
    pending: list[PairwiseProposal] = []
    generated = 0
    stopped = False

    for observation_i, observation_j in combinations(observations, 2):
        oi, oj = observation_i.report_index, observation_j.report_index
        ui = np.asarray(
            [np.sin(radians(observation_i.bearing_deg)), np.cos(radians(observation_i.bearing_deg))]
        )
        uj = np.asarray(
            [np.sin(radians(observation_j.bearing_deg)), np.cos(radians(observation_j.bearing_deg))]
        )
        matrix = np.column_stack((ui, -uj))
        determinant = abs(float(np.linalg.det(matrix)))
        if determinant < minimum_sine:
            continue
        # Transform rounding can change strict ray-sign or inclusive range decisions
        # near their boundaries, so reevaluate that narrow band with the old frame.
        decision_tolerance_m = 1e-6 / max(determinant, 1e-12)
        for source in records:
            nearby_keys = geo_index.query_radius_keys(
                source.longitude_deg, source.latitude_deg, 2.0 * max_range_m
            )
            for target_key in nearby_keys:
                if str(target_key) <= source.key:
                    continue
                target = records[geo_index.index_for_key(target_key)]
                midpoint_bearing, _, separation = geod.inv(
                    source.longitude_deg,
                    source.latitude_deg,
                    target.longitude_deg,
                    target.latitude_deg,
                )
                if separation > 2.0 * max_range_m:
                    continue
                midpoint_lon, midpoint_lat, _ = geod.fwd(
                    source.longitude_deg,
                    source.latitude_deg,
                    midpoint_bearing,
                    separation / 2.0,
                )
                pair_midpoint = (float(midpoint_lon), float(midpoint_lat))
                source_xy = _geodesic_polar_local_xy(
                    pair_midpoint, (source.longitude_deg, source.latitude_deg), geod
                )
                target_xy = _geodesic_polar_local_xy(
                    pair_midpoint, (target.longitude_deg, target.latitude_deg), geod
                )
                pair_frame: LocalFrame | None = None
                for assigned_i, assigned_j in ((source, target), (target, source)):
                    if assigned_i.key == source.key:
                        assigned_i_xy, assigned_j_xy = source_xy, target_xy
                    else:
                        assigned_i_xy, assigned_j_xy = target_xy, source_xy
                    if pair_frame is not None:
                        assigned_i_xy = pair_frame.to_local(
                            (assigned_i.longitude_deg, assigned_i.latitude_deg)
                        )
                        assigned_j_xy = pair_frame.to_local(
                            (assigned_j.longitude_deg, assigned_j.latitude_deg)
                        )
                    delta = np.asarray(assigned_i_xy) - np.asarray(assigned_j_xy)
                    try:
                        range_i, range_j = np.linalg.solve(matrix, delta)
                    except np.linalg.LinAlgError:
                        continue
                    if min(abs(float(range_i)), abs(float(range_j))) <= decision_tolerance_m:
                        pair_frame = LocalFrame(pair_midpoint)
                        assigned_i_xy = pair_frame.to_local(
                            (assigned_i.longitude_deg, assigned_i.latitude_deg)
                        )
                        assigned_j_xy = pair_frame.to_local(
                            (assigned_j.longitude_deg, assigned_j.latitude_deg)
                        )
                        delta = np.asarray(assigned_i_xy) - np.asarray(assigned_j_xy)
                        try:
                            range_i, range_j = np.linalg.solve(matrix, delta)
                        except np.linalg.LinAlgError:
                            continue
                    if range_i <= 0.0 or range_j <= 0.0:
                        continue
                    local_candidate = np.asarray(assigned_i_xy) - range_i * ui
                    if pair_frame is None:
                        candidate_lonlat = _geodesic_polar_to_geographic(
                            pair_midpoint,
                            float(local_candidate[0]),
                            float(local_candidate[1]),
                            geod,
                        )
                    else:
                        candidate_lonlat = pair_frame.to_geographic(
                            float(local_candidate[0]), float(local_candidate[1])
                        )
                    candidate_to_i = geodesic_bearing_distance(
                        candidate_lonlat, (assigned_i.longitude_deg, assigned_i.latitude_deg)
                    ).distance_m
                    candidate_to_j = geodesic_bearing_distance(
                        candidate_lonlat, (assigned_j.longitude_deg, assigned_j.latitude_deg)
                    ).distance_m
                    if (
                        pair_frame is None
                        and min(
                            abs(max_range_m - candidate_to_i),
                            abs(max_range_m - candidate_to_j),
                        )
                        <= decision_tolerance_m
                    ):
                        pair_frame = LocalFrame(pair_midpoint)
                        assigned_i_xy = pair_frame.to_local(
                            (assigned_i.longitude_deg, assigned_i.latitude_deg)
                        )
                        assigned_j_xy = pair_frame.to_local(
                            (assigned_j.longitude_deg, assigned_j.latitude_deg)
                        )
                        delta = np.asarray(assigned_i_xy) - np.asarray(assigned_j_xy)
                        try:
                            range_i, range_j = np.linalg.solve(matrix, delta)
                        except np.linalg.LinAlgError:
                            continue
                        if range_i <= 0.0 or range_j <= 0.0:
                            continue
                        local_candidate = np.asarray(assigned_i_xy) - range_i * ui
                        candidate_lonlat = pair_frame.to_geographic(
                            float(local_candidate[0]), float(local_candidate[1])
                        )
                        candidate_to_i = geodesic_bearing_distance(
                            candidate_lonlat,
                            (assigned_i.longitude_deg, assigned_i.latitude_deg),
                        ).distance_m
                        candidate_to_j = geodesic_bearing_distance(
                            candidate_lonlat,
                            (assigned_j.longitude_deg, assigned_j.latitude_deg),
                        ).distance_m
                    if candidate_to_i > max_range_m or candidate_to_j > max_range_m:
                        continue
                    if max_proposals is not None and generated >= max_proposals:
                        stopped = True
                        break
                    east_m, north_m = output_frame.to_local(candidate_lonlat)
                    key_pair = (assigned_i.key, assigned_j.key)
                    pending.append(
                        PairwiseProposal(
                            proposal_id=_proposal_id((oi, oj), key_pair),
                            observation_indices=(oi, oj),
                            catalog_keys=key_pair,
                            east_m=east_m,
                            north_m=north_m,
                            ranges_m=(candidate_to_i, candidate_to_j),
                            coordinate_model="local_planar_approximation",
                        )
                    )
                    generated += 1
                    if len(pending) >= batch_size:
                        yield PairwiseProposalBatch(
                            tuple(pending), generated, "generation_incomplete"
                        )
                        pending.clear()
                if stopped:
                    break
            if stopped:
                break
        if stopped:
            break

    status: Literal["completed", "budget_exhausted"] = (
        "budget_exhausted" if stopped else "completed"
    )
    yield PairwiseProposalBatch(tuple(pending), generated, status)


__all__ = [
    "PairwiseCatalogObject",
    "PairwiseObservation",
    "PairwiseProposal",
    "PairwiseProposalBatch",
    "generate_geographic_pairwise_proposals",
    "generate_pairwise_proposals",
]
