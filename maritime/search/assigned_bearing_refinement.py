"""Assignment-conditioned local geographic pose proposals.

This module provides a deliberately small proposal step. It freezes the
representative catalog keys in an already-scored hypothesis, fits their WGS84
true-north bearings with a bounded nonlinear least-squares solve, and asks the
canonical CandidateScorer to re-evaluate the proposed pose. The scorer remains
responsible for water, coverage, visibility, range, angular merging, type
likelihood, and complete reassignment. Consequently this function never
returns a localization fix or an optimality certificate.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees, hypot, isfinite
from typing import Literal

import numpy as np
from pyproj import Geod, Transformer
from scipy.optimize import least_squares

from maritime.schemas.inputs_v2 import CatalogRecordV2
from maritime.schemas.localization import Hypothesis

from .scoring import CandidateEvaluation, CandidateScorer

AssignedBearingStatus = Literal[
    "improved",
    "no_improvement",
    "invalid_bounds",
    "invalid_assignment",
    "insufficient_assignments",
    "unsupported_calibration",
    "unsupported_covariance",
    "invalid_seed",
    "stale_hypothesis",
    "invalid_water",
    "unresolved_coverage",
    "unresolved_model",
    "solver_failed",
    "max_nfev",
]

_GEOD = Geod(ellps="WGS84")
_MAX_NFEV = 100
_LOCAL_RADIUS_M = 1_000.0


@dataclass(frozen=True, slots=True)
class AssignedBearingRefinementResult:
    """Outcome of one bounded assignment-conditioned pose proposal.

    exact_hypothesis is populated only when the final canonical score is
    strictly lower than original_hypothesis.score. A proposal can be
    numerically valid and still be rejected by the complete scorer; callers
    must retain original_hypothesis for every other status.
    """

    original_hypothesis: Hypothesis
    proposed_pose: tuple[float, float] | None
    exact_hypothesis: Hypothesis | None
    status: AssignedBearingStatus
    solver_status: int | None
    nfev: int
    njev: int | None
    residual_count: int
    residual_evaluations: int
    assignment_count: int
    canonical_evaluations: int = 0
    message: str | None = None

    @property
    def numerical_status(self) -> AssignedBearingStatus:
        """Expose the status using the proposal terminology."""
        return self.status

    @property
    def reevaluated_hypothesis(self) -> Hypothesis | None:
        """Return the exact canonical hypothesis only after strict improvement."""
        return self.exact_hypothesis

    @property
    def improved_hypothesis(self) -> Hypothesis | None:
        """Alias for callers that describe the accepted result as an improvement."""
        return self.exact_hypothesis

    @property
    def proposed_position(self) -> tuple[float, float] | None:
        """Alias for callers that use position terminology."""
        return self.proposed_pose

    @property
    def solver_nfev(self) -> int:
        """Number of bounded least-squares residual evaluations reported by scipy."""
        return self.nfev

    @property
    def solver_njev(self) -> int | None:
        """Number of bounded least-squares Jacobian evaluations, when reported."""
        return self.njev

    @property
    def canonical_evaluation_count(self) -> int:
        """Number of canonical scorer calls used by the proposal."""
        return self.canonical_evaluations

    @property
    def residual_evaluation_count(self) -> int:
        """Number of residual callback calls made during fitting."""
        return self.residual_evaluations


def refine_assigned_bearing(
    scorer: CandidateScorer,
    hypothesis: Hypothesis,
) -> AssignedBearingRefinementResult:
    """Propose a locally fitted pose for the assignments in hypothesis.

    The input hypothesis is expected to be an exact result from scorer.
    Assignments are held fixed only while constructing geometric residuals. A
    final scorer.evaluate call recomputes all geometry and may therefore
    change the assignment set. The local solve is bounded to 1000 m around
    the seed, clipped to SearchConfig.bounds_xy.
    """
    if not isinstance(scorer, CandidateScorer):
        raise TypeError("scorer must be a CandidateScorer instance.")
    if not isinstance(hypothesis, Hypothesis):
        raise TypeError("hypothesis must be a Hypothesis instance.")

    def make_result(
        status: AssignedBearingStatus,
        *,
        proposed_pose: tuple[float, float] | None = None,
        exact_hypothesis: Hypothesis | None = None,
        solver_status: int | None = None,
        nfev: int = 0,
        njev: int | None = None,
        residual_count: int = 0,
        residual_evaluations: int = 0,
        canonical_evaluations: int = 0,
        message: str | None = None,
    ) -> AssignedBearingRefinementResult:
        return AssignedBearingRefinementResult(
            original_hypothesis=hypothesis,
            proposed_pose=proposed_pose,
            exact_hypothesis=exact_hypothesis,
            status=status,
            solver_status=solver_status,
            nfev=nfev,
            njev=njev,
            residual_count=residual_count,
            residual_evaluations=residual_evaluations,
            assignment_count=len(hypothesis.assignments),
            canonical_evaluations=canonical_evaluations,
            message=message,
        )

    config = scorer.config
    bounds = config.bounds_xy
    if bounds is None:
        return make_result("invalid_bounds", message="SearchConfig.bounds_xy is required.")

    measurements = scorer.measurements
    if config.common_bearing_bias_prior_deg is not None or (
        measurements.calibrated_common_bias_mean_deg not in (None, 0.0)
        or measurements.calibrated_common_bias_sigma_deg not in (None, 0.0)
    ):
        return make_result(
            "unsupported_calibration",
            message="Common-bearing-bias calibration is unsupported by this proposal.",
        )
    if measurements.covariance_deg2 is not None:
        return make_result(
            "unsupported_covariance",
            message="Measurement covariance is unsupported by this independent residual fit.",
        )

    xmin, xmax, ymin, ymax = bounds
    seed = (float(hypothesis.east_m), float(hypothesis.north_m))
    if not (xmin <= seed[0] <= xmax and ymin <= seed[1] <= ymax):
        return make_result("invalid_seed", message="Hypothesis lies outside configured bounds.")
    if len(hypothesis.assignments) < 2:
        return make_result(
            "insufficient_assignments",
            message="At least two assigned bearings are required for a 2D pose proposal.",
        )
    report_count = len(measurements.measurements)
    if any(assignment.report_index >= report_count for assignment in hypothesis.assignments):
        return make_result(
            "invalid_assignment",
            message="An assignment refers to a report outside MeasurementSetV2.",
        )

    canonical_evaluations = 0
    try:
        seed_evaluation = scorer.evaluate(seed)
        canonical_evaluations += 1
    except (NotImplementedError, ValueError) as exc:
        status: AssignedBearingStatus = (
            "unsupported_covariance" if isinstance(exc, NotImplementedError) else "invalid_seed"
        )
        return make_result(status, canonical_evaluations=canonical_evaluations, message=str(exc))
    if seed_evaluation.status != "scored" or seed_evaluation.predicted is None:
        return make_result(
            _status_for_evaluation(seed_evaluation),
            canonical_evaluations=canonical_evaluations,
            message=seed_evaluation.reason,
        )
    groups_by_key = {group.representative_key: group for group in seed_evaluation.predicted.groups}
    records_by_key = {record.key: record for record in scorer.catalog.records}
    frozen: list[tuple[int, CatalogRecordV2, float]] = []
    for assignment in hypothesis.assignments:
        group = groups_by_key.get(assignment.catalog_key)
        record = records_by_key.get(assignment.catalog_key)
        if group is None or record is None:
            return make_result(
                "invalid_assignment",
                canonical_evaluations=canonical_evaluations,
                message=(
                    f"Assignment key {assignment.catalog_key!r} is not a visible representative."
                ),
            )
        measurement = measurements.measurements[assignment.report_index]
        report_sigma = (
            config.generated_sigma_deg if measurement.sigma_deg is None else measurement.sigma_deg
        )
        group_sigma = degrees(atan2(group.catalog_uncertainty_m or 0.0, group.range_m))
        sigma = max(config.sigma_floor_deg, hypot(report_sigma, group_sigma))
        frozen.append((assignment.report_index, record, sigma))

    if seed_evaluation.hypothesis != hypothesis:
        return make_result(
            "stale_hypothesis",
            canonical_evaluations=canonical_evaluations,
            message="Input hypothesis does not exactly match the canonical seed score.",
        )

    inverse = Transformer.from_crs(scorer.vector_map.crs, "EPSG:4326", always_xy=True)
    try:
        target_lonlat = {
            record.key: _target_lonlat(
                record, groups_by_key[record.key].visible_feature_offset_quality
            )
            for _report_index, record, _sigma in frozen
        }
    except ValueError as exc:
        return make_result(
            "unresolved_model",
            canonical_evaluations=canonical_evaluations,
            message=str(exc),
        )
    observed = np.asarray(
        [
            measurements.measurements[report_index].bearing_deg
            for report_index, _record, _sigma in frozen
        ],
        dtype=float,
    )
    sigmas = np.asarray([sigma for _report_index, _record, sigma in frozen], dtype=float)
    residual_evaluations = 0

    def residuals(position: np.ndarray) -> np.ndarray:
        nonlocal residual_evaluations
        residual_evaluations += 1
        try:
            lon, lat = inverse.transform(float(position[0]), float(position[1]))
            if not isfinite(lon) or not isfinite(lat):
                raise ValueError("projected pose cannot be transformed to WGS84.")
            predicted = np.asarray(
                [
                    _GEOD.inv(
                        lon,
                        lat,
                        target_lonlat[record.key][0],
                        target_lonlat[record.key][1],
                    )[0]
                    % 360.0
                    for _report_index, record, _sigma in frozen
                ],
                dtype=float,
            )
            wrapped = (predicted - observed + 180.0) % 360.0 - 180.0
            return wrapped / sigmas
        except (TypeError, ValueError, OverflowError):
            return np.full(len(frozen), 1.0e12, dtype=float)

    local_lower = np.maximum(
        np.asarray((xmin, ymin), dtype=float), np.asarray(seed) - _LOCAL_RADIUS_M
    )
    local_upper = np.minimum(
        np.asarray((xmax, ymax), dtype=float), np.asarray(seed) + _LOCAL_RADIUS_M
    )
    if np.any(local_lower >= local_upper):
        return make_result(
            "invalid_bounds",
            canonical_evaluations=canonical_evaluations,
            message="The clipped local bounds have no positive width.",
        )
    try:
        solve = least_squares(
            residuals,
            np.asarray(seed, dtype=float),
            bounds=(local_lower, local_upper),
            method="trf",
            max_nfev=_MAX_NFEV,
        )
    except (TypeError, ValueError, RuntimeError) as exc:
        return make_result(
            "solver_failed",
            canonical_evaluations=canonical_evaluations,
            residual_count=len(frozen),
            residual_evaluations=residual_evaluations,
            message=str(exc),
        )

    solver_nfev = int(solve.nfev)
    solver_njev = None if solve.njev is None else int(solve.njev)
    proposal = (float(solve.x[0]), float(solve.x[1])) if np.isfinite(solve.x).all() else None
    if proposal is None:
        return make_result(
            "solver_failed",
            canonical_evaluations=canonical_evaluations,
            solver_status=int(solve.status),
            nfev=solver_nfev,
            njev=solver_njev,
            residual_count=len(frozen),
            residual_evaluations=residual_evaluations,
            message=str(solve.message),
        )
    if not solve.success:
        return make_result(
            "max_nfev" if solve.status == 0 else "solver_failed",
            canonical_evaluations=canonical_evaluations,
            proposed_pose=proposal,
            solver_status=int(solve.status),
            nfev=solver_nfev,
            njev=solver_njev,
            residual_count=len(frozen),
            residual_evaluations=residual_evaluations,
            message=str(solve.message),
        )

    try:
        proposal_evaluation = scorer.evaluate(proposal)
        canonical_evaluations += 1
    except (NotImplementedError, ValueError) as exc:
        return make_result(
            "unsupported_covariance" if isinstance(exc, NotImplementedError) else "solver_failed",
            canonical_evaluations=canonical_evaluations,
            proposed_pose=proposal,
            solver_status=int(solve.status),
            nfev=solver_nfev,
            njev=solver_njev,
            residual_count=len(frozen),
            residual_evaluations=residual_evaluations,
            message=str(exc),
        )
    if proposal_evaluation.status != "scored" or proposal_evaluation.hypothesis is None:
        return make_result(
            _status_for_evaluation(proposal_evaluation),
            canonical_evaluations=canonical_evaluations,
            proposed_pose=proposal,
            solver_status=int(solve.status),
            nfev=solver_nfev,
            njev=solver_njev,
            residual_count=len(frozen),
            residual_evaluations=residual_evaluations,
            message=proposal_evaluation.reason,
        )
    if proposal_evaluation.hypothesis.score >= hypothesis.score:
        return make_result(
            "no_improvement",
            canonical_evaluations=canonical_evaluations,
            proposed_pose=proposal,
            solver_status=int(solve.status),
            nfev=solver_nfev,
            njev=solver_njev,
            residual_count=len(frozen),
            residual_evaluations=residual_evaluations,
            message=str(solve.message),
        )
    return make_result(
        "improved",
        canonical_evaluations=canonical_evaluations,
        proposed_pose=proposal,
        exact_hypothesis=proposal_evaluation.hypothesis,
        solver_status=int(solve.status),
        nfev=solver_nfev,
        njev=solver_njev,
        residual_count=len(frozen),
        residual_evaluations=residual_evaluations,
        message=str(solve.message),
    )


def propose_assigned_bearing_refinement(
    scorer: CandidateScorer,
    hypothesis: Hypothesis,
) -> AssignedBearingRefinementResult:
    """Named proposal alias for refine_assigned_bearing."""
    return refine_assigned_bearing(scorer, hypothesis)


def assigned_bearing_refinement(
    scorer: CandidateScorer,
    hypothesis: Hypothesis,
) -> AssignedBearingRefinementResult:
    """Compatibility alias for the assignment-conditioned proposal."""
    return refine_assigned_bearing(scorer, hypothesis)


def _status_for_evaluation(evaluation: CandidateEvaluation) -> AssignedBearingStatus:
    if evaluation.status == "invalid_water":
        return "invalid_water"
    if evaluation.status == "unresolved_coverage":
        return "unresolved_coverage"
    if evaluation.status == "unresolved_model":
        return "unresolved_model"
    return "solver_failed"


def _target_lonlat(
    record: CatalogRecordV2,
    offset_quality: str,
) -> tuple[float, float]:
    """Apply the prediction module's calibrated true-east/north offset rule."""
    if offset_quality != "calibrated":
        return record.longitude_deg, record.latitude_deg
    metadata = dict(record.metadata)
    east = metadata.get("visible_feature_offset_east_m")
    north = metadata.get("visible_feature_offset_north_m")
    if (
        isinstance(east, bool)
        or not isinstance(east, (int, float))
        or not isfinite(east)
        or isinstance(north, bool)
        or not isinstance(north, (int, float))
        or not isfinite(north)
    ):
        raise ValueError("Calibrated feature offset metadata is invalid.")
    distance = hypot(float(east), float(north))
    if distance == 0.0:
        return record.longitude_deg, record.latitude_deg
    bearing = degrees(atan2(float(east), float(north))) % 360.0
    lon, lat, _ = _GEOD.fwd(record.longitude_deg, record.latitude_deg, bearing, distance)
    return float(lon), float(lat)


__all__ = [
    "AssignedBearingRefinementResult",
    "AssignedBearingStatus",
    "assigned_bearing_refinement",
    "propose_assigned_bearing_refinement",
    "refine_assigned_bearing",
]
