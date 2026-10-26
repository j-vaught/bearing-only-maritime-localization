"""Version-two standalone command helpers for simulation, search, and evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any, cast

import numpy as np
from pyproj import CRS, Transformer

from bearing_localization.evaluation.metrics import (
    SupportManifest,
    evaluate_localization,
)
from bearing_localization.search.bounds import BoundsResult
from bearing_localization.search.branch_bound import BranchBoundContext, branch_and_bound_search
from bearing_localization.search.calibration import load_frozen_calibration
from bearing_localization.search.config_serialization import read_search_config
from bearing_localization.search.decision import CompetitorCoverageEvidence, apply_accuracy_decision
from bearing_localization.search.grid import run_grid_search_v2
from bearing_localization.search.hierarchical import run_hierarchical_search_v2
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
    read_contract,
    write_contract,
)


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
            run = score_candidate_positions_batched(
                scorer,
                enumerate_grid_points(config.bounds_xy, spacing_m),
                candidate_limit=config.candidate_budget,
                certificate_scope="finite_candidate_set",
            )
        else:
            run = run_grid_search_v2(scorer, grid_spacing_m=spacing_m)
        return (
            run.localization,
            {"candidate_count": run.candidate_count, "outcome_counts": dict(run.outcome_counts)},
            run.candidate_count,
            run.localization.certificate_scope == "finite_candidate_set",
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
            complete = localization.certificate_scope == "finite_candidate_set"
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
        complete = run.localization_result.certificate_scope == "finite_candidate_set"
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
        return run.localization, run.diagnostic_dict(), run.candidates_evaluated, False
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
    """Execute one truth-free v2 search and write result plus telemetry sidecar."""
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

    config = read_search_config(config_path)
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
    scope_verified = False
    if scope_manifest_path is not None:
        _validate_scope_manifest(
            scope_manifest_path,
            catalog_path=catalog_path,
            map_path=map_path,
            projected_crs=projected_crs,
            bounds_xy=config.bounds_xy,
        )
        scope_verified = True

    telemetry = SearchTelemetry(
        objective_version=config.objective_version,
        backend=config.backend,
        device_ids=selected_devices,
    )
    with telemetry.stage("load_inputs"):
        measurements = read_contract(measurements_path, MeasurementSetV2)
        catalog = read_contract(catalog_path, CatalogV2)
        map_input = read_contract(map_path, MapV2)
    with telemetry.stage("project_map"):
        vector_map = VectorMap.from_map_v2(map_input, target_crs=projected_crs)
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
    if calibration_path is not None and scope_verified:
        with telemetry.stage("calibrated_decision"):
            calibration = load_frozen_calibration(calibration_path)
            bounds = config.bounds_xy
            assert bounds is not None
            extent = float(np.hypot(bounds[1] - bounds[0], bounds[3] - bounds[2]))
            unresolved = int(diagnostics.get("unresolved_coverage", 0)) + int(
                diagnostics.get("unresolved_model", 0)
            )
            coverage = CompetitorCoverageEvidence(
                evaluated_candidate_count=candidate_count,
                search_extent_m=extent,
                coverage_complete=coverage_complete and unresolved == 0,
                global_bound_excludes_outside=result.certificate_scope == "continuous_objective",
            )
            result, _decision = apply_accuracy_decision(result, calibration, coverage=coverage)
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
            diagnostics["decision_reason"] = (
                "missing_full_coast_scope_manifest"
                if calibration_path is not None
                else "missing_frozen_calibration"
            )
    with telemetry.stage("serialize_result"):
        out.parent.mkdir(parents=True, exist_ok=True)
        write_contract(out, result)
    _write_json(
        telemetry_target,
        {
            "telemetry": telemetry.as_dict(),
            "diagnostics": diagnostics,
            "catalog_sha256": hashlib.sha256(Path(catalog_path).read_bytes()).hexdigest(),
            "map_sha256": hashlib.sha256(Path(map_path).read_bytes()).hexdigest(),
            "measurement_sha256": hashlib.sha256(Path(measurements_path).read_bytes()).hexdigest(),
        },
    )
    return result


def evaluate_v2_files(
    estimate_path: str | Path,
    truth_path: str | Path,
    support_path: str | Path,
    output_path: str | Path,
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
    manifest = _construct_dataclass(
        SupportManifest, _load_json(support_path), name="SupportManifest"
    )
    metrics = evaluate_localization(estimate, truth, manifest)
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


def search_v2_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Standalone truth-free v2 search.")
    parser.add_argument("--measurements", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--map", dest="map_path", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--projected-crs", required=True)
    parser.add_argument("--output", type=Path, required=True)
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
    try:
        result = search_v2_files(
            args.measurements,
            args.catalog,
            args.map_path,
            args.config,
            args.projected_crs,
            args.output,
            calibration_path=args.calibration,
            scope_manifest_path=args.scope_manifest,
            algorithm=args.algorithm,
            spacing_m=args.spacing_m,
            seed=args.seed,
            wedge_error_deg=args.max_grid_bearing_error_deg,
            wedge_tolerance_deg=args.wedge_tolerance_deg,
            wedge_cell_size_m=args.wedge_cell_size_m,
            branch_max_regions=args.branch_max_regions,
            refine=not args.no_refine,
            refine_modes_count=args.refine_modes,
            refine_initial_step_m=args.refine_initial_step_m,
            refine_iterations=args.refine_iterations,
            telemetry_path=args.telemetry_output,
            device_ids=args.device_id,
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
    args = parser.parse_args(argv)
    try:
        result = evaluate_v2_files(args.estimate, args.truth, args.support, args.output)
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
