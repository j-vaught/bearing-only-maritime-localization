"""Type-aware bearing association between sensor reports and AIS records.

Sensor object labels and AIS catalogue classes describe different things. This
module keeps their compatibility explicit, while allowing a detection to remain
unmatched when its object does not transmit AIS or has no catalogue entry.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import atan2, degrees, hypot

import numpy as np
from scipy.optimize import linear_sum_assignment

from maritime_localization_shared.contracts import AISRecord, Observation
from maritime_localization_shared.contracts.object_types import normalize_object_type

# These are deliberately coarse object-kind aliases, not AIS transponder
# equipment classes. Unknown labels are compared after case/spacing cleanup.
_SENSOR_KIND_ALIASES: dict[str, frozenset[str]] = {
    "ship": frozenset({"ship", "vessel", "boat", "large_vessel"}),
    "vessel": frozenset({"ship", "vessel", "boat", "large_vessel"}),
    "boat": frozenset({"ship", "vessel", "boat", "large_vessel"}),
    "buoy": frozenset({"buoy", "aid_to_navigation", "aids_to_navigation"}),
    "channel_marker": frozenset({"channel_marker", "aid_to_navigation", "aids_to_navigation"}),
    "shore_structure": frozenset({"shore_structure"}),
}


@dataclass(frozen=True, slots=True)
class AssociationResult:
    """One-to-one matches plus detections left unmatched by the AIS catalogue."""

    cost: float
    matches: tuple[tuple[int, int], ...]
    unmatched_observation_indices: tuple[int, ...]
    unused_ais_indices: tuple[int, ...]


def compatible_types(
    sensor_type: str | None,
    ais_class: str,
    *,
    use_types: bool = True,
    sensor_to_ais: Mapping[str, frozenset[str] | set[str] | tuple[str, ...]] | None = None,
) -> bool:
    """Return whether a sensor object label may match an AIS object class.

    Missing sensor labels are wildcards. Disabling type use makes every pair
    compatible. A supplied ``sensor_to_ais`` mapping replaces the built-in
    coarse aliases and is keyed by normalized sensor label.
    """
    if not use_types or sensor_type is None:
        return True
    sensor = _normalize(sensor_type)
    ais = _normalize(ais_class)
    explicit_wildcards = {"unknown", "unk", "unclassified", "unspecified"}
    if sensor in explicit_wildcards or ais in explicit_wildcards:
        return True
    mapping = sensor_to_ais
    if mapping is not None:
        normalized_mapping = {
            _normalize(key): frozenset(_normalize(value) for value in values)
            for key, values in mapping.items()
        }
        allowed = normalized_mapping.get(sensor)
        if allowed is not None:
            # A custom mapping deliberately assigns meaning to otherwise
            # unknown source labels such as "class_a".
            return ais in allowed
        return ais == sensor
    sensor_kind = normalize_object_type(sensor_type)
    ais_kind = normalize_object_type(ais_class)
    if sensor_kind != "unknown" and ais_kind != "unknown" and sensor_kind == ais_kind:
        return True
    # Preserve historical crosswalks for legacy AIS strings not in the
    # neutral taxonomy while treating other unrecognized labels literally.
    aliases = _SENSOR_KIND_ALIASES.get(sensor)
    if aliases is not None and ais in aliases:
        return True
    if sensor_kind == "unknown" or ais_kind == "unknown":
        return sensor == ais
    return False


def associate_observations_to_ais(
    observer_xy_m: tuple[float, float] | np.ndarray,
    observations: tuple[Observation, ...] | list[Observation],
    ais_records: tuple[AISRecord, ...] | list[AISRecord],
    *,
    use_types: bool = True,
    sensor_to_ais: Mapping[str, frozenset[str] | set[str] | tuple[str, ...]] | None = None,
    unmatched_penalty_deg: float = 12.0,
    sigma_floor_deg: float = 0.1,
    max_range_m: float = float("inf"),
) -> AssociationResult:
    """Find the minimum-cost one-to-one assignment at a candidate position.

    Bearings are global degrees clockwise from north. Each observation has a
    private unmatched option, so non-AIS objects and missing AIS reports do not
    force false matches. AIS entries outside ``max_range_m`` are excluded.
    The assignment cost is bearing error normalized by each observation's
    uncertainty, capped by the unmatched penalty.
    """
    if unmatched_penalty_deg <= 0 or sigma_floor_deg <= 0 or max_range_m <= 0:
        raise ValueError("Penalties, uncertainty floor, and range must be positive.")
    observer = np.asarray(observer_xy_m, dtype=float)
    if observer.shape != (2,) or not np.isfinite(observer).all():
        raise ValueError("observer_xy_m must contain two finite coordinates.")
    if not all(isinstance(item, Observation) for item in observations):
        raise TypeError("observations must contain Observation records.")
    if not all(isinstance(item, AISRecord) for item in ais_records):
        raise TypeError("ais_records must contain AISRecord records.")

    obs_count = len(observations)
    ais_indices = tuple(
        index
        for index, record in enumerate(ais_records)
        if hypot(record.east_m - observer[0], record.north_m - observer[1]) <= max_range_m
    )
    if obs_count == 0:
        return AssociationResult(0.0, (), (), ais_indices)
    row_penalties = np.asarray(
        [
            unmatched_penalty_deg / max(observation.bearing_sigma_deg, sigma_floor_deg)
            for observation in observations
        ],
        dtype=float,
    )
    if not ais_indices:
        return AssociationResult(float(row_penalties.sum()), (), tuple(range(obs_count)), ())

    costs = np.full((obs_count, len(ais_indices)), np.inf, dtype=float)
    for obs_index, observation in enumerate(observations):
        sigma = max(observation.bearing_sigma_deg, sigma_floor_deg)
        for column, ais_index in enumerate(ais_indices):
            record = ais_records[ais_index]
            if not compatible_types(
                observation.object_type,
                record.ais_class,
                use_types=use_types,
                sensor_to_ais=sensor_to_ais,
            ):
                continue
            predicted = degrees(atan2(record.east_m - observer[0], record.north_m - observer[1]))
            error = abs((predicted - observation.bearing_deg + 180.0) % 360.0 - 180.0)
            costs[obs_index, column] = error / sigma

    # One private dummy column per observation guarantees an unmatched option.
    # A real AIS match must beat its dummy, which prevents poor forced matches.
    matrix = np.broadcast_to(
        row_penalties[:, None], (obs_count, len(ais_indices) + obs_count)
    ).copy()
    matrix[:, : len(ais_indices)] = np.minimum(costs, row_penalties[:, None])
    incompatible = ~np.isfinite(costs)
    forbidden_costs = np.broadcast_to(2.0 * row_penalties[:, None], costs.shape)
    matrix[:, : len(ais_indices)][incompatible] = forbidden_costs[incompatible]
    rows, columns = linear_sum_assignment(matrix)
    matches: list[tuple[int, int]] = []
    unmatched: list[int] = []
    total_cost = 0.0
    used_ais: set[int] = set()
    for row, column in zip(rows, columns):
        total_cost += float(matrix[row, column])
        if column < len(ais_indices) and costs[row, column] < row_penalties[row]:
            ais_index = ais_indices[column]
            matches.append((int(row), ais_index))
            used_ais.add(ais_index)
        else:
            unmatched.append(int(row))
    unused = tuple(index for index in ais_indices if index not in used_ais)
    return AssociationResult(
        cost=total_cost,
        matches=tuple(sorted(matches)),
        unmatched_observation_indices=tuple(sorted(unmatched)),
        unused_ais_indices=unused,
    )


def _normalize(label: str) -> str:
    return "_".join(label.strip().lower().replace("-", " ").split())


__all__ = ["AssociationResult", "associate_observations_to_ais", "compatible_types"]
