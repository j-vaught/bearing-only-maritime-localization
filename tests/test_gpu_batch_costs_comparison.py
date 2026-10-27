"""Compare padded GPU likelihood batches against the reference objective."""

from math import atan2, degrees

import numpy as np
import pytest

from maritime.schemas.inputs_v2 import MeasurementSetV2, MeasurementV2
from maritime.search.assignment import assign_likelihood_costs
from maritime.search.config import SearchConfig
from maritime.search.gpu.batch_costs import build_batched_likelihood_costs_cupy
from maritime.search.gpu.batch_scoring import score_prediction_batch_cupy
from maritime.search.likelihood import build_likelihood_costs
from maritime.search.prediction import PredictedGroupSet, PredictedVisibleGroup


def test_batched_gpu_costs_match_reference() -> None:
    cp = pytest.importorskip("cupy")
    try:
        cp.cuda.runtime.getDeviceCount()
    except Exception as error:
        pytest.skip(f"CUDA is unavailable: {error}")
    measurement = MeasurementSetV2(
        (
            MeasurementV2(359.9, "2026-01-01T00:00:00Z", 0.5, "vessel"),
            MeasurementV2(90.0, "2026-01-01T00:00:00Z", 16.0, "buoy"),
        )
    )
    g1 = PredictedVisibleGroup(0.1, "vessel", ("one",), "one", 1000.0, 0.0, 5.0)
    g2 = PredictedVisibleGroup(89.0, "buoy", ("two",), "two", 2000.0, 0.0, None)
    predictions = (
        PredictedGroupSet((g1, g2), "known", ()),
        PredictedGroupSet((g2,), "known", ()),
        PredictedGroupSet((), "known", ()),
    )
    config = SearchConfig()
    costs = build_batched_likelihood_costs_cupy(measurement, predictions, config)
    _, assignments = score_prediction_batch_cupy(measurement, predictions, config)
    for i, pred in enumerate(predictions):
        ref = build_likelihood_costs(
            measurement,
            [g.bearing_deg for g in pred.groups],
            [g.object_type for g in pred.groups],
            config,
            [degrees(atan2(g.catalog_uncertainty_m or 0.0, g.range_m)) for g in pred.groups],
        )
        reference_assignment = assign_likelihood_costs(ref)
        assert float(assignments.device_costs[i]) == pytest.approx(reference_assignment.cost)
        assert assignments.matches[i] == reference_assignment.matches
        n = len(pred.groups)
        np.testing.assert_allclose(
            cp.asnumpy(costs.matched_costs[i, :, :n]), ref.matched_costs, rtol=1e-10, atol=1e-10
        )
        np.testing.assert_allclose(
            cp.asnumpy(costs.residuals_deg[i, :, :n]), ref.residuals_deg, rtol=0, atol=1e-10
        )
        np.testing.assert_allclose(
            cp.asnumpy(costs.report_clutter_costs[i]), ref.report_clutter_costs
        )
        np.testing.assert_allclose(cp.asnumpy(costs.group_miss_costs[i, :n]), ref.group_miss_costs)
        assert float(costs.baseline_constants[i]) == ref.baseline_constant
