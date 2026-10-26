"""Tests for deterministic local scene construction."""

from __future__ import annotations

from typing import Any

import pytest

from maritime.simulator.scene_builder import (
    SceneBuilderConfig,
    build_scene,
)


def test_build_scene_is_deterministic_for_a_fixed_seed() -> None:
    config = SceneBuilderConfig(ais_count=8, non_ais_count=5)

    first = build_scene(config, seed=23)
    second = build_scene(config, seed=23)

    assert first == second


def test_scene_keeps_ais_and_non_ais_separate_and_truth_ids_distinct() -> None:
    scene = build_scene(SceneBuilderConfig(ais_count=4, non_ais_count=3), seed=4)
    ais_ids = {record.record_id for record in scene.ais_snapshot.records}
    truth_ids = {obj.object_id for obj in scene.truth_objects}

    assert len(scene.ais_snapshot.records) == 4
    assert len(scene.non_ais_objects) == 3
    assert len(scene.truth_objects) == 7
    assert ais_ids.isdisjoint(truth_ids)
    assert all(obj.ais_record_id is None for obj in scene.non_ais_objects)
    assert {obj.ais_record_id for obj in scene.truth_objects if obj.ais_record_id} == ais_ids


def test_objects_stay_within_local_east_north_bounds() -> None:
    config = SceneBuilderConfig(
        east_bounds_m=(-150.0, 250.0),
        north_bounds_m=(1_000.0, 2_000.0),
        ais_count=20,
        non_ais_count=20,
    )
    scene = build_scene(config, seed=91)

    for obj in scene.truth_objects:
        assert -150.0 <= obj.east_m <= 250.0
        assert 1_000.0 <= obj.north_m <= 2_000.0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"east_bounds_m": (1.0, 1.0)},
        {"north_bounds_m": (2.0, -2.0)},
        {"ais_count": -1},
        {"non_ais_count": -1},
        {"object_types": ()},
        {"object_types": ("ship", "ship")},
    ],
)
def test_invalid_scene_config_is_rejected(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        SceneBuilderConfig(**kwargs)


def test_empty_scene_is_supported() -> None:
    scene = build_scene(SceneBuilderConfig(ais_count=0, non_ais_count=0), seed=1)

    assert scene.ais_snapshot.records == ()
    assert scene.non_ais_objects == ()
    assert scene.truth_objects == ()
