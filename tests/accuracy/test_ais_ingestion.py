from __future__ import annotations

from pathlib import Path

import pytest

from maritime_localization_ingest.ais import catalog_from_ais_rows, read_ais_csv
from maritime_localization_ingest.measurements import measurement_set_v2_from_observations
from maritime_localization_shared.contracts.observation import MeasurementSet, Observation


def row(stamp: str, *, lat: float = 37.0, lon: float = -76.0, **extra: object) -> dict[str, object]:
    return {
        "mmsi": "123456789",
        "timestamp_utc": stamp,
        "latitude_deg": lat,
        "longitude_deg": lon,
        **extra,
    }


def test_stationary_bracket_is_interpolated_at_target() -> None:
    catalog = catalog_from_ais_rows(
        [row("2026-01-01T00:00:00Z"), row("2026-01-01T00:01:00Z")],
        target_time_utc="2026-01-01T00:00:30Z",
        source="fixture",
    )
    record = catalog.records[0]
    assert record.latitude_deg == pytest.approx(37.0)
    assert record.longitude_deg == pytest.approx(-76.0)
    assert record.report_time_utc == "2026-01-01T00:00:30Z"
    assert dict(record.provenance)["source_timestamps_utc"] == [
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:01:00Z",
    ]
    assert dict(record.metadata)["motion_status"] == "interpolated"


def test_moving_bracket_uses_geodesic_interpolation() -> None:
    catalog = catalog_from_ais_rows(
        [row("2026-01-01T00:00:00Z", lon=-76.0), row("2026-01-01T00:01:00Z", lon=-75.998)],
        target_time_utc="2026-01-01T00:00:30Z",
        source="fixture",
    )
    assert catalog.records[0].longitude_deg == pytest.approx(-75.999, abs=1e-7)
    assert catalog.records[0].motion_reference == "target_time_interpolation"


def test_unbracketed_and_wide_bracket_reports_are_explicitly_stale() -> None:
    stale = catalog_from_ais_rows(
        [row("2026-01-01T00:00:00Z")],
        target_time_utc="2026-01-01T00:02:00Z",
        source="fixture",
    ).records[0]
    assert stale.report_time_utc == "2026-01-01T00:00:00Z"
    assert dict(stale.metadata)["motion_status"] == "stale_extrapolation_disabled"
    wide = catalog_from_ais_rows(
        [row("2026-01-01T00:00:00Z"), row("2026-01-01T00:01:01Z")],
        target_time_utc="2026-01-01T00:00:30Z",
        source="fixture",
    ).records[0]
    assert dict(wide.metadata)["motion_status"] == "stale_bracket_too_wide"


def test_exact_60_second_bracket_is_accepted_but_extrapolation_is_disabled() -> None:
    catalog = catalog_from_ais_rows(
        [row("2026-01-01T00:00:00Z"), row("2026-01-01T00:01:00Z")],
        target_time_utc="2026-01-01T00:00:30Z",
        source="fixture",
        max_bracket_seconds=60,
    )
    assert dict(catalog.records[0].metadata)["motion_status"] == "interpolated"
    with pytest.raises(ValueError, match="not supported"):
        catalog_from_ais_rows(
            [row("2026-01-01T00:00:00Z")],
            target_time_utc="2026-01-01T00:00:30Z",
            source="fixture",
            allow_extrapolation=True,
        )


def test_missing_motion_dimensions_are_preserved_as_missing() -> None:
    record = catalog_from_ais_rows(
        [row("2026-01-01T00:00:00Z", sog_kn=2.0)],
        target_time_utc="2026-01-01T00:00:00Z",
        source="fixture",
    ).records[0]
    assert record.speed_knots == 2.0
    assert record.course_deg is None
    assert dict(record.metadata)["motion_valid"] is False


def test_malformed_motion_and_ais_sentinels_are_rejected() -> None:
    for speed, course in (("bad", "bad"), (102.3, 360.0), (-1, 361)):
        record = catalog_from_ais_rows(
            [row("2026-01-01T00:00:00Z", sog_kn=speed, cog_deg=course)],
            target_time_utc="2026-01-01T00:00:00Z",
            source="fixture",
        ).records[0]
        assert record.speed_knots is None
        assert record.course_deg is None
        assert dict(record.metadata)["motion_valid"] is False


def test_observer_is_excluded_and_mmsi_keys_are_stable() -> None:
    catalog = catalog_from_ais_rows(
        [row("2026-01-01T00:00:00Z"), {**row("2026-01-01T00:00:00Z"), "mmsi": "999"}],
        target_time_utc="2026-01-01T00:00:00Z",
        source="fixture",
        observer_id="123456789",
    )
    assert [record.key for record in catalog.records] == ["mmsi:999"]


def test_legacy_csv_uses_source_key_without_inventing_mmsi() -> None:
    root = Path(__file__).parents[2]
    catalog = read_ais_csv(
        root / "data/eastern-seaboard_2023-07-15T1200Z.csv",
        target_time_utc="2023-07-15T12:00:30Z",
    )
    record = catalog.records[0]
    assert record.key.startswith("source:")
    assert dict(record.provenance)["mmsi_present"] is False
    assert dict(record.metadata)["motion_status"] == "stale_extrapolation_disabled"
    assert dict(record.metadata)["uncertainty_status"] == "unspecified_by_source"
    assert record.report_time_utc != catalog.timestamp_utc


def test_measurement_replay_preserves_north_wrap_sigma_extent_and_timestamps() -> None:
    old = MeasurementSet(
        (
            Observation(-0.25, 0.7, "vessel", 0.03),
            Observation(179.5, 1.2, None, 0.0),
        ),
        trial_id="run-1",
        timestamp_utc="2026-01-01T00:00:00Z",
    )
    replay = measurement_set_v2_from_observations(
        old,
        timestamps_utc=("2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z"),
        calibrated_common_bias_mean_deg=0.2,
        calibrated_common_bias_sigma_deg=0.1,
    )
    assert [item.bearing_deg for item in replay.measurements] == pytest.approx([359.75, 179.5])
    assert [item.sigma_deg for item in replay.measurements] == [0.7, 1.2]
    assert [item.angular_half_width_deg for item in replay.measurements] == [0.03, 0.0]
    assert [item.timestamp_utc for item in replay.measurements] == [
        "2026-01-01T00:00:01Z",
        "2026-01-01T00:00:02Z",
    ]
    assert replay.calibrated_common_bias_mean_deg == 0.2
    assert replay.calibrated_common_bias_sigma_deg == 0.1
