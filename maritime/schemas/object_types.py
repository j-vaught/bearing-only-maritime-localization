"""Neutral coarse object labels shared by sensor and catalogue contracts.

The taxonomy keeps visible physical objects distinct from electronic aids to
navigation. Source-specific numeric codes belong in provenance fields and are
never rewritten by this module.
"""

from __future__ import annotations

import re
from typing import TypeAlias

ObjectTypeInput: TypeAlias = str | int | None

# A numeric source code is not a neutral object label. Keep it opaque unless a
# source adapter has already supplied its meaning as a textual label.
_ALIASES: dict[str, str] = {
    "ship": "vessel",
    "ships": "vessel",
    "vessel": "vessel",
    "vessels": "vessel",
    "boat": "vessel",
    "boats": "vessel",
    "large vessel": "vessel",
    "large vessels": "vessel",
    "small vessel": "vessel",
    "small vessels": "vessel",
    "unknown": "unknown",
    "unk": "unknown",
    "unclassified": "unknown",
    "unspecified": "unknown",
    "physical aid": "physical_aid",
    "physical aids": "physical_aid",
    "physical aid to navigation": "physical_aid",
    "aid to navigation": "physical_aid",
    "aids to navigation": "physical_aid",
    "aton": "physical_aid",
    "buoy": "physical_aid",
    "buoys": "physical_aid",
    "channel marker": "physical_aid",
    "channel markers": "physical_aid",
    "beacon": "physical_aid",
    "light": "physical_aid",
    "shoreline": "shoreline",
    "shore structure": "shoreline",
    "shore structures": "shoreline",
    "shoreline structure": "shoreline",
    "shoreline aid": "shoreline",
    "shore based aid": "shoreline",
    "virtual aid": "virtual_aid",
    "virtual aids": "virtual_aid",
    "virtual aid to navigation": "virtual_aid",
    "virtual aton": "virtual_aid",
    "synthetic aid": "virtual_aid",
    "synthetic aton": "virtual_aid",
}


def normalize_object_type(
    label: ObjectTypeInput, *, type_labels_enabled: bool = True
) -> str | None:
    """Return a neutral coarse label; unrecognized labels become unknown.

    None represents a missing label or a disabled type-label policy, so it
    can be treated as a wildcard by association code. AIS and chart source codes
    are not interpreted here; their exact raw spelling belongs in provenance.
    Vessel size is intentionally not inferred from a coarse vessel label.
    """
    if not type_labels_enabled or label is None:
        return None
    if isinstance(label, bool) or not isinstance(label, (str, int)):
        raise TypeError("object type labels must be strings, integers, or None.")

    source_label = str(label).strip()
    if not source_label:
        return None
    key = re.sub(r"[_-]+", " ", source_label.casefold())
    key = " ".join(key.split())
    if source_label.isdecimal():
        return source_label
    return _ALIASES.get(key, "unknown")


def is_visual_entity(label: ObjectTypeInput, *, type_labels_enabled: bool = True) -> bool:
    """Return whether a label can describe a visible physical scene entity.

    Virtual aids are excluded. Missing, unknown, and unrecognized labels are
    retained because absence of a recognized virtual-aid label is not evidence
    that a visible object is virtual.
    """
    normalized = normalize_object_type(label, type_labels_enabled=True)
    return normalized != "virtual_aid"


__all__ = ["ObjectTypeInput", "is_visual_entity", "normalize_object_type"]
