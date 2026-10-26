from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from pyproj import Geod, Transformer

from bearing_localization.accuracy_cli import _calibration_binding
from bearing_localization.search.config import SearchConfig
from bearing_localization.search.config_serialization import write_search_config
from maritime_localization_shared.contracts.inputs_v2 import (
    CatalogRecordV2,
    CatalogV2,
    MapV2,
    MeasurementSetV2,
    MeasurementV2,
)
from maritime_localization_shared.serialization import write_contract
from tools.run_accuracy_suite import (
    _format_search_argv,
    _load_command_config,
    _search_execution_options,
)


def test_search_execution_options_default_expansion_is_off() -> None:
    command = _load_command_config(Path("benchmarks/accuracy/commands.template.json"))
    options = _search_execution_options(command["search_argv"], algorithm="wedge_geodesic")

    assert options["expand_assignments_enabled"] is False
    assert options["expansion_local_radius_m"] == 1000.0
    assert options["expansion_max_nfev"] == 100
    assert options["expansion_max_branches"] == 5000
    assert "expansion_request_timeout_s" not in options


def test_expansion_options_parse_overrides_and_timeout() -> None:
    argv = [
        "bearing-localization-search",
        "--algorithm",
        "wedge_geodesic",
        "--expand-assignments",
        "--expansion-local-radius-m",
        "1250.5",
        "--expansion-max-nfev",
        "75",
        "--expansion-max-branches",
        "444",
        "--request-timeout-s",
        "321.25",
    ]
    options = _search_execution_options(argv, algorithm="wedge_geodesic")

    assert options["expand_assignments_enabled"] is True
    assert options["expansion_local_radius_m"] == 1250.5
    assert options["expansion_max_nfev"] == 75
    assert options["expansion_max_branches"] == 444
    assert options["expansion_request_timeout_s"] == 321.25


def test_numeric_expansion_options_without_opt_in_do_not_enable_expansion() -> None:
    argv = [
        "--expansion-local-radius-m",
        "1500",
        "--expansion-max-nfev",
        "20",
        "--expansion-max-branches",
        "21",
        "--request-timeout-s",
        "22",
    ]
    options = _search_execution_options(argv, algorithm="grid")
    assert options["expand_assignments_enabled"] is False
    assert options["expansion_local_radius_m"] == 1500.0
    assert options["expansion_max_nfev"] == 20
    assert options["expansion_max_branches"] == 21
    assert "expansion_request_timeout_s" not in options


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--expansion-local-radius-m", "-1"),
        ("--expansion-local-radius-m", "nan"),
        ("--expansion-max-nfev", "0"),
        ("--expansion-max-nfev", "101"),
        ("--expansion-max-branches", "0"),
        ("--expansion-max-branches", "5001"),
        ("--request-timeout-s", "inf"),
        ("--request-timeout-s", "0"),
    ],
)
def test_malformed_or_out_of_range_expansion_values_are_rejected(flag: str, value: str) -> None:
    with pytest.raises((ValueError, OverflowError)):
        _search_execution_options([flag, value], algorithm="grid")


def test_command_template_keeps_expansion_opt_in_false_and_options_concrete() -> None:
    command = json.loads(Path("benchmarks/accuracy/commands.template.json").read_text())
    argv = command["search_argv"]
    assert "--expand-assignments" not in argv
    assert argv[argv.index("--expansion-local-radius-m") + 1] == "1000"
    assert argv[argv.index("--expansion-max-nfev") + 1] == "100"
    assert argv[argv.index("--expansion-max-branches") + 1] == "5000"


def test_search_argv_binds_suite_timeout_only_for_opted_in_expansion() -> None:
    base = ["bearing-localization-search", "--algorithm", "wedge_geodesic", "--output", "out.json"]
    values = {"output": "result.json"}
    disabled = _format_search_argv(base, values, algorithm="wedge_geodesic", timeout_s=123.0)
    assert "--request-timeout-s" not in disabled

    enabled = _format_search_argv(
        [*base, "--expand-assignments"],
        values,
        algorithm="wedge_geodesic",
        timeout_s=123.0,
    )
    assert enabled[-2:] == ["--request-timeout-s", "123.0"]


def _binding(tmp_path: Path, options: dict[str, Any]):
    catalog = tmp_path / "catalog.json"
    coast_map = tmp_path / "map.json"
    scope = tmp_path / "scope.json"
    catalog.write_text("catalog", encoding="utf-8")
    coast_map.write_text("map", encoding="utf-8")
    scope.write_text("scope", encoding="utf-8")
    return _calibration_binding(
        config=SearchConfig(
            algorithm="grid",
            bounds_xy=(500000.0, 500100.0, 4000000.0, 4000100.0),
            max_range_m=1000.0,
            use_types=False,
        ),
        algorithm="grid",
        projected_crs="EPSG:32618",
        catalog_path=catalog,
        map_path=coast_map,
        scope_manifest_path=scope,
        spacing_m=50.0,
        seed=0,
        seed_policy="fixed",
        wedge_error_deg=None,
        wedge_tolerance_deg=0.5,
        wedge_cell_size_m=250.0,
        branch_max_regions=999,
        refine=True,
        refine_modes_count=8,
        refine_initial_step_m=100.0,
        refine_iterations=50,
        **options,
    )


def test_expansion_settings_change_fingerprint_only_when_enabled(tmp_path: Path) -> None:
    baseline = _binding(tmp_path, {})
    changed_disabled = _binding(
        tmp_path,
        {
            "expansion_local_radius_m": 2500.0,
            "expansion_max_nfev": 12,
            "expansion_max_branches": 13,
            "expansion_request_timeout_s": 14.0,
        },
    )
    changed_enabled = _binding(
        tmp_path,
        {
            "expand_assignments_enabled": True,
            "expansion_local_radius_m": 2500.0,
            "expansion_max_nfev": 12,
            "expansion_max_branches": 13,
            "expansion_request_timeout_s": 14.0,
        },
    )
    repeated_enabled = _binding(
        tmp_path,
        {
            "expand_assignments_enabled": True,
            "expansion_local_radius_m": 2500.0,
            "expansion_max_nfev": 12,
            "expansion_max_branches": 13,
            "expansion_request_timeout_s": 14.0,
        },
    )
    assert baseline[1].as_dict() == changed_disabled[1].as_dict()
    assert changed_enabled[1].as_dict() == repeated_enabled[1].as_dict()
    assert baseline[1].as_dict() != changed_enabled[1].as_dict()


def test_coarse_seed_stage_changes_expansion_fingerprint(tmp_path: Path) -> None:
    refined = _binding(
        tmp_path,
        {
            "expand_assignments_enabled": True,
            "expansion_local_radius_m": 1000.0,
            "expansion_max_nfev": 100,
            "expansion_max_branches": 5000,
            "expansion_request_timeout_s": 1800.0,
        },
    )
    coarse = _binding(
        tmp_path,
        {
            "expand_assignments_enabled": True,
            "expand_assignments_from_coarse": True,
            "expansion_local_radius_m": 1000.0,
            "expansion_max_nfev": 100,
            "expansion_max_branches": 5000,
            "expansion_request_timeout_s": 1800.0,
        },
    )
    assert refined[1].as_dict() != coarse[1].as_dict()


def _write_tiny_search_inputs(root: Path) -> tuple[Path, Path, Path, Path]:
    timestamp = "2026-10-26T12:00:00Z"
    lon, lat = -76.0, 37.0
    geod = Geod(ellps="WGS84")
    records = []
    measurements = []
    for index, (bearing, distance) in enumerate(((20.0, 650.0), (138.0, 900.0), (265.0, 1100.0))):
        target_lon, target_lat, _ = geod.fwd(lon, lat, bearing, distance)
        records.append(
            CatalogRecordV2(
                key=f"target-{index}",
                latitude_deg=target_lat,
                longitude_deg=target_lon,
                raw_type="vessel",
                normalized_type="vessel",
            )
        )
        measurements.append(
            MeasurementV2(
                bearing_deg=bearing,
                timestamp_utc=timestamp,
                sigma_deg=0.5,
                object_type="vessel",
            )
        )
    measurement_path = root / "measurements.json"
    catalog_path = root / "catalog.json"
    map_path = root / "map.json"
    config_path = root / "config.json"
    write_contract(
        measurement_path,
        MeasurementSetV2(tuple(measurements), trial_id="expansion-smoke"),
    )
    write_contract(
        catalog_path,
        CatalogV2(tuple(records), timestamp, "tiny", "1", "tiny"),
    )
    ring = [[-77.0, 36.0], [-75.0, 36.0], [-75.0, 38.0], [-77.0, 38.0], [-77.0, 36.0]]
    write_contract(
        map_path,
        MapV2(
            crs="EPSG:4326",
            source="tiny",
            map_version="1",
            coverage_status="known",
            coverage_geometry_geojson=json.dumps(
                {"type": "Polygon", "coordinates": [ring]}, separators=(",", ":")
            ),
        ),
    )
    x, y = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform(lon, lat)
    write_search_config(
        config_path,
        SearchConfig(
            algorithm="grid",
            bounds_xy=(x - 400.0, x + 400.0, y - 400.0, y + 400.0),
            max_range_m=3000.0,
            candidate_budget=25,
        ),
    )
    return measurement_path, catalog_path, map_path, config_path


@pytest.mark.parametrize("enabled", [False, True])
def test_standalone_search_cli_accepts_expansion_settings(tmp_path: Path, enabled: bool) -> None:
    measurement, catalog, coast_map, config = _write_tiny_search_inputs(tmp_path)
    output = tmp_path / "estimate.json"
    command = [
        "bearing-localization-search",
        "--schema-version",
        "2",
        "--measurements",
        str(measurement),
        "--catalog",
        str(catalog),
        "--map",
        str(coast_map),
        "--config",
        str(config),
        "--projected-crs",
        "EPSG:32618",
        "--output",
        str(output),
        "--spacing-m",
        "100",
        "--refine-modes",
        "2",
        "--expansion-local-radius-m",
        "800",
        "--expansion-max-nfev",
        "9",
        "--expansion-max-branches",
        "7",
    ]
    if enabled:
        command.extend(
            [
                "--expand-assignments",
                "--request-timeout-s",
                "30",
            ]
        )
    process = subprocess.run(command, capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    telemetry = json.loads(output.with_suffix(".json.telemetry.json").read_text(encoding="utf-8"))
    if not enabled:
        assert "assignment_expansion" not in telemetry["diagnostics"]
        return
    expansion = telemetry["diagnostics"]["assignment_expansion"]
    assert expansion["enabled"] is True
    if enabled:
        assert expansion["request_timeout_s"] == 30.0
        assert expansion["config"] == {
            "local_radius_m": 800.0,
            "max_nfev": 9,
            "max_branches": 7,
        }
    else:
        assert expansion["selected_mode_count"] == 0


def test_suite_timeout_overrides_template_internal_deadline_and_is_opt_in_only() -> None:
    values = {}
    template = [
        "search",
        "--request-timeout-s",
        "999",
        "--expand-assignments",
    ]
    enabled = _format_search_argv(template, values, algorithm="grid", timeout_s=123.0)
    assert enabled[enabled.index("--request-timeout-s") + 1] == "123.0"

    disabled = _format_search_argv(
        ["search", "--request-timeout-s", "999"],
        values,
        algorithm="grid",
        timeout_s=123.0,
    )
    assert "--request-timeout-s" not in disabled


def test_coarse_seed_stage_is_opt_in_and_old_and_new_flags_conflict() -> None:
    options = _search_execution_options(
        [
            "--expand-assignments-from-coarse",
            "--expansion-local-radius-m",
            "700",
        ],
        algorithm="grid",
    )
    assert options["expand_assignments_enabled"] is True
    assert options["expand_assignments_from_coarse"] is True
    assert options["expansion_local_radius_m"] == 700.0

    with pytest.raises(ValueError, match="mutually exclusive"):
        _search_execution_options(
            ["--expand-assignments", "--expand-assignments-from-coarse"],
            algorithm="grid",
        )


def test_coarse_expansion_flag_does_not_add_pattern_refinement() -> None:
    argv = _format_search_argv(
        [
            "search",
            "--expand-assignments-from-coarse",
            "--no-refine",
        ],
        {},
        algorithm="grid",
        timeout_s=45.0,
    )
    assert "--no-refine" in argv
    assert argv[-2:] == ["--request-timeout-s", "45.0"]


def test_standalone_coarse_expansion_runs_without_pattern_refinement(tmp_path: Path) -> None:
    measurement, catalog, coast_map, config = _write_tiny_search_inputs(tmp_path)
    output = tmp_path / "coarse-estimate.json"
    command = [
        "bearing-localization-search",
        "--schema-version",
        "2",
        "--measurements",
        str(measurement),
        "--catalog",
        str(catalog),
        "--map",
        str(coast_map),
        "--config",
        str(config),
        "--projected-crs",
        "EPSG:32618",
        "--output",
        str(output),
        "--spacing-m",
        "100",
        "--no-refine",
        "--expand-assignments-from-coarse",
        "--expansion-local-radius-m",
        "800",
        "--expansion-max-nfev",
        "9",
        "--expansion-max-branches",
        "7",
        "--request-timeout-s",
        "30",
    ]
    process = subprocess.run(command, capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    telemetry = json.loads(output.with_suffix(".json.telemetry.json").read_text(encoding="utf-8"))
    expansion = telemetry["diagnostics"]["assignment_expansion"]
    assert expansion["enabled"] is True
    assert expansion["seed_stage"] == "coarse"
    assert expansion["request_timeout_s"] == 30.0
