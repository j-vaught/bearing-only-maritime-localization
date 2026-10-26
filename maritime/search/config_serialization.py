"""Strict JSON handoff helpers for search configuration."""

from __future__ import annotations

import json
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

from maritime.search.config import SearchConfig


def search_config_to_dict(config: SearchConfig) -> dict[str, Any]:
    """Return a JSON-compatible, explicitly tagged search configuration."""
    if not isinstance(config, SearchConfig):
        raise TypeError("config must be a SearchConfig.")
    payload = asdict(config)
    payload["contract_type"] = "SearchConfig"
    return payload


def search_config_from_dict(payload: dict[str, Any]) -> SearchConfig:
    """Validate a strict search configuration mapping."""
    if not isinstance(payload, dict):
        raise TypeError("Search configuration JSON root must be an object.")
    declared_type = payload.get("contract_type")
    if declared_type != "SearchConfig":
        raise ValueError("Search configuration must declare contract_type 'SearchConfig'.")
    accepted = {field.name for field in fields(SearchConfig)} | {"contract_type"}
    unexpected = set(payload) - accepted
    if unexpected:
        raise ValueError(f"Unexpected SearchConfig fields: {sorted(unexpected)}.")
    values = {key: value for key, value in payload.items() if key != "contract_type"}
    for name in ("bounds_xy", "common_bearing_bias_prior_deg"):
        if values.get(name) is not None:
            values[name] = tuple(values[name])
    return SearchConfig(**values)


def dumps_search_config(config: SearchConfig) -> str:
    """Return deterministic compact JSON with finite numbers only."""
    return json.dumps(
        search_config_to_dict(config),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def loads_search_config(document: str) -> SearchConfig:
    """Decode and validate one explicitly tagged search configuration."""
    try:
        payload = json.loads(document, parse_constant=_reject_nonfinite)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid search configuration JSON.") from exc
    return search_config_from_dict(payload)


def write_search_config(path: str | Path, config: SearchConfig) -> None:
    """Write a search configuration as UTF-8 JSON with a stable final newline."""
    Path(path).write_text(dumps_search_config(config) + "\n", encoding="utf-8")


def read_search_config(path: str | Path) -> SearchConfig:
    """Read and validate a search configuration JSON document from disk."""
    return loads_search_config(Path(path).read_text(encoding="utf-8"))


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"Non-finite JSON number {value!r} is not permitted.")
