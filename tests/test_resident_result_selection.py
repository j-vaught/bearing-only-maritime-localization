"""Saved-output selection checks validity, deterministic ties and diagnostic status."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest


def test_frozen_selector_uses_valid_residuals_and_coordinate_ties(tmp_path):
    cp = pytest.importorskip("cupy")
    if cp.cuda.runtime.getDeviceCount() <= 3:
        pytest.skip("GPU3 preserves the existing run")
    search = tmp_path / "search"
    search.mkdir()
    (search / "summary.json").write_text(json.dumps({"complete": True}))
    (search / "manifest.json").write_text(
        json.dumps({"marker_bearings_deg": [0, 90], "object_keys": ["aid-a", "aid-b"]})
    )
    centers = np.asarray([[6, 6], [5, 5], [3, 3], [2, 3], [2, 2], [1, 1], [0, 0]])
    centers = centers + [40000, 3600000]
    np.savez(
        search / "leaf-0.npz",
        cells=np.column_stack((centers - 0.1, centers + 0.1)),
        residual_deg=np.asarray([0.00001, 0.00002, 0.1, 0.1, 0.1, 0.01, 0.00003]),
        status=np.asarray([0, 0, 0, 0, 0, 2, 0]),
        flags=np.asarray([0, 0, 0, 0, 0, 8, 0]),
        matched_records=np.asarray([[0, -1], [0, 0], [0, 1], [0, 1], [0, 1], [0, 1], [0, 10]]),
    )
    output = tmp_path / "estimate.json"
    result = subprocess.run(
        [sys.executable, "tools/select_resident_result.py", str(search), str(output)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    selected = json.loads(output.read_text())
    assert selected["consistent_centers"] == 3
    assert selected["estimate"]["east_m"] == 40002
    assert selected["estimate"]["north_m"] == 3600002
    assert selected["estimate"]["status"] == 0
    assert selected["diagnostic_best_complete_assignment"]["flags"] == 8
    assert selected["truth_used_for_selection"] is False


def test_cache_only_wrapper_rejects_miss_before_calling_input_builder(tmp_path):
    from tools.run_cached_resident_only import _required_cache

    called = []

    def builder():
        called.append(True)
        raise AssertionError("Input map reconstruction must never start")

    with pytest.raises(RuntimeError, match="rebuilding is disabled"):
        _required_cache(tmp_path, "a" * 64, builder)
    assert called == []


def test_evaluator_rejects_changed_estimate_before_opening_truth(tmp_path, monkeypatch):
    from tools.evaluate_frozen_resident_position import main

    estimate, freeze = tmp_path / "estimate.json", tmp_path / "freeze.json"
    estimate.write_text("{}")
    freeze.write_text(json.dumps({"estimate_sha256": "0" * 64}))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate",
            str(estimate),
            str(freeze),
            str(tmp_path / "truth-does-not-exist.json"),
            str(tmp_path / "accuracy.json"),
        ],
    )
    with pytest.raises(ValueError, match="changed after freezing"):
        main()
