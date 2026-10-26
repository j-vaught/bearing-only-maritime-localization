from pathlib import Path
from runpy import run_path
from types import SimpleNamespace

import numpy as np
from pyproj import Geod

_witness_cell = run_path(
    str(Path(__file__).resolve().parents[2] / "tools" / "diagnose_a35_wedge_omission.py")
)["_witness_cell"]


def test_exact_cell_predicate_includes_quantization_and_rejects_outside_range() -> None:
    geod = Geod(ellps="WGS84")
    lon0, lat0 = -68.0, 44.0
    accepted_lon, accepted_lat, _ = geod.fwd(lon0, lat0, 1.5, 10_000.0)
    rejected_lon, rejected_lat, _ = geod.fwd(lon0, lat0, 1.7, 10_000.0)
    far_lon, far_lat, _ = geod.fwd(lon0, lat0, 0.0, 10_500.0)
    keys = ["true-a", "false-b", "far-c"]
    result = _witness_cell(
        4,
        9,
        lon0,
        lat0,
        (SimpleNamespace(bearing_deg=0.0),),
        keys,
        np.asarray([accepted_lon, rejected_lon, far_lon]),
        np.asarray([accepted_lat, rejected_lat, far_lat]),
        {0: {"true-a"}},
        [0.5],
        cell_size_m=250.0,
        max_range_m=10_000.0,
        angular_tolerance_deg=0.5,
    )
    assert result["matching_count"] == 1
    assert result["by_report"]["0"]["witness_count"] == 1
    assert result["by_report"]["0"]["true_group_member_hits"] == 1
