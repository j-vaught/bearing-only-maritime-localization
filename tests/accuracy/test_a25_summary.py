"""Tests for the frozen development geometry aggregator."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "summarize_development_difficulty.py"
_SPEC = importlib.util.spec_from_file_location("summarize_development_difficulty", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
summarize_difficulty_rows = _MODULE.summarize_difficulty_rows


def row(
    range_m: int,
    report_types: bool,
    supported: bool,
    group_count: int,
    stratum: str,
) -> dict[str, object]:
    return {
        "range_m": range_m,
        "report_types": report_types,
        "supported": supported,
        "difficulty": {
            "matched_group_count": group_count,
            "geometry_stratum": stratum,
        },
    }


def test_aggregate_reports_support_and_geometry_per_gate():
    rows = [
        row(1000, True, True, 4, "<=25m"),
        row(1000, True, False, 2, "singular"),
        row(1000, False, True, 3, "25-100m"),
        row(5000, True, True, 5, ">100m"),
        row(10000, False, False, 1, "singular"),
    ]

    summary = summarize_difficulty_rows(rows)

    assert summary["nominal_trial_count"] == 5
    assert summary["supported_trial_count"] == 3
    assert summary["unsupported_trial_count"] == 2
    assert summary["geometry_strata_all"] == {
        "<=25m": 1,
        "25-100m": 1,
        ">100m": 1,
        "singular": 2,
    }
    assert summary["geometry_strata_supported"] == {
        "<=25m": 1,
        "25-100m": 1,
        ">100m": 1,
        "singular": 0,
    }
    gates = {(gate["range_m"], gate["report_types"]): gate for gate in summary["gates"]}
    assert gates[(1000, True)]["nominal_trial_count"] == 2
    assert gates[(1000, True)]["supported_trial_count"] == 1
    assert gates[(1000, True)]["geometry_strata_all"]["singular"] == 1
    assert gates[(5000, True)]["catalog_match_count_supported"]["median"] == 5
    assert gates[(10000, True)]["nominal_trial_count"] == 0


def test_empty_gate_still_reports_all_geometry_strata():
    summary = summarize_difficulty_rows([])

    assert summary["gates"][0]["nominal_trial_count"] == 0
    assert summary["gates"][0]["geometry_strata_all"] == {
        "<=25m": 0,
        "25-100m": 0,
        ">100m": 0,
        "singular": 0,
    }
    assert summary["catalog_match_count_all"] == {
        "count": 0,
        "min": None,
        "median": None,
        "max": None,
    }
