from __future__ import annotations

from typing import Any, cast

import pytest

from maritime.schemas.ais import AISRecord
from maritime.schemas.object_types import (
    is_visual_entity,
    normalize_object_type,
)


@pytest.mark.parametrize(
    "label", ["ship", "SHIP", "vessel", "boat", "large_vessel", "small-vessel"]
)
def test_ship_and_vessel_aliases_share_neutral_class(label: str) -> None:
    assert normalize_object_type(label) == "vessel"


def test_unknown_and_unrecognized_labels_are_not_false_vessel_matches() -> None:
    assert normalize_object_type(None) is None
    assert normalize_object_type("unknown") == "unknown"
    assert normalize_object_type("mystery craft") == "unknown"


def test_vessel_size_is_not_inferred_from_coarse_alias() -> None:
    assert normalize_object_type("small vessel") == "vessel"
    assert normalize_object_type("large vessel") == "vessel"


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("buoy", "physical_aid"),
        ("channel-marker", "physical_aid"),
        ("aid_to_navigation", "physical_aid"),
        ("shore_structure", "shoreline"),
        ("shoreline aid", "shoreline"),
        ("virtual-aid", "virtual_aid"),
        ("virtual_aton", "virtual_aid"),
    ],
)
def test_physical_shoreline_and_virtual_aids_remain_distinct(label: str, expected: str) -> None:
    assert normalize_object_type(label) == expected


def test_visual_entities_exclude_virtual_aids_only() -> None:
    assert is_visual_entity("buoy")
    assert is_visual_entity("shore_structure")
    assert is_visual_entity("unknown")
    assert is_visual_entity("future label")
    assert not is_visual_entity("virtual aid")


def test_source_codes_are_preserved_verbatim() -> None:
    record = AISRecord("ais-1", 100.0, 200.0, "vessel", source_type_code="036")
    assert record.source_type_code == "036"
    assert normalize_object_type("036") == "036"
    assert normalize_object_type("17") == "17"


def test_type_label_policy_can_disable_labels_without_dropping_entity() -> None:
    assert normalize_object_type("ship", type_labels_enabled=False) is None
    assert normalize_object_type(None, type_labels_enabled=False) is None
    assert not is_visual_entity("virtual aid", type_labels_enabled=False)
    assert is_visual_entity("ship", type_labels_enabled=False)


def test_invalid_label_types_are_rejected() -> None:
    with pytest.raises(TypeError):
        normalize_object_type(cast(Any, 1.5))
    with pytest.raises(TypeError):
        normalize_object_type(True)
