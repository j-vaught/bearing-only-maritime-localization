"""Opt-in chart geometry pruning independent of shoreline coverage.

All baseline source files, prepared arrays and cache identity stay unchanged.
The true-bearing variant uses a reviewed whole-cell geodesic envelope rather
than a fixed projected-to-true bearing allowance. Unsupported cases retain
all angular possibilities.
"""

from pathlib import Path
from typing import Any

import numpy as np

from .distinct_pruning import (
    build_distinct_chart_graph,
    prune_distinct_chart_geometry,
    solve_distinct_chart_graph,
)


def _source(true_bearings: bool = True) -> str:
    source = Path(__file__).with_name("kernels.cu").read_text()
    guard = "if(!known||!marker_proofs||nd==0||nd>256)return;"
    envelope_guard = """    if(!covers_box(m,1,c[0]-margin,c[1]-margin,c[2]+margin,c[3]+margin)||
       boundary_box(m,2,c[0]-margin,c[1]-margin,c[2]+margin,c[3]+margin)||
       inside(m,2,(c[0]+c[2])*.5,(c[1]+c[3])*.5)!=0)return;"""
    if source.count(guard) != 1 or source.count(envelope_guard) != 1:
        raise RuntimeError("Baseline pruning changed; review the independent chart guards.")
    source = source.replace(guard, "if(!marker_proofs||nd==0||nd>256)return;")
    source = source.replace(envelope_guard, "")
    source = source.replace("void prune_cells(", "void prune_chart_geometry(")
    if true_bearings:
        old = """        ++candidates;double dist=hypot(o[0]-cx,o[1]-cy),rad=cr+unc;
        double angle=fmod(atan2(o[0]-cx,o[1]-cy)*DEG+360.,360.);
        double expand=dist<=rad?180.:asin(fmin(1.,rad/dist))*DEG;
        for(int d=0;d<nd;++d)if(angular(angle,bearings[d])<=tolerances[d]+expand+azimuth+o[5]+bearing_error)possible[d]=true;"""  # noqa: E501
        new = """        double angle=0.,expand=180.,range_lower=0.;
        if(true_bearing_envelope(c,o,range_error,bearing_error,&angle,&expand,&range_lower)){
            if(range_lower>radius+range_error)continue;
        }else{angle=0.;expand=180.;range_lower=0.;}
        ++candidates;
        for(int d=0;d<nd;++d)if(angular(angle,bearings[d])<=tolerances[d]+expand+o[5]+bearing_error)possible[d]=true;"""  # noqa: E501
        if source.count(old) != 1:
            raise RuntimeError(
                "Baseline angular predicate changed; review the true-bearing substitution."
            )
        source = source.replace(old, new)
        at = source.index('extern "C" __global__ void prune_chart_geometry')
        helper = Path(__file__).with_name("bearing_bounds.cu").read_text()
        source = source[:at] + helper + "\n" + source[at:]
    distinct_source = Path(__file__).with_name("distinct_pruning.cu").read_text()
    source += f"\n#define DISTINCT_TRUE_BEARINGS {int(true_bearings)}\n{distinct_source}"
    return source


def module_for(resident: Any, true_bearings: bool = True) -> Any:
    modules = getattr(resident, "_independent_chart_modules", None)
    if modules is None:
        modules = {}
        resident._independent_chart_modules = modules
    if true_bearings not in modules:
        modules[true_bearings] = resident.cp.RawModule(
            code=_source(true_bearings),
            options=("--std=c++11",),
        )
    return modules[true_bearings]


def prune_chart_geometry(
    resident: Any,
    cells: Any,
    bearings: Any,
    tolerances: Any,
    *,
    true_bearings: bool = True,
    distinct_markers: bool = True,
) -> Any:
    """Prove whole-cell physical impossibility, including distinct-marker matching.

    The graph path requires a possible injective assignment of positive physical
    reports to canonical objects. Unsupported bounds and exhausted workspaces
    retain the cell. Set distinct_markers=False for the reviewed individual-only
    path. Missing detections never create pruning evidence.
    """
    if distinct_markers:
        return prune_distinct_chart_geometry(
            resident, cells, bearings, tolerances, true_bearings=true_bearings
        )
    cp, world, config = resident.cp, resident.world, resident.config
    if true_bearings and world.crs != "EPSG:32618":
        raise ValueError("True-bearing bounds are restricted to EPSG:32618.")
    state = cp.empty(len(cells), dtype=cp.int32)
    if len(cells):
        module_for(resident, true_bearings).get_function("prune_chart_geometry")(
            ((len(cells) + 127) // 128,),
            (128,),
            (
                cells,
                np.int32(len(cells)),
                *resident.map_args,
                *resident.object_args,
                bearings,
                tolerances,
                np.int32(len(bearings)),
                resident.barriers,
                np.int32(len(world.barriers)),
                np.float64(config.radius_m),
                np.float64(config.range_numerical_margin_m),
                np.float64(config.bearing_numerical_margin_deg),
                np.float64(world.max_projection_scale or 1),
                np.float64(world.azimuth_error_deg or 0),
                np.float64(resident.max_uncertainty),
                np.int32(resident.marker_proofs),
                np.int32(world.identities_complete),
                state,
            ),
        )
    return state


__all__ = [
    "build_distinct_chart_graph",
    "module_for",
    "prune_chart_geometry",
    "prune_distinct_chart_geometry",
    "solve_distinct_chart_graph",
]
