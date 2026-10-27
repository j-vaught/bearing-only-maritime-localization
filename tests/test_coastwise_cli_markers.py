"""CLI adapter tests for typed marker evidence from MeasurementSetV2."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from maritime.schemas.inputs_v2 import CatalogV2, MapV2, MeasurementSetV2, MeasurementV2
from tools import run_coastwise_marker_search as cli


def _measurements() -> MeasurementSetV2:
    timestamp = "2026-10-26T12:00:00Z"
    return MeasurementSetV2(
        (
            MeasurementV2(359.8, timestamp, object_type="physical_aid"),
            MeasurementV2(45.0, timestamp, object_type="vessel"),
            MeasurementV2(270.0, timestamp, object_type="unclassified-object"),
        ),
        trial_id="typed-marker-adapter",
    )


def test_typed_measurements_only_become_hard_markers_with_explicit_reliability():
    measurements = _measurements()

    untrusted = cli._measurements_to_marker_observations(
        measurements, tolerance_deg=0.5, typed_markers_reliable=False
    )
    trusted = cli._measurements_to_marker_observations(
        measurements, tolerance_deg=0.5, typed_markers_reliable=True
    )

    assert [row.marker_id for row in untrusted] == [
        "measurement-000000@2026-10-26T12:00:00Z#type=physical_aid",
        "measurement-000001@2026-10-26T12:00:00Z#type=vessel",
        "measurement-000002@2026-10-26T12:00:00Z#type=unknown",
    ]
    assert untrusted[0].bearing_deg == pytest.approx(359.8)
    assert untrusted[0].positive
    assert untrusted[0].identity_status == "unresolved"
    assert not untrusted[0].tolerance_calibrated
    assert all(not row.positive for row in untrusted[1:])

    assert trusted[0].positive
    assert trusted[0].identity_status == "resolved"
    assert trusted[0].tolerance_calibrated
    assert all(not row.positive for row in trusted[1:])


def _invoke_cli(
    monkeypatch, *, assert_reliable: bool, measurements: MeasurementSetV2, include_tolerance=True
):
    captured = {}
    read_models = []
    catalog = CatalogV2((), "2026-10-26T12:00:00Z", "test", "1", "cli-marker-test")
    map_v2 = object()

    def read_contract(path, model):
        read_models.append(model)
        if model is CatalogV2:
            return catalog
        if model is MapV2:
            return map_v2
        if model is MeasurementSetV2:
            return measurements
        pytest.fail(f"unexpected contract read: {model.__name__} from {path}")

    def run_search(vector_map, passed_catalog, markers, **kwargs):
        captured.update(
            vector_map=vector_map,
            catalog=passed_catalog,
            markers=markers,
            kwargs=kwargs,
        )
        return SimpleNamespace(
            status="complete",
            traversal=SimpleNamespace(retained=(), unprocessed=()),
            score_errors=(),
        )

    monkeypatch.setattr(cli, "read_contract", read_contract)
    monkeypatch.setattr(
        cli.VectorMap,
        "from_map_v2",
        staticmethod(lambda *_args, **_kwargs: "synthetic-vector-map"),
    )
    monkeypatch.setattr(cli, "run_coastwise_marker_search", run_search)
    arguments = [
        "run_coastwise_marker_search",
        "--catalog",
        "catalog.json",
        "--map",
        "map.json",
        "--measurements",
        "measurements.json",
        *(["--marker-bearing-tolerance-deg", "0.5"] if include_tolerance else []),
        "--bounds-en-m",
        "0",
        "2",
        "0",
        "2",
        "--visibility-radius-m",
        "100",
        "--device-ids",
        "7",
        "--output",
        "result.json",
    ]
    if assert_reliable:
        arguments.append("--assert-typed-markers-reliable")
    monkeypatch.setattr("sys.argv", arguments)
    cli.main()
    return captured, read_models


def test_cli_reads_only_estimator_contracts_and_applies_reliability_opt_in(monkeypatch):
    captured, read_models = _invoke_cli(
        monkeypatch, assert_reliable=True, measurements=_measurements()
    )

    assert read_models == [CatalogV2, MapV2, MeasurementSetV2]
    assert captured["vector_map"] == "synthetic-vector-map"
    assert captured["markers"][0].identity_status == "resolved"
    assert captured["markers"][0].tolerance_calibrated
    assert all(not row.positive for row in captured["markers"][1:])
    assert captured["kwargs"]["device_ids"] == [7]


def test_cli_tolerance_without_reliability_assertion_cannot_hard_prune(monkeypatch):
    captured, read_models = _invoke_cli(
        monkeypatch, assert_reliable=False, measurements=_measurements()
    )

    assert read_models == [CatalogV2, MapV2, MeasurementSetV2]
    typed_aid = captured["markers"][0]
    assert typed_aid.positive
    assert typed_aid.identity_status == "unresolved"
    assert not typed_aid.tolerance_calibrated
    assert all(not row.positive for row in captured["markers"][1:])


def test_cli_requires_tolerance_for_measurement_adapter(monkeypatch):
    with pytest.raises(ValueError, match="tolerance-deg is required"):
        _invoke_cli(
            monkeypatch,
            assert_reliable=True,
            measurements=_measurements(),
            include_tolerance=False,
        )
