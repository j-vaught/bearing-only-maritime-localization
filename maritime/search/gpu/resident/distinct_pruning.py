"""Conservative whole-cell incidence and injective physical-marker proofs.

The graph is an overapproximation of positive physical reports. Unknown-kind
objects are possibilities; vessels are excluded. Missing observations carry no
negative evidence. A detected marker originates at a physical representative's
true bearing plus bounded error, rather than anywhere in a nominal group's
angular extent. All numerical queries, graph construction and matching execute
on the resident device. There is no host computation fallback.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def _validate_device_array(resident: Any, value: Any, name: str, dtype: Any) -> None:
    cp = resident.cp
    if cp.cuda.runtime.getDevice() != resident.device:
        raise ValueError("Distinct pruning must launch with resident.device current.")
    if not isinstance(value, cp.ndarray) or value.dtype != dtype:
        raise TypeError(f"{name} must be a device array with dtype {dtype}.")
    if not value.flags.c_contiguous:
        raise ValueError(f"{name} must be C-contiguous for CUDA flat indexing.")
    if value.device.id != resident.device:
        raise ValueError(f"{name} must reside on resident.device.")


def _validate_inputs(resident: Any, cells: Any, bearings: Any, tolerances: Any) -> None:
    for name, value in (("cells", cells), ("bearings", bearings), ("tolerances", tolerances)):
        _validate_device_array(resident, value, name, resident.cp.float64)
    if cells.ndim != 2 or cells.shape[1] != 4:
        raise ValueError("cells must have shape (batch, 4).")
    if bearings.ndim != 1 or bearings.shape != tolerances.shape:
        raise ValueError("Aligned bearing/tolerance vectors are required.")


def _validate_graph(resident: Any, graph: dict[str, Any]) -> tuple[int, int, int]:
    cp = resident.cp
    for name, dtype in (
        ("incidence", cp.uint32),
        ("object_ids", cp.int32),
        ("counts", cp.int32),
        ("flags", cp.int32),
        ("base_states", cp.int32),
    ):
        if name not in graph:
            raise ValueError(f"Graph is missing {name}.")
        _validate_device_array(resident, graph[name], name, dtype)
    incidence = graph["incidence"]
    if incidence.ndim != 3:
        raise ValueError("incidence must have shape (batch, capacity, report_words).")
    batch, capacity, words = incidence.shape
    reports = graph["reports"]
    if type(reports) is not int or reports < 0 or words != (reports + 31) // 32:
        raise ValueError("reports must align with the incidence report-word axis.")
    if graph["object_ids"].shape != (batch, capacity):
        raise ValueError("object_ids must align with incidence batch and capacity.")
    for name in ("counts", "flags", "base_states"):
        if graph[name].shape != (batch,):
            raise ValueError(f"{name} must have shape (batch,).")
    return batch, capacity, words


def _empty_graph(resident: Any, batch: int, reports: int, words: int, flag: int) -> dict[str, Any]:
    cp = resident.cp
    return dict(
        incidence=cp.empty((batch, 0, words), dtype=cp.uint32),
        object_ids=cp.empty((batch, 0), dtype=cp.int32),
        counts=cp.zeros(batch, dtype=cp.int32),
        flags=cp.full(batch, flag, dtype=cp.int32),
        base_states=cp.zeros(batch, dtype=cp.int32),
        identity_proofs=bool(
            getattr(resident, "marker_identities_complete", resident.world.identities_complete)
        ),
        reports=reports,
        workspace_bytes=0,
    )


def build_distinct_chart_graph(
    resident: Any,
    cells: Any,
    bearings: Any,
    tolerances: Any,
    *,
    true_bearings: bool = True,
) -> dict[str, Any]:
    """Return GPU object-major report incidence and conservative proof metadata.

    ``incidence[B, capacity, ceil(R/32)]`` stores report bits per possible object.
    ``counts`` and ``object_ids`` describe actual columns. ``flags`` means
    1=missing/unsupported proof, 2=capacity/workspace, 4=numerical/tree failure.
    Any nonzero flag retains the region. ``base_states`` preserves independently
    certified whole-cell land rejection. A graph with zero columns is valid only
    when flags are zero; no bounded workspace is silently truncated.

    Input arrays must be C-contiguous float64 arrays on resident.device, which
    must also be the current CUDA device. Invalid layout or device context raises
    ValueError before kernel launch; no implicit cross-device copy is performed.
    Input world rows must be the existing exact-coordinate canonical objects.
    Unknown positions disable the proof, and finite uncertain target positions
    cannot be discarded using a nominal blocked ray. The only land rejection
    for an object is the existing certified universal cell-to-target barrier.
    """
    from .chart_pruning import module_for

    _validate_inputs(resident, cells, bearings, tolerances)
    cp, world, config = resident.cp, resident.world, resident.config
    if true_bearings and world.crs != "EPSG:32618":
        raise ValueError("True-bearing bounds are restricted to EPSG:32618.")
    batch, reports = len(cells), len(bearings)
    words = (reports + 31) // 32
    if reports > 256:
        return _empty_graph(resident, batch, reports, words, 2)
    capacity = min(config.object_capacity, len(world.objects))
    # Includes graph plus all matching scratch/output arrays, not only incidence.
    workspace = batch * (capacity * (4 * words + 12) + 4 * reports + 12 * words + 40)
    if workspace > config.device_workspace_budget_bytes:
        return _empty_graph(resident, batch, reports, words, 2)
    try:
        graph = dict(
            incidence=cp.empty((batch, capacity, words), dtype=cp.uint32),
            object_ids=cp.empty((batch, capacity), dtype=cp.int32),
            counts=cp.empty(batch, dtype=cp.int32),
            flags=cp.empty(batch, dtype=cp.int32),
            base_states=cp.empty(batch, dtype=cp.int32),
            identity_proofs=bool(
                getattr(resident, "marker_identities_complete", world.identities_complete)
            ),
            reports=reports,
            workspace_bytes=workspace,
        )
    except MemoryError:
        return _empty_graph(resident, batch, reports, words, 2)
    if not batch:
        return graph
    module = module_for(resident, true_bearings)
    module.get_function("gather_distinct_chart_objects")(
        (batch,),
        (32,),
        (
            cells,
            np.int32(batch),
            *resident.map_args,
            *resident.object_args,
            np.int32(len(world.objects)),
            bearings,
            tolerances,
            np.int32(reports),
            resident.barriers,
            np.int32(len(world.barriers)),
            np.float64(config.radius_m),
            np.float64(config.range_numerical_margin_m),
            np.float64(config.bearing_numerical_margin_deg),
            np.float64(world.max_projection_scale or 1),
            np.float64(world.azimuth_error_deg or 0),
            np.float64(resident.max_uncertainty),
            np.int32(
                resident.marker_proofs
                and getattr(
                    resident, "chart_position_support_complete", world.position_support_complete
                )
            ),
            np.int32(capacity),
            graph["object_ids"],
            graph["counts"],
            graph["flags"],
            graph["base_states"],
        ),
    )
    module.get_function("build_distinct_chart_incidence")(
        (batch,),
        (128,),
        (
            cells,
            np.int32(batch),
            resident.object_args[2],
            graph["object_ids"],
            graph["counts"],
            graph["flags"],
            graph["base_states"],
            bearings,
            tolerances,
            np.int32(reports),
            np.float64(config.radius_m),
            np.float64(config.range_numerical_margin_m),
            np.float64(config.bearing_numerical_margin_deg),
            np.float64(world.max_projection_scale or 1),
            np.float64(world.azimuth_error_deg or 0),
            np.int32(capacity),
            np.int32(words),
            graph["incidence"],
        ),
    )
    return graph


def solve_distinct_chart_graph(
    resident: Any, graph: dict[str, Any], *, true_bearings: bool = True
) -> dict[str, Any]:
    """Find a possible injection on GPU; no injection certifies impossibility.

    ``feasible`` is 1=possible, 0=proved impossible, -1=unsupported/retain.
    ``reasons`` is 2=an individually unsupported report, 3=too few objects,
    4=Hall deficiency. Missing identity certificates disable reasons 3 and 4,
    preserving the existing safe individual-support proof. ``proof_valid``
    explicitly excludes all workspace, numerical and certificate failures.
    matched_counts counts the prefix of successfully augmented reports, not a
    maximum partial cardinality when an earlier report has no possible match.
    Graph arrays must have the builder's dtypes/shapes, be C-contiguous, and
    reside on the current resident device. Invalid counts are flagged on GPU
    before the matching kernel can index any column.
    """
    from .chart_pruning import module_for

    cp = resident.cp
    batch, capacity, words = _validate_graph(resident, graph)
    reports = graph["reports"]
    flags = graph["flags"].copy()
    result = dict(
        feasible=cp.full(batch, -1, dtype=cp.int32),
        matched_counts=cp.zeros(batch, dtype=cp.int32),
        reasons=cp.zeros(batch, dtype=cp.int32),
        flags=flags,
    )
    required_workspace = batch * (capacity * (4 * words + 12) + 4 * reports + 12 * words + 40)
    if (
        reports > 256
        or (batch and graph["workspace_bytes"] == 0)
        or required_workspace > resident.config.device_workspace_budget_bytes
    ):
        flags |= np.int32(2)
    elif batch:
        try:
            owners, parents = (cp.empty((batch, capacity), dtype=cp.int32) for _ in range(2))
            row_matches = cp.empty((batch, reports), dtype=cp.int32)
            frontiers = cp.empty((batch, 3, words), dtype=cp.uint32)
        except MemoryError:
            flags |= np.int32(2)
        else:
            module = module_for(resident, true_bearings)
            module.get_function("validate_distinct_chart_counts")(
                ((batch + 127) // 128,),
                (128,),
                (graph["counts"], np.int32(batch), np.int32(capacity), flags),
            )
            module.get_function("match_distinct_chart_incidence")(
                (batch,),
                (32,),
                (
                    graph["incidence"],
                    graph["counts"],
                    flags,
                    graph["base_states"],
                    np.int32(batch),
                    np.int32(reports),
                    np.int32(capacity),
                    np.int32(words),
                    np.int32(graph["identity_proofs"]),
                    owners,
                    parents,
                    row_matches,
                    frontiers,
                    result["feasible"],
                    result["matched_counts"],
                    result["reasons"],
                    flags,
                ),
            )
    result["proof_valid"] = (flags == 0) & (graph["base_states"] == 0)
    return result


def prune_distinct_chart_geometry(
    resident: Any,
    cells: Any,
    bearings: Any,
    tolerances: Any,
    *,
    true_bearings: bool = True,
    return_details: bool = False,
) -> Any:
    """Emit 0=retain, 1=land, 2=individual, 3=count, 4=Hall proof on GPU."""
    graph = build_distinct_chart_graph(
        resident, cells, bearings, tolerances, true_bearings=true_bearings
    )
    solved = solve_distinct_chart_graph(resident, graph, true_bearings=true_bearings)
    state = resident.cp.where(
        graph["base_states"] != 0,
        graph["base_states"],
        resident.cp.where(solved["proof_valid"] & (solved["feasible"] == 0), solved["reasons"], 0),
    ).astype(resident.cp.int32)
    if return_details:
        return dict(states=state, graph=graph, **solved)
    return state
