"""Independent checks for the coarse assignment-expansion API boundary.

These tests intentionally exercise the public file API rather than the command
line parser. The feature is allowed to reject an inconsistent direct call,
but it must never leak an implementation-local unbound variable error or
create an output that claims calibrated acceptance.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from bearing_localization.accuracy_cli import search_v2_files

from .test_a32 import _v2_fixture


def test_direct_coarse_flag_without_master_fails_closed(tmp_path: Path) -> None:
    """The stage flag alone must reject cleanly or imply the master opt in."""
    if "expand_assignments_from_coarse" not in inspect.signature(search_v2_files).parameters:
        pytest.skip("coarse assignment-expansion API is not integrated in this checkout")

    measurement_path, catalog_path, map_path, config_path, truth_path = _v2_fixture(tmp_path)
    truth_path.unlink()
    result_path = tmp_path / "coarse-direct.json"

    coarse_search: Any = search_v2_files
    try:
        result = coarse_search(
            measurement_path,
            catalog_path,
            map_path,
            config_path,
            "EPSG:32618",
            result_path,
            refine=False,
            **{"expand_assignments_from_coarse": True},
        )
    except ValueError:
        assert not result_path.exists()
        return

    assert result_path.exists()
    assert result.decision_status != "accepted"
    telemetry = json.loads(
        result_path.with_suffix(".json.telemetry.json").read_text(encoding="utf-8")
    )
    expansion = telemetry["diagnostics"]["assignment_expansion"]
    assert expansion["seed_stage"] == "coarse"
    assert telemetry["diagnostics"]["public_evidence"]["calibration_binding_status"] in {
        "assignment_expansion_not_calibrated",
        "scope_not_verified",
    }
    assert not truth_path.exists()
