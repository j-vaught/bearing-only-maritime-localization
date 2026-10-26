"""Version-two standalone command helpers for simulation, search, and evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, fields
from math import hypot, isfinite
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, cast

import numpy as np
from pyproj import CRS, Transformer

from bearing_localization.evaluation.metrics import (
    SupportManifest,
    evaluate_localization,
)
from bearing_localization.search.bounds import BoundsResult
from bearing_localization.search.branch_bound import BranchBoundContext, branch_and_bound_search
from bearing_localization.search.calibration import (
    CalibrationContext,
    CalibrationOperatingCondition,
    implementation_sha256,
    load_frozen_calibration,
    search_execution_sha256,
)
from bearing_localization.search.config import SearchConfig
from bearing_localization.search.config_serialization import (
    loads_search_config,
    read_search_config,
)
from bearing_localization.search.decision import CompetitorCoverageEvidence, apply_accuracy_decision
from bearing_localization.search.grid import run_grid_search_v2
from bearing_localization.search.hierarchical import run_hierarchical_search_v2
from bearing_localization.search.modes import SearchModeEvidence, converged_mode_evidence
from bearing_localization.search.pairwise import run_pairwise_search_v2
from bearing_localization.search.proposals.wedge import (
    GeographicWedgeCatalogObject,
    WedgeObservation,
    generate_geographic_reverse_wedge_proposals,
)
from bearing_localization.search.randomized import run_randomized_search_v2
from bearing_localization.search.reference_grid import search_reference_grid
from bearing_localization.search.refinement import refine_modes
from bearing_localization.search.scoring import CandidateScorer, score_candidate_positions
from bearing_localization.search.telemetry import SearchTelemetry
from bearing_localization.simulator.mismatch import (
    KnownWaterObserver,
    PhysicalObjectV2,
    PhysicalSceneV2,
    SensorConfigurationV2,
    SimulatorMismatchConfig,
)
from bearing_localization.simulator.runner import run_simulator_v2
from maritime_localization_shared.contracts import EvaluationTruth
from maritime_localization_shared.contracts.inputs_v2 import CatalogV2, MapV2, MeasurementSetV2
from maritime_localization_shared.contracts.localization import LocalizationResult
from maritime_localization_shared.geospatial.coordinates import LocalFrame
from maritime_localization_shared.geospatial.vector_map import VectorMap
from maritime_localization_shared.serialization import (
    contract_from_dict,
    loads_contract,
    read_contract,
    write_contract,
)


def _candidate_outcome_evidence(
    diagnostics: dict[str, Any],
) -> tuple[dict[str, int] | None, int | None]:
    """Return evaluated outcome counts; absence is unknown and must fail closed."""
    raw = diagnostics.get("outcome_counts")
    if raw is None:
        raw = diagnostics.get("candidate_counts")
    if not isinstance(raw, dict):
        return None, None
    names = ("scored", "invalid_water", "unresolved_coverage", "unresolved_model")
    if any(type(raw.get(name)) is not int or raw[name] < 0 for name in names):
        return None, None
    normalized = {name: raw[name] for name in names}
    expected_count = diagnostics.get("candidate_count", raw.get("evaluated"))
    if type(expected_count) is not int or expected_count < 0:
        return None, None
    if sum(normalized.values()) != expected_count:
        return None, None
    unresolved = normalized["unresolved_coverage"] + normalized["unresolved_model"]
    return normalized, unresolved


def _top_mode_evidence_index(
    result: LocalizationResult, modes: Sequence[SearchModeEvidence]
) -> int | None:
    """Find the evidence record that exactly supports the ranked result top."""
    if not result.hypotheses:
        return None
    top = result.hypotheses[0]
    top_keys = tuple(sorted(assignment.catalog_key for assignment in top.assignments))
    for index, mode in enumerate(modes):
        if hypot(top.east_m - mode.east_m, top.north_m - mode.north_m) > 1e-6:
            continue
        score_tolerance = max(1e-12, 1e-10 * max(abs(top.score), abs(mode.score)))
        if abs(top.score - mode.score) > score_tolerance or top_keys != mode.matched_catalog_keys:
            continue
        if top.uncertainty is None:
            continue
        radius_tolerance = max(
            1e-8,
            1e-10 * max(top.uncertainty.major_radius_m, mode.radius_95_m),
        )
        if abs(top.uncertainty.major_radius_m - mode.radius_95_m) <= radius_tolerance:
            return index
    return None


def _distant_region_bound_evidence(
    diagnostics: dict[str, Any],
) -> tuple[float | None, str | None, float | None, float | None]:
    """Read only explicitly scoped, anchored margin-bound evidence; never infer it."""
    lower_bound = diagnostics.get("distant_region_score_lower_bound")
    bound_scope = diagnostics.get("distant_region_bound_scope")
    top_east_m = diagnostics.get("distant_region_bound_top_east_m")
    top_north_m = diagnostics.get("distant_region_bound_top_north_m")
    if (
        isinstance(lower_bound, bool)
        or not isinstance(lower_bound, (int, float))
        or not isfinite(lower_bound)
        or bound_scope != "all_objective_positions_at_least_100m_from_top"
        or isinstance(top_east_m, bool)
        or not isinstance(top_east_m, (int, float))
        or not isfinite(top_east_m)
        or isinstance(top_north_m, bool)
        or not isinstance(top_north_m, (int, float))
        or not isfinite(top_north_m)
    ):
        return None, None, None, None
    return float(lower_bound), bound_scope, float(top_east_m), float(top_north_m)


def _raw_distant_hypothesis_evidence(result: LocalizationResult) -> dict[str, Any]:
    """Serialize raw distant candidates as veto-only evidence, never competitors."""
    if not result.hypotheses:
        return {"top_score": None, "score_tolerance": None, "alternatives": []}
    top = result.hypotheses[0]
    score_tolerance = max(1e-12, 1e-10 * abs(top.score))
    alternatives = []
    for hypothesis in result.hypotheses[1:]:
        distance = hypot(hypothesis.east_m - top.east_m, hypothesis.north_m - top.north_m)
        if distance < 100.0:
            continue
        alternatives.append(
            {
                "east_m": hypothesis.east_m,
                "north_m": hypothesis.north_m,
                "score": hypothesis.score,
                "score_gap": hypothesis.score - top.score,
                "distance_from_top_m": distance,
            }
        )
    return {
        "top_score": top.score,
        "score_tolerance": score_tolerance,
        "alternatives": alternatives,
    }


def _file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _calibration_binding(
    *,
    config: SearchConfig,
    algorithm: str,
    projected_crs: str,
    catalog_path: str | Path,
    map_path: str | Path,
    scope_manifest_path: str | Path | None,
    spacing_m: float,
    seed: int,
    seed_policy: str,
    wedge_error_deg: float | None,
    wedge_tolerance_deg: float,
    wedge_cell_size_m: float,
    branch_max_regions: int,
    refine: bool,
    refine_modes_count: int,
    refine_initial_step_m: float,
    refine_iterations: int,
    device_ids: Sequence[int],
) -> tuple[CalibrationOperatingCondition, CalibrationContext]:
    """Bind frozen release decisions to the exact context and execution."""
    if seed_policy not in ("fixed", "per_trial_variable"):
        raise ValueError("seed_policy must be fixed or per_trial_variable.")
    config_fields = asdict(config)
    condition = CalibrationOperatingCondition(
        range_m=config_fields["max_range_m"],
        use_types=config_fields["use_types"],
        objective_version=config_fields["objective_version"],
        algorithm=algorithm,
    )
    execution_settings = {
        "search_config": config_fields,
        "algorithm": algorithm,
        "projected_crs": CRS.from_user_input(projected_crs).to_string(),
        "spacing_m": spacing_m,
        "seed": seed,
        "seed_policy": seed_policy,
        "wedge_error_deg": wedge_error_deg,
        "wedge_tolerance_deg": wedge_tolerance_deg,
        "wedge_cell_size_m": wedge_cell_size_m,
        "branch_max_regions": branch_max_regions,
        "refine": refine,
        "refine_modes_count": refine_modes_count,
        "refine_initial_step_m": refine_initial_step_m,
        "refine_iterations": refine_iterations,
        "backend": config_fields["backend"],
        "device_ids": list(device_ids),
    }
    scope_sha256 = (
        _file_sha256(scope_manifest_path)
        if scope_manifest_path is not None
        else hashlib.sha256(b"scope-manifest-v2:not-provided").hexdigest()
    )
    context = CalibrationContext(
        catalog_sha256=_file_sha256(catalog_path),
        map_sha256=_file_sha256(map_path),
        scope_manifest_sha256=scope_sha256,
        search_execution_sha256=search_execution_sha256(
            execution_settings, seed_policy=seed_policy
        ),
        implementation_sha256=implementation_sha256(Path(__file__).resolve().parents[1]),
    )
    return condition, context


def _write_json(path: str | Path, value: object) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )


def _load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=_reject_constant)
    if not isinstance(payload, dict):
        raise ValueError("JSON document root must be an object.")
    return payload


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number {value!r} is not permitted.")


def _construct_dataclass(model: type, payload: object, *, name: str):
    if not isinstance(payload, dict):
        raise TypeError(f"{name} must be a JSON object.")
    allowed = {item.name for item in fields(cast(Any, model))}
    extra = set(payload) - allowed
    if extra:
        raise ValueError(f"Unexpected {name} fields: {sorted(extra)}.")
    return model(**payload)


def _read_support_manifest(path: str | Path) -> SupportManifest:
    """Read producer support JSON with an optional, verified derived flag."""
    payload = _load_json(path)
    derived_marker = object()
    derived_supported = payload.pop("supported", derived_marker)
    manifest = _construct_dataclass(SupportManifest, payload, name="SupportManifest")
    if derived_supported is not derived_marker:
        if type(derived_supported) is not bool:
            raise TypeError("SupportManifest supported must be bool when present.")
        if derived_supported != manifest.supported:
            raise ValueError("SupportManifest supported disagrees with its source fields.")
    return manifest


def _search_dispatch(
    scorer: CandidateScorer,
    *,
    algorithm: str,
    spacing_m: float,
    seed: int,
    wedge_error_deg: float | None,
    wedge_tolerance_deg: float,
    wedge_cell_size_m: float,
    branch_max_regions: int,
) -> tuple[LocalizationResult, dict[str, Any], int, bool]:
    config = scorer.config
    if algorithm == "grid":
        if config.backend == "gpu":
            from bearing_localization.search.gpu.search_adapter import (
                GpuCandidateScorer,
                score_candidate_positions_batched,
            )
            from bearing_localization.search.reference_grid import enumerate_grid_points

            if not isinstance(scorer, GpuCandidateScorer):
                raise TypeError("GPU grid search requires GpuCandidateScorer.")
            assert config.bounds_xy is not None
            from bearing_localization.search.candidate_order import order_candidate_positions

            positions = order_candidate_positions(
                enumerate_grid_points(config.bounds_xy, spacing_m),
                water_status=scorer.vector_map.point_water_status,
                bounds=config.bounds_xy,
            )
            run = score_candidate_positions_batched(
                scorer,
                positions,
                candidate_limit=config.candidate_budget,
                certificate_scope="finite_candidate_set",
            )
        else:
            run = run_grid_search_v2(scorer, grid_spacing_m=spacing_m)
        return (
            run.localization,
            {"candidate_count": run.candidate_count, "outcome_counts": dict(run.outcome_counts)},
            run.candidate_count,
            run.localization.search_status == "completed",
        )
    if algorithm == "hierarchical":
        run = run_hierarchical_search_v2(scorer, coarse_spacing_m=spacing_m)
        return (
            run.localization,
            {"candidate_count": run.candidate_count, "outcome_counts": dict(run.outcome_counts)},
            run.candidate_count,
            False,
        )
    if algorithm == "randomized":
        run = run_randomized_search_v2(scorer, seed=seed)
        return (
            run.localization,
            {"candidate_count": run.candidate_count, "outcome_counts": dict(run.outcome_counts)},
            run.candidate_count,
            False,
        )
    if algorithm == "pairwise":
        run = run_pairwise_search_v2(scorer)
        return (
            run.localization,
            {"candidate_count": run.candidate_count, "outcome_counts": dict(run.outcome_counts)},
            run.candidate_count,
            False,
        )
    if algorithm == "reference_grid":
        if config.backend == "gpu":
            from itertools import islice

            from bearing_localization.search.gpu.search_adapter import (
                GpuCandidateScorer,
                score_candidate_positions_batched,
            )
            from bearing_localization.search.reference_grid import (
                CandidateCounts,
                _local_modes,
                enumerate_grid_points,
                grid_shape,
            )
            from maritime_localization_shared.contracts.localization import LocalizationResult

            if not isinstance(scorer, GpuCandidateScorer):
                raise TypeError("GPU reference-grid search requires GpuCandidateScorer.")
            bounds = config.bounds_xy
            if bounds is None:
                raise ValueError("Reference grid search requires config.bounds_xy.")
            shape = grid_shape(bounds, spacing_m)
            total = shape[0] * shape[1]
            run = score_candidate_positions_batched(
                scorer,
                enumerate_grid_points(bounds, spacing_m),
                candidate_limit=config.candidate_budget,
                certificate_scope="finite_candidate_set",
            )
            outcome_counts = dict(run.outcome_counts)
            evaluated = run.candidate_count
            counts = CandidateCounts(
                total=total,
                evaluated=evaluated,
                scored=outcome_counts.get("scored", 0),
                invalid_water=outcome_counts.get("invalid_water", 0),
                unresolved_coverage=outcome_counts.get("unresolved_coverage", 0),
                unresolved_model=outcome_counts.get("unresolved_model", 0),
                unevaluated=total - evaluated,
            )
            by_position = {
                (round(item.east_m, 9), round(item.north_m, 9)): item
                for item in run.localization.hypotheses
            }
            scored = {}
            for ordinal, point in enumerate(
                islice(enumerate_grid_points(bounds, spacing_m), evaluated)
            ):
                hypothesis = by_position.get((round(point[0], 9), round(point[1], 9)))
                if hypothesis is not None:
                    scored[(ordinal // shape[1], ordinal % shape[1])] = hypothesis
            modes = _local_modes(
                scored,
                nx=shape[0],
                ny=shape[1],
                absolute_tolerance=config.absolute_score_tolerance,
                relative_tolerance=config.relative_score_tolerance,
            )
            hypotheses = tuple(mode.hypothesis for mode in modes)
            localization = LocalizationResult(
                hypotheses=hypotheses,
                elapsed_s=run.localization.elapsed_s,
                decision_status="ambiguous" if hypotheses else "no_fix",
                search_status=run.localization.search_status,
                certificate_scope=run.localization.certificate_scope,
                budget_limit=run.localization.budget_limit,
                budget_used=run.localization.budget_used,
                report_count=run.localization.report_count,
                objective_version=run.localization.objective_version,
            )
            complete = counts.unevaluated == 0
            return (
                localization,
                {
                    "candidate_counts": asdict(counts),
                    "grid_shape": shape,
                    "mode_count": len(modes),
                },
                evaluated,
                complete,
            )
        run = search_reference_grid(
            scorer.measurements, scorer.catalog, scorer.vector_map, config, spacing_m=spacing_m
        )
        complete = run.candidate_counts.unevaluated == 0
        return (
            run.localization_result,
            {
                "candidate_counts": asdict(run.candidate_counts),
                "grid_shape": run.grid_shape,
                "mode_count": len(run.modes),
            },
            run.candidate_counts.evaluated,
            complete,
        )
    if algorithm == "branch_bound":
        # No region-bound model is certified for the fully dynamic map-aware objective yet.
        def unresolved_bound(_context: BranchBoundContext) -> BoundsResult:
            return BoundsResult(
                None, (), "map_visibility_grouping_and_variance_not_frozen", None, None
            )

        run = branch_and_bound_search(
            scorer,
            unresolved_bound,
            max_candidates=config.candidate_budget,
            max_regions=branch_max_regions,
        )
        diagnostics = run.diagnostic_dict()
        diagnostics["outcome_counts"] = None
        diagnostics["unresolved_region_count"] = len(run.unresolved_regions)
        complete = (
            run.localization.certificate_scope == "continuous_objective"
            and run.localization.bound_gap is not None
            and run.localization.bound_gap <= 1e-8
        )
        return run.localization, diagnostics, run.candidates_evaluated, complete
    if algorithm == "wedge":
        if wedge_error_deg is None:
            raise ValueError("algorithm wedge requires --max-grid-bearing-error-deg.")
        bounds = config.bounds_xy
        if bounds is None:
            raise ValueError("Wedge search requires finite config.bounds_xy.")
        xmin, xmax, ymin, ymax = bounds
        to_geo = Transformer.from_crs(scorer.vector_map.crs, "EPSG:4326", always_xy=True)
        origin = to_geo.transform((xmin + xmax) / 2.0, (ymin + ymax) / 2.0)
        frame = LocalFrame((float(origin[0]), float(origin[1])))
        proposals = generate_geographic_reverse_wedge_proposals(
            [
                WedgeObservation(index, item.bearing_deg)
                for index, item in enumerate(scorer.measurements.measurements)
            ],
            [
                GeographicWedgeCatalogObject(
                    record.key, record.longitude_deg, record.latitude_deg, record.normalized_type
                )
                for record in scorer.catalog.records
            ],
            frame=frame,
            max_range_m=config.max_range_m,
            cell_size_m=wedge_cell_size_m,
            angular_tolerance_deg=wedge_tolerance_deg,
            max_grid_bearing_error_deg=wedge_error_deg,
            max_candidate_cells=config.candidate_budget,
        )
        to_map = Transformer.from_crs("EPSG:4326", scorer.vector_map.crs, always_xy=True)
        positions = []
        for proposal in proposals.proposals:
            lon, lat = frame.to_geographic(proposal.east_m, proposal.north_m)
            east_m, north_m = to_map.transform(lon, lat)
            positions.append((float(east_m), float(north_m)))
        run = score_candidate_positions(
            scorer,
            positions,
            search_status="completed"
            if proposals.generation_status == "completed"
            else "budget_exhausted",
            budget_limit=config.candidate_budget
            if not proposals.generation_status == "completed"
            else None,
            budget_used=config.candidate_budget
            if not proposals.generation_status == "completed"
            else None,
            candidate_limit=config.candidate_budget,
        )
        diagnostics = {
            "proposal_count": len(proposals.proposals),
            "source_pairs_total": proposals.source_pairs_total,
            "source_pairs_processed": proposals.source_pairs_processed,
            "candidate_cells_total": proposals.candidate_cells_total,
            "generation_status": proposals.generation_status,
            "grid_bearing_error_bound_deg": wedge_error_deg,
            "candidate_count": run.candidate_count,
            "outcome_counts": dict(run.outcome_counts),
        }
        return run.localization, diagnostics, run.candidate_count, False
    raise ValueError(f"Unsupported v2 search algorithm {algorithm!r}.")


def _validate_scope_manifest(
    path: str | Path,
    *,
    catalog_path: str | Path,
    map_path: str | Path,
    projected_crs: str,
    bounds_xy: tuple[float, float, float, float],
) -> None:
    """Verify a full-coast scope declaration is bound to the exact search files."""
    manifest = _load_json(path)
    expected = {
        "schema_version",
        "geographic_scope",
        "catalog_sha256",
        "map_sha256",
        "projected_crs",
        "bounds_xy_m",
    }
    if set(manifest) != expected:
        raise ValueError(f"Scope manifest fields must be exactly {sorted(expected)}.")
    if manifest["schema_version"] != 1 or manifest["geographic_scope"] != "full_coast":
        raise ValueError("Scope manifest must declare schema_version 1 and full_coast.")
    checksums = {
        "catalog_sha256": hashlib.sha256(Path(catalog_path).read_bytes()).hexdigest(),
        "map_sha256": hashlib.sha256(Path(map_path).read_bytes()).hexdigest(),
    }
    if any(manifest.get(key) != value for key, value in checksums.items()):
        raise ValueError("Scope manifest does not match the supplied CatalogV2 and MapV2 files.")
    if not CRS.from_user_input(manifest["projected_crs"]).equals(
        CRS.from_user_input(projected_crs)
    ):
        raise ValueError("Scope manifest projected_crs does not match the CLI projection.")
    declared_bounds = manifest["bounds_xy_m"]
    if not isinstance(declared_bounds, list) or tuple(declared_bounds) != bounds_xy:
        raise ValueError("Scope manifest bounds_xy_m must exactly match SearchConfig.bounds_xy.")


@dataclass(frozen=True, slots=True)
class _StaticSearchSnapshot:
    catalog_sha256: str
    map_sha256: str
    catalog: CatalogV2
    map_input: MapV2
    vector_map: VectorMap
    projected_crs: str


@dataclass(frozen=True, slots=True, init=False)
class SearchV2Session:
    """Reuse validated catalog, map, and projection across independent requests.

    Static contracts are parsed from the exact byte strings whose hashes are
    retained. Requests fail explicitly if either source file changes.
    """

    catalog_path: Path
    map_path: Path
    projected_crs: str
    _snapshot: _StaticSearchSnapshot
    _clock: Callable[[], float]
    startup_elapsed_s: float

    def __init__(
        self,
        catalog_path: str | Path,
        map_path: str | Path,
        projected_crs: str,
        *,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        if not callable(clock):
            raise TypeError("clock must be callable.")
        started = clock()
        normalized_catalog_path = Path(catalog_path).expanduser().resolve()
        normalized_map_path = Path(map_path).expanduser().resolve()
        normalized_crs = CRS.from_user_input(projected_crs).to_string()
        catalog_bytes = normalized_catalog_path.read_bytes()
        map_bytes = normalized_map_path.read_bytes()
        catalog = loads_contract(catalog_bytes.decode("utf-8"), CatalogV2)
        map_input = loads_contract(map_bytes.decode("utf-8"), MapV2)
        vector_map = VectorMap.from_map_v2(map_input, target_crs=normalized_crs)
        snapshot = _StaticSearchSnapshot(
            hashlib.sha256(catalog_bytes).hexdigest(),
            hashlib.sha256(map_bytes).hexdigest(),
            catalog,
            map_input,
            vector_map,
            normalized_crs,
        )
        startup_elapsed = clock() - started
        if startup_elapsed < 0:
            raise ValueError("Session clock must be monotonic.")
        object.__setattr__(self, "catalog_path", normalized_catalog_path)
        object.__setattr__(self, "map_path", normalized_map_path)
        object.__setattr__(self, "projected_crs", normalized_crs)
        object.__setattr__(self, "_snapshot", snapshot)
        object.__setattr__(self, "_clock", clock)
        object.__setattr__(self, "startup_elapsed_s", startup_elapsed)

    def _verify_static_inputs(self) -> None:
        current_catalog = _file_sha256(self.catalog_path)
        current_map = _file_sha256(self.map_path)
        if current_catalog != self._snapshot.catalog_sha256:
            raise ValueError("CatalogV2 changed after SearchV2Session initialization.")
        if current_map != self._snapshot.map_sha256:
            raise ValueError("MapV2 changed after SearchV2Session initialization.")

    def run_request(
        self,
        measurements_path: str | Path,
        config_path: str | Path,
        output_path: str | Path,
        *,
        projected_crs: str | None = None,
        batch_preflight_s: float = 0.0,
        **options: Any,
    ) -> LocalizationResult:
        """Run a full independent search while reusing only the static snapshot."""
        started = self._clock()
        if projected_crs is not None and not CRS.from_user_input(projected_crs).equals(
            CRS.from_user_input(self.projected_crs)
        ):
            raise ValueError("Request projection does not match SearchV2Session.")
        return _search_v2_request(
            measurements_path,
            self.catalog_path,
            self.map_path,
            config_path,
            self.projected_crs,
            output_path,
            _session=self,
            _clock=self._clock,
            _timing_started=started,
            _session_startup_s=self.startup_elapsed_s,
            _timing_scope="warm_session_request",
            _batch_preflight_s=batch_preflight_s,
            **options,
        )


def _search_v2_request(
    measurements_path: str | Path,
    catalog_path: str | Path,
    map_path: str | Path,
    config_path: str | Path,
    projected_crs: str,
    output_path: str | Path,
    *,
    calibration_path: str | Path | None = None,
    scope_manifest_path: str | Path | None = None,
    algorithm: str | None = None,
    spacing_m: float = 250.0,
    seed: int = 0,
    seed_policy: Literal["fixed", "per_trial_variable"] = "fixed",
    wedge_error_deg: float | None = None,
    wedge_tolerance_deg: float = 0.5,
    wedge_cell_size_m: float = 250.0,
    branch_max_regions: int = 999,
    refine: bool = True,
    refine_modes_count: int = 8,
    refine_initial_step_m: float = 100.0,
    refine_iterations: int = 50,
    telemetry_path: str | Path | None = None,
    device_ids: Sequence[int] | None = None,
    _session: SearchV2Session,
    _clock: Callable[[], float],
    _timing_started: float,
    _session_startup_s: float,
    _timing_scope: str,
    _batch_preflight_s: float,
) -> LocalizationResult:
    """Execute one request against a validated immutable static-input snapshot."""
    if Path(catalog_path).expanduser().resolve() != _session.catalog_path:
        raise ValueError("Request catalog path does not match SearchV2Session.")
    if Path(map_path).expanduser().resolve() != _session.map_path:
        raise ValueError("Request map path does not match SearchV2Session.")
    if not CRS.from_user_input(projected_crs).equals(CRS.from_user_input(_session.projected_crs)):
        raise ValueError("Request projection does not match SearchV2Session.")
    _session._verify_static_inputs()
    out = Path(output_path).expanduser().resolve()
    inputs = [
        Path(item).expanduser().resolve()
        for item in (measurements_path, catalog_path, map_path, config_path)
    ]
    if calibration_path is not None:
        inputs.append(Path(calibration_path).expanduser().resolve())
    if scope_manifest_path is not None:
        inputs.append(Path(scope_manifest_path).expanduser().resolve())
    if out in inputs:
        raise ValueError("Search output must be separate from all search inputs.")
    telemetry_target = (
        Path(telemetry_path) if telemetry_path else out.with_suffix(out.suffix + ".telemetry.json")
    )
    if telemetry_target.expanduser().resolve() in set(inputs) | {out}:
        raise ValueError("Telemetry output must be separate from search inputs and result.")

    config_bytes = Path(config_path).read_bytes()
    config = loads_search_config(config_bytes.decode("utf-8"))
    config_sha256 = hashlib.sha256(config_bytes).hexdigest()
    selected_algorithm = algorithm or config.algorithm
    if selected_algorithm != config.algorithm:
        raise ValueError("CLI algorithm must match SearchConfig.algorithm.")
    selected_devices = tuple(device_ids or ())
    if config.backend == "gpu":
        if not selected_devices:
            raise ValueError("GPU search requires one or more explicit --device-id values.")
        if any(type(device_id) is not int or device_id < 0 for device_id in selected_devices):
            raise ValueError("GPU device IDs must be nonnegative integers.")
        if len(set(selected_devices)) != len(selected_devices):
            raise ValueError("GPU device IDs must be distinct.")
    elif selected_devices:
        raise ValueError("--device-id can only be used with a GPU SearchConfig.")
    if config.bounds_xy is None:
        raise ValueError("SearchConfig.bounds_xy is required for v2 CLI search.")
    telemetry = SearchTelemetry(
        objective_version=config.objective_version,
        backend=config.backend,
        device_ids=selected_devices,
        clock=_clock,
    )
    scope_verified = False
    with telemetry.stage("load_inputs"):
        if scope_manifest_path is not None:
            _validate_scope_manifest(
                scope_manifest_path,
                catalog_path=catalog_path,
                map_path=map_path,
                projected_crs=projected_crs,
                bounds_xy=config.bounds_xy,
            )
            scope_verified = True
        condition, calibration_context = _calibration_binding(
            config=config,
            algorithm=selected_algorithm,
            projected_crs=projected_crs,
            catalog_path=catalog_path,
            map_path=map_path,
            scope_manifest_path=scope_manifest_path,
            spacing_m=spacing_m,
            seed=seed,
            seed_policy=seed_policy,
            wedge_error_deg=wedge_error_deg,
            wedge_tolerance_deg=wedge_tolerance_deg,
            wedge_cell_size_m=wedge_cell_size_m,
            branch_max_regions=branch_max_regions,
            refine=refine,
            refine_modes_count=refine_modes_count,
            refine_initial_step_m=refine_initial_step_m,
            refine_iterations=refine_iterations,
            device_ids=selected_devices,
        )
        if (
            calibration_context.catalog_sha256 != _session._snapshot.catalog_sha256
            or calibration_context.map_sha256 != _session._snapshot.map_sha256
        ):
            raise ValueError(
                "Static CatalogV2 or MapV2 changed while request context was being bound."
            )
        measurement_bytes = Path(measurements_path).read_bytes()
        measurements = loads_contract(measurement_bytes.decode("utf-8"), MeasurementSetV2)
        measurement_sha256 = hashlib.sha256(measurement_bytes).hexdigest()
    catalog = _session._snapshot.catalog
    map_input = _session._snapshot.map_input
    vector_map = _session._snapshot.vector_map
    if config.backend == "gpu":
        from bearing_localization.search.gpu.search_adapter import GpuCandidateScorer

        scorer = GpuCandidateScorer(
            measurements,
            catalog,
            vector_map,
            config,
            device_ids=selected_devices,
            telemetry=telemetry,
        )
    else:
        scorer = CandidateScorer(measurements, catalog, vector_map, config)
    telemetry.record_memory()
    with telemetry.stage("candidate_search"):
        result, diagnostics, candidate_count, coverage_complete = _search_dispatch(
            scorer,
            algorithm=selected_algorithm,
            spacing_m=spacing_m,
            seed=seed,
            wedge_error_deg=wedge_error_deg,
            wedge_tolerance_deg=wedge_tolerance_deg,
            wedge_cell_size_m=wedge_cell_size_m,
            branch_max_regions=branch_max_regions,
        )
    diagnostics["algorithm"] = selected_algorithm
    diagnostics["projected_crs"] = str(vector_map.crs)
    diagnostics["map_coverage_status"] = map_input.coverage_status
    diagnostics["declared_bounds_xy_m"] = list(config.bounds_xy)
    telemetry.record_candidates(
        generated=candidate_count, evaluated=candidate_count, retained=len(result.hypotheses)
    )
    candidate_result = result
    mode_evidence: tuple[SearchModeEvidence, ...] = ()
    if refine and result.hypotheses:
        with telemetry.stage("continuous_refinement"):
            seeds = []
            min_separation = max(25.0, refine_initial_step_m)
            for hypothesis in result.hypotheses:
                if all(
                    np.hypot(hypothesis.east_m - x, hypothesis.north_m - y) >= min_separation
                    for x, y in seeds
                ):
                    seeds.append((hypothesis.east_m, hypothesis.north_m))
                if len(seeds) >= refine_modes_count:
                    break
            seed_hypotheses = []
            for position in seeds:
                evaluated = scorer.evaluate(position)
                if evaluated.hypothesis is not None:
                    seed_hypotheses.append(evaluated.hypothesis)
            refined = refine_modes(
                measurements,
                catalog,
                vector_map,
                config,
                tuple(seed_hypotheses),
                initial_step_m=refine_initial_step_m,
                max_iterations=refine_iterations,
            )
            refined_hypotheses = tuple(
                mode.hypothesis for mode in refined.modes if mode.hypothesis is not None
            )
            mode_evidence = converged_mode_evidence(refined)
            merged = {
                (round(item.east_m, 8), round(item.north_m, 8)): item for item in result.hypotheses
            }
            merged.update(
                {
                    (round(item.east_m, 8), round(item.north_m, 8)): item
                    for item in refined_hypotheses
                }
            )
            ranked = tuple(sorted(merged.values(), key=lambda item: item.score))
            result = LocalizationResult(
                hypotheses=ranked,
                elapsed_s=result.elapsed_s + refined.elapsed_s,
                decision_status=result.decision_status,
                search_status=result.search_status,
                certificate_scope="none",
                budget_limit=result.budget_limit,
                budget_used=result.budget_used,
                report_count=result.report_count,
                objective_version=result.objective_version,
                bound_gap=None,
            )
            diagnostics["refinement"] = {
                "mode_count": len(refined.modes),
                "stop_reasons": [mode.stop_reason for mode in refined.modes],
            }
    outcome_counts, unresolved_count = _candidate_outcome_evidence(diagnostics)
    unresolved_region_count = diagnostics.get("unresolved_region_count", 0)
    global_bound_excludes_outside = diagnostics.get("global_bound_excludes_outside", False)
    if type(global_bound_excludes_outside) is not bool:
        global_bound_excludes_outside = False
    (
        distant_region_score_lower_bound,
        distant_region_bound_scope,
        distant_region_bound_top_east_m,
        distant_region_bound_top_north_m,
    ) = _distant_region_bound_evidence(diagnostics)
    if type(unresolved_region_count) is not int or unresolved_region_count < 0:
        unresolved_region_count = 1
    # Unknown evaluator outcomes fail closed by suppressing positive mode evidence.
    decision_modes = mode_evidence if outcome_counts is not None else ()
    top_mode_index = _top_mode_evidence_index(result, decision_modes)
    public_modes = [
        {**mode.as_dict(), "converged": True, "ellipse_rank": 2} for mode in decision_modes
    ]
    diagnostics["public_evidence"] = {
        "schema": "search-mode-evidence-v1",
        "calibration_context_schema": "calibration-context-v2",
        "calibration_condition": condition.as_dict(),
        "calibration_context": calibration_context.as_dict(),
        "search_config_sha256": config_sha256,
        "seed_policy": seed_policy,
        "seed": seed,
        "calibration_binding_status": "not_requested",
        "search_status": candidate_result.search_status,
        "scope_manifest_verified": scope_verified,
        "mode_source": "refined_local_modes" if decision_modes else "none",
        "top_mode_converged": top_mode_index is not None,
        "top_mode_index": top_mode_index,
        "mode_evidence": public_modes,
        "candidate_outcome_counts_available": outcome_counts is not None,
        "evaluated_candidate_count": candidate_count,
        "unresolved_candidate_count": unresolved_count,
        "candidate_outcome_counts": outcome_counts,
        "unresolved_region_count": unresolved_region_count,
        "coverage_complete": coverage_complete,
        "global_bound_excludes_outside": global_bound_excludes_outside,
        "distant_region_score_lower_bound": distant_region_score_lower_bound,
        "distant_region_bound_scope": distant_region_bound_scope,
        "distant_region_bound_top_east_m": distant_region_bound_top_east_m,
        "distant_region_bound_top_north_m": distant_region_bound_top_north_m,
        "certificate_scope": result.certificate_scope,
        "bound_gap": result.bound_gap,
        "candidate_certificate_scope": candidate_result.certificate_scope,
        "candidate_bound_gap": candidate_result.bound_gap,
        "raw_distant_hypothesis_veto_evidence": _raw_distant_hypothesis_evidence(result),
    }
    if calibration_path is not None and scope_verified:
        with telemetry.stage("calibrated_decision"):
            calibration = load_frozen_calibration(
                calibration_path,
                expected_condition=condition,
                expected_context=calibration_context,
                release_acceptance=True,
            )
            diagnostics["public_evidence"]["calibration_binding_status"] = "matched"
            coverage = CompetitorCoverageEvidence(
                evaluated_candidate_count=candidate_count,
                search_extent_m=0.0,
                coverage_complete=coverage_complete,
                global_bound_excludes_outside=global_bound_excludes_outside,
                unresolved_candidate_count=unresolved_count,
                candidate_outcome_counts_available=outcome_counts is not None,
                unresolved_region_count=unresolved_region_count,
                distant_region_score_lower_bound=distant_region_score_lower_bound,
                distant_region_bound_scope=distant_region_bound_scope,
                distant_region_bound_top_east_m=distant_region_bound_top_east_m,
                distant_region_bound_top_north_m=distant_region_bound_top_north_m,
            )
            result, decision = apply_accuracy_decision(
                result, calibration, coverage=coverage, mode_evidence=decision_modes
            )
            diagnostics["decision_reason"] = decision.reason
    else:
        with telemetry.stage("decision"):
            if result.decision_status == "accepted":
                result = LocalizationResult(
                    hypotheses=result.hypotheses,
                    elapsed_s=result.elapsed_s,
                    decision_status="ambiguous",
                    search_status=result.search_status,
                    certificate_scope=result.certificate_scope,
                    budget_limit=result.budget_limit,
                    budget_used=result.budget_used,
                    report_count=result.report_count,
                    objective_version=result.objective_version,
                    bound_gap=result.bound_gap,
                )
            diagnostics["public_evidence"]["calibration_binding_status"] = (
                "not_requested" if calibration_path is None else "scope_not_verified"
            )
            diagnostics["decision_reason"] = (
                "missing_full_coast_scope_manifest"
                if calibration_path is not None
                else "missing_frozen_calibration"
            )
    _session._verify_static_inputs()
    if _file_sha256(config_path) != config_sha256:
        raise ValueError("SearchConfig changed during the request.")
    if _file_sha256(measurements_path) != measurement_sha256:
        raise ValueError("MeasurementSetV2 changed during the request.")
    if (
        scope_manifest_path is not None
        and _file_sha256(scope_manifest_path) != calibration_context.scope_manifest_sha256
    ):
        raise ValueError("Scope manifest changed during the request.")
    with telemetry.stage("serialize_result"):
        out.parent.mkdir(parents=True, exist_ok=True)
        write_contract(out, result)
    request_elapsed_s = _clock() - _timing_started
    if request_elapsed_s < 0:
        raise ValueError("Request clock must be monotonic.")
    _write_json(
        telemetry_target,
        {
            "request_timing": {
                "scope": _timing_scope,
                "request_elapsed_s": request_elapsed_s,
                "session_startup_s": _session_startup_s,
                "batch_preflight_s": _batch_preflight_s,
                "batch_preflight_included": _timing_scope != "warm_session_request",
                "includes_static_session_startup": _timing_scope == "cold_one_shot",
                "result_serialization_completed": True,
                "measurement_boundary": (
                    "request_entry_through_result_file_serialization_before_sidecar_write"
                ),
                "session_reuse_policy": "catalog_map_projection_only",
            },
            "telemetry": telemetry.as_dict(),
            "diagnostics": diagnostics,
            "catalog_sha256": _session._snapshot.catalog_sha256,
            "map_sha256": _session._snapshot.map_sha256,
            "measurement_sha256": measurement_sha256,
        },
    )
    return result


def _preflight_search_config(
    config_path: str | Path,
    *,
    algorithm: str | None,
    device_ids: Sequence[int] | None,
) -> SearchConfig:
    config = read_search_config(config_path)
    if algorithm is not None and algorithm != config.algorithm:
        raise ValueError("CLI algorithm must match SearchConfig.algorithm.")
    selected_devices = tuple(device_ids or ())
    if config.backend == "gpu":
        if not selected_devices:
            raise ValueError("GPU search requires one or more explicit --device-id values.")
        if any(type(device_id) is not int or device_id < 0 for device_id in selected_devices):
            raise ValueError("GPU device IDs must be nonnegative integers.")
        if len(set(selected_devices)) != len(selected_devices):
            raise ValueError("GPU device IDs must be distinct.")
    elif selected_devices:
        raise ValueError("--device-id can only be used with a GPU SearchConfig.")
    if config.bounds_xy is None:
        raise ValueError("SearchConfig.bounds_xy is required for v2 CLI search.")
    return config


def search_v2_files(
    measurements_path: str | Path,
    catalog_path: str | Path,
    map_path: str | Path,
    config_path: str | Path,
    projected_crs: str,
    output_path: str | Path,
    *,
    calibration_path: str | Path | None = None,
    scope_manifest_path: str | Path | None = None,
    algorithm: str | None = None,
    spacing_m: float = 250.0,
    seed: int = 0,
    seed_policy: Literal["fixed", "per_trial_variable"] = "fixed",
    wedge_error_deg: float | None = None,
    wedge_tolerance_deg: float = 0.5,
    wedge_cell_size_m: float = 250.0,
    branch_max_regions: int = 999,
    refine: bool = True,
    refine_modes_count: int = 8,
    refine_initial_step_m: float = 100.0,
    refine_iterations: int = 50,
    telemetry_path: str | Path | None = None,
    device_ids: Sequence[int] | None = None,
) -> LocalizationResult:
    """Run one cold v2 request, preserving the historical file API."""
    clock = perf_counter
    started = clock()
    _preflight_search_config(config_path, algorithm=algorithm, device_ids=device_ids)
    session = SearchV2Session(catalog_path, map_path, projected_crs, clock=clock)
    return _search_v2_request(
        measurements_path,
        session.catalog_path,
        session.map_path,
        config_path,
        session.projected_crs,
        output_path,
        calibration_path=calibration_path,
        scope_manifest_path=scope_manifest_path,
        algorithm=algorithm,
        spacing_m=spacing_m,
        seed=seed,
        seed_policy=seed_policy,
        wedge_error_deg=wedge_error_deg,
        wedge_tolerance_deg=wedge_tolerance_deg,
        wedge_cell_size_m=wedge_cell_size_m,
        branch_max_regions=branch_max_regions,
        refine=refine,
        refine_modes_count=refine_modes_count,
        refine_initial_step_m=refine_initial_step_m,
        refine_iterations=refine_iterations,
        telemetry_path=telemetry_path,
        device_ids=device_ids,
        _session=session,
        _clock=clock,
        _timing_started=started,
        _session_startup_s=session.startup_elapsed_s,
        _timing_scope="cold_one_shot",
        _batch_preflight_s=0.0,
    )


def _read_jsonl_requests(path: str | Path) -> tuple[dict[str, Path], ...]:
    source = Path(path).expanduser().resolve()
    requests: list[dict[str, Path]] = []
    allowed = {"measurements", "config", "output", "telemetry_output"}
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"JSONL request line {line_number} is blank.")
        try:
            raw = json.loads(line, parse_constant=_reject_constant)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL request on line {line_number}.") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"JSONL request line {line_number} must be an object.")
        if set(raw) - allowed or not {"measurements", "config", "output"} <= set(raw):
            raise ValueError(
                f"JSONL request line {line_number} must contain measurements, config, output, "
                "and optional telemetry_output only."
            )
        if any(not isinstance(raw[key], str) or not raw[key] for key in raw):
            raise ValueError(f"JSONL request line {line_number} paths must be nonempty strings.")
        if len(requests) == 256:
            raise ValueError("JSONL batch exceeds the 256-request limit.")
        requests.append({key: (source.parent / value).resolve() for key, value in raw.items()})
    if not requests:
        raise ValueError("JSONL request file must contain at least one request.")
    for request in requests:
        request.setdefault(
            "telemetry_output",
            request["output"].with_suffix(request["output"].suffix + ".telemetry.json"),
        )
    return tuple(requests)


def _validate_jsonl_paths(
    requests: Sequence[dict[str, Path]],
    *,
    request_file: str | Path,
    static_inputs: Sequence[str | Path],
    shared_inputs: Sequence[str | Path | None],
) -> None:
    inputs = {Path(request_file).expanduser().resolve()}
    inputs.update(Path(path).expanduser().resolve() for path in static_inputs)
    inputs.update(Path(path).expanduser().resolve() for path in shared_inputs if path is not None)
    for request in requests:
        inputs.add(request["measurements"].resolve())
        inputs.add(request["config"].resolve())
    outputs = [
        path.resolve()
        for request in requests
        for path in (request["output"], request["telemetry_output"])
    ]
    if len(outputs) != len(set(outputs)):
        raise ValueError("Every JSONL request must have distinct result and telemetry outputs.")
    if set(outputs) & inputs:
        raise ValueError("JSONL outputs must be separate from every request and shared input.")


def evaluate_v2_files(
    estimate_path: str | Path,
    truth_path: str | Path,
    support_path: str | Path,
    output_path: str | Path,
    *,
    coordinate_crs: str | int | CRS | None = None,
) -> dict[str, object]:
    """Join a v2 estimate with evaluator-only truth and its support manifest."""
    paths = [
        Path(item).expanduser().resolve() for item in (estimate_path, truth_path, support_path)
    ]
    output = Path(output_path).expanduser().resolve()
    if output in paths or len(set(paths)) != len(paths):
        raise ValueError(
            "Estimate, truth, support, and evaluation outputs must use separate files."
        )
    estimate = read_contract(estimate_path, LocalizationResult)
    truth = read_contract(truth_path, EvaluationTruth)
    manifest = _read_support_manifest(support_path)
    metrics = evaluate_localization(estimate, truth, manifest, coordinate_crs=coordinate_crs)
    document = asdict(metrics)
    document["supported"] = manifest.supported
    _write_json(output, document)
    return document


def simulate_v2_files(
    fixture_path: str | Path,
    projected_crs: str,
    measurements_output: str | Path,
    truth_output: str | Path,
    diagnostics_output: str | Path,
    *,
    seed: int = 0,
    trial_id: str | None = None,
) -> dict[str, object]:
    """Generate estimator measurements and isolated evaluator truth from a JSON fixture."""
    outputs = [
        Path(item).expanduser().resolve()
        for item in (measurements_output, truth_output, diagnostics_output)
    ]
    if len(set(outputs)) != 3 or Path(fixture_path).expanduser().resolve() in outputs:
        raise ValueError("Simulator fixture and all three outputs must use separate files.")
    fixture = _load_json(fixture_path)
    allowed = {
        "catalog",
        "map",
        "observer",
        "sensor",
        "uncataloged_physical_objects",
        "mismatch",
        "timestamp_utc",
    }
    extra = set(fixture) - allowed
    if extra:
        raise ValueError(f"Unexpected simulator fixture fields: {sorted(extra)}.")
    catalog = contract_from_dict(CatalogV2, fixture["catalog"])
    map_input = contract_from_dict(MapV2, fixture["map"])
    observer = _construct_dataclass(KnownWaterObserver, fixture["observer"], name="observer")
    sensor = _construct_dataclass(SensorConfigurationV2, fixture["sensor"], name="sensor")
    uncataloged = tuple(
        _construct_dataclass(PhysicalObjectV2, item, name="uncataloged physical object")
        for item in fixture.get("uncataloged_physical_objects", [])
    )
    mismatch_payload = fixture.get("mismatch", {})
    mismatch_extra = set(mismatch_payload) - {
        "heading_bias_deg",
        "catalog_displacement_east_m",
        "catalog_displacement_north_m",
    }
    if mismatch_extra:
        raise ValueError(f"Unexpected mismatch fields: {sorted(mismatch_extra)}.")
    mismatch = SimulatorMismatchConfig(**mismatch_payload)
    vector_map = VectorMap.from_map_v2(map_input, target_crs=projected_crs)
    run = run_simulator_v2(
        PhysicalSceneV2(catalog, uncataloged),
        observer,
        vector_map,
        sensor,
        seed=seed,
        mismatch=mismatch,
        trial_id=trial_id,
        timestamp_utc=fixture.get("timestamp_utc", "2026-10-25T12:00:00Z"),
    )
    for output in outputs:
        output.parent.mkdir(parents=True, exist_ok=True)
    write_contract(measurements_output, run.measurements)
    write_contract(truth_output, run.truth)
    diagnostics = asdict(run.diagnostics)
    _write_json(diagnostics_output, diagnostics)
    return diagnostics


def _run_jsonl_searches(
    request_file: str | Path,
    catalog_path: str | Path,
    map_path: str | Path,
    projected_crs: str,
    *,
    options: dict[str, Any],
) -> tuple[LocalizationResult, ...]:
    batch_preflight_started = perf_counter()
    requests = _read_jsonl_requests(request_file)
    _validate_jsonl_paths(
        requests,
        request_file=request_file,
        static_inputs=(catalog_path, map_path),
        shared_inputs=(options.get("calibration_path"), options.get("scope_manifest_path")),
    )
    # Validate every request document before the first output is created. Each
    # request is read and validated again inside its own timed execution.
    for request in requests:
        read_contract(request["measurements"], MeasurementSetV2)
        _preflight_search_config(
            request["config"],
            algorithm=options.get("algorithm"),
            device_ids=options.get("device_ids"),
        )
    batch_preflight_s = perf_counter() - batch_preflight_started
    session = SearchV2Session(catalog_path, map_path, projected_crs)
    results = []
    for request in requests:
        results.append(
            session.run_request(
                request["measurements"],
                request["config"],
                request["output"],
                telemetry_path=request["telemetry_output"],
                batch_preflight_s=batch_preflight_s,
                **options,
            )
        )
    return tuple(results)


def search_v2_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Standalone truth-free v2 search.")
    parser.add_argument("--measurements", type=Path)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--map", dest="map_path", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--projected-crs", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--requests-jsonl",
        type=Path,
        help="Run up to 256 independent requests from JSONL using one static-input session.",
    )
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--scope-manifest", type=Path)
    parser.add_argument(
        "--algorithm",
        choices=(
            "grid",
            "hierarchical",
            "randomized",
            "pairwise",
            "reference_grid",
            "branch_bound",
            "wedge",
        ),
    )
    parser.add_argument("--spacing-m", type=float, default=250.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--seed-policy",
        choices=("fixed", "per_trial_variable"),
        default="fixed",
        help="Bind a fixed seed or declare that trials use varying seeds.",
    )
    parser.add_argument("--max-grid-bearing-error-deg", type=float)
    parser.add_argument("--wedge-tolerance-deg", type=float, default=0.5)
    parser.add_argument("--wedge-cell-size-m", type=float, default=250.0)
    parser.add_argument("--branch-max-regions", type=int, default=999)
    parser.add_argument("--no-refine", action="store_true")
    parser.add_argument("--refine-modes", type=int, default=8)
    parser.add_argument("--refine-initial-step-m", type=float, default=100.0)
    parser.add_argument("--refine-iterations", type=int, default=50)
    parser.add_argument(
        "--device-id",
        type=int,
        action="append",
        default=None,
        help="GPU device index. Repeat to use multiple GPUs; required for GPU backend.",
    )
    parser.add_argument("--telemetry-output", type=Path)
    args = parser.parse_args(argv)
    if (
        min(args.spacing_m, args.wedge_cell_size_m, args.refine_initial_step_m) <= 0
        or args.refine_modes < 1
        or args.refine_iterations < 1
        or args.branch_max_regions < 1
    ):
        parser.error("spacing, cell size, refinement, and region budgets must be positive")
    if args.device_id is not None and (
        any(device_id < 0 for device_id in args.device_id)
        or len(set(args.device_id)) != len(args.device_id)
    ):
        parser.error("device IDs must be distinct nonnegative integers")
    single_request = all(
        value is not None for value in (args.measurements, args.config, args.output)
    )
    if args.requests_jsonl is not None:
        if any(value is not None for value in (args.measurements, args.config, args.output)):
            parser.error("--requests-jsonl cannot be combined with single-request paths.")
        if args.telemetry_output is not None:
            parser.error("--telemetry-output is available only for a single request.")
    elif not single_request:
        parser.error("Provide --requests-jsonl or all of --measurements, --config, and --output.")
    try:
        options = {
            "calibration_path": args.calibration,
            "scope_manifest_path": args.scope_manifest,
            "algorithm": args.algorithm,
            "spacing_m": args.spacing_m,
            "seed": args.seed,
            "seed_policy": args.seed_policy,
            "wedge_error_deg": args.max_grid_bearing_error_deg,
            "wedge_tolerance_deg": args.wedge_tolerance_deg,
            "wedge_cell_size_m": args.wedge_cell_size_m,
            "branch_max_regions": args.branch_max_regions,
            "refine": not args.no_refine,
            "refine_modes_count": args.refine_modes,
            "refine_initial_step_m": args.refine_initial_step_m,
            "refine_iterations": args.refine_iterations,
            "device_ids": args.device_id,
        }
        if args.requests_jsonl is not None:
            results = _run_jsonl_searches(
                args.requests_jsonl,
                args.catalog,
                args.map_path,
                args.projected_crs,
                options=options,
            )
            print(json.dumps({"requests_completed": len(results)}, sort_keys=True))
            return 0
        result = search_v2_files(
            args.measurements,
            args.catalog,
            args.map_path,
            args.config,
            args.projected_crs,
            args.output,
            telemetry_path=args.telemetry_output,
            **options,
        )
    except (OSError, TypeError, ValueError, RuntimeError, KeyError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "decision_status": result.decision_status,
                "search_status": result.search_status,
                "hypotheses": len(result.hypotheses),
                "elapsed_s": result.elapsed_s,
            },
            sort_keys=True,
        )
    )
    return 0


def evaluate_v2_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Standalone v2 evaluator using hidden truth.")
    parser.add_argument("--estimate", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--support", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--coordinate-crs",
        help="Shared CRS of estimate and truth coordinates, such as EPSG:32618.",
    )
    args = parser.parse_args(argv)
    try:
        result = evaluate_v2_files(
            args.estimate, args.truth, args.support, args.output, coordinate_crs=args.coordinate_crs
        )
    except (OSError, TypeError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True))
    return 0


def simulator_v2_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Standalone v2 simulator from scene fixture JSON.")
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--projected-crs", required=True)
    parser.add_argument("--measurements-out", type=Path, required=True)
    parser.add_argument("--truth-out", type=Path, required=True)
    parser.add_argument("--diagnostics-out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--trial-id")
    args = parser.parse_args(argv)
    try:
        diagnostics = simulate_v2_files(
            args.fixture,
            args.projected_crs,
            args.measurements_out,
            args.truth_out,
            args.diagnostics_out,
            seed=args.seed,
            trial_id=args.trial_id,
        )
    except (OSError, TypeError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "measurements": str(args.measurements_out),
                "truth": str(args.truth_out),
                "detected_groups": diagnostics["detected_physical_group_count"],
            },
            sort_keys=True,
        )
    )
    return 0
