from __future__ import annotations

import numpy as np
import pytest

from bearing_localization.search.association import (
    associate_observations_to_ais,
    compatible_types,
)
from maritime_localization_shared.contracts import AISRecord, Observation


def _ais(record_id: str, east: float, north: float, ais_class: str) -> AISRecord:
    return AISRecord(record_id, east, north, ais_class)


def test_type_aliases_bridge_sensor_label_and_ais_class() -> None:
    assert compatible_types("ship", "vessel")
    assert compatible_types("channel marker", "aid-to-navigation")
    assert not compatible_types("buoy", "vessel")


def test_type_blind_mode_allows_different_classes() -> None:
    assert compatible_types("buoy", "vessel", use_types=False)


def test_custom_mapping_overrides_default_type_compatibility() -> None:
    mapping = {"ship": {"class_a"}, "buoy": {"aton"}}
    assert compatible_types("ship", "Class A", sensor_to_ais=mapping)
    assert not compatible_types("ship", "vessel", sensor_to_ais=mapping)


def test_assignment_matches_by_bearing_and_type_and_leaves_non_ais_unmatched() -> None:
    observations = (
        Observation(0.0, 1.0, "ship"),
        Observation(90.0, 1.0, "buoy"),
        Observation(-90.0, 1.0, "shore_structure"),
    )
    ais = (
        _ais("v1", 0.0, 1000.0, "vessel"),
        _ais("b1", 1000.0, 0.0, "aid_to_navigation"),
        _ais("wrong", -1000.0, 0.0, "vessel"),
    )
    result = associate_observations_to_ais((0.0, 0.0), observations, ais)
    assert result.matches == ((0, 0), (1, 1))
    assert result.unmatched_observation_indices == (2,)
    assert result.unused_ais_indices == (2,)


def test_unlabeled_observation_is_wildcard_when_type_matching_enabled() -> None:
    result = associate_observations_to_ais(
        (0.0, 0.0),
        (Observation(45.0, 1.0),),
        (_ais("v1", 100.0, 100.0, "vessel"),),
    )
    assert result.matches == ((0, 0),)
    assert result.unmatched_observation_indices == ()


def test_type_blind_assignment_can_use_otherwise_incompatible_ais() -> None:
    result = associate_observations_to_ais(
        (0.0, 0.0),
        (Observation(90.0, 1.0, "buoy"),),
        (_ais("v1", 1000.0, 0.0, "vessel"),),
        use_types=False,
    )
    assert result.matches == ((0, 0),)


def test_max_range_excludes_ais_candidates() -> None:
    result = associate_observations_to_ais(
        (0.0, 0.0),
        (Observation(0.0, 1.0, "ship"),),
        (_ais("near", 0.0, 500.0, "vessel"), _ais("far", 0.0, 2000.0, "vessel")),
        max_range_m=1000.0,
    )
    assert result.matches == ((0, 0),)
    assert result.unused_ais_indices == ()


def test_empty_inputs_and_invalid_inputs() -> None:
    assert associate_observations_to_ais((0.0, 0.0), (), ()).cost == 0.0
    with pytest.raises(ValueError):
        associate_observations_to_ais((np.nan, 0.0), (), ())
    with pytest.raises(ValueError):
        associate_observations_to_ais((0.0, 0.0), (), (), max_range_m=0.0)
