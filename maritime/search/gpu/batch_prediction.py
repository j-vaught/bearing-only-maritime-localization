"""Batched CuPy candidate prediction with WGS84 Vincenty inverse."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from importlib import import_module
from math import isfinite
from typing import Any, Literal

import numpy as np
from numpy.typing import ArrayLike

from maritime.geometry.angular_grouping import DetectedObject, group_angular_detections
from maritime.schemas.object_types import normalize_object_type
from maritime.search.prediction import (
    PredictedGroupSet,
    PredictedVisibleGroup,
    _circular_separation,
)

from .geodesic import _inverse


@dataclass(frozen=True, slots=True)
class BatchedPredictionResult:
    candidate_ids: tuple[str | int, ...]
    object_keys: tuple[str, ...]
    ranges_m: Any
    bearings_deg: Any
    converged: Any
    in_range: Any
    visibility_state: Any
    unresolved: Any
    groups: tuple[PredictedGroupSet, ...]


def predict_candidates_cupy(
    candidate_lonlat_deg: ArrayLike,
    catalog_lonlat_deg: ArrayLike,
    *,
    candidate_ids: Sequence[str | int],
    object_keys: Sequence[str],
    object_types: Sequence[str | int | None],
    visibility_state: ArrayLike,
    visibility_radius_m: float,
    minimum_range_m: float = 0.0,
    range_inclusive: bool = True,
    angular_resolution_deg: float = 0.1,
    type_labels_enabled: bool = True,
    angular_half_width_deg: ArrayLike | None = None,
    catalog_uncertainty_m: ArrayLike | None = None,
    observer_status: Sequence[str] | None = None,
    unsupported_keys: Sequence[str] = (),
    identity_limited_keys: Sequence[str] = (),
    offset_quality: Sequence[Literal["calibrated", "nominal_zero_unknown", "unsupported"]]
    | None = None,
    cupy_module: Any | None = None,
) -> BatchedPredictionResult:
    """Inputs are CPU lon-lat degrees; map LOS is caller-prepared: 0 blocked, 1 visible, 2 unknown.
    Visibility codes may be NumPy or CuPy arrays. Geodesic metadata and grouping are host-side;
    pair arrays stay on GPU. Geodesic failures are unresolved, never approximated.
    Group objects use the shared CPU circular grouping contract. Preapply east/north offsets
    to catalog coordinates. Pass offset quality and unsupported/identity-limited keys explicitly.
    """
    if (
        not isfinite(visibility_radius_m)
        or visibility_radius_m < 0
        or not isfinite(minimum_range_m)
        or not 0 <= minimum_range_m <= visibility_radius_m
    ):
        raise ValueError("Invalid prediction range.")
    if (
        type(range_inclusive) is not bool
        or type(type_labels_enabled) is not bool
        or not isfinite(angular_resolution_deg)
        or not 0 <= angular_resolution_deg < 180
    ):
        raise ValueError("Invalid grouping or range options.")
    c, t = _pts(candidate_lonlat_deg), _pts(catalog_lonlat_deg)
    n, m = len(c), len(t)
    ids, keys = tuple(candidate_ids), tuple(object_keys)
    if (
        len(ids) != n
        or len(set(ids)) != n
        or len(keys) != m
        or len(set(keys)) != m
        or len(object_types) != m
    ):
        raise ValueError("IDs and types must uniquely align with input rows.")
    cp = cupy_module or import_module("cupy")
    v = cp.asarray(visibility_state, dtype=cp.uint8)
    if v.shape != (n, m) or not bool(cp.all((v == 0) | (v == 1) | (v == 2) | (v == 3))):
        raise ValueError("visibility_state must be (N,M) coded 0/1/2/3.")
    status = tuple(observer_status) if observer_status is not None else ("water",) * n
    if len(status) != n or any(x not in ("water", "land", "unknown") for x in status):
        raise ValueError("Invalid observer_status.")
    widths = _vec(angular_half_width_deg, m, 0.0)
    unc = _vec(catalog_uncertainty_m, m, np.nan)
    unsupported = set(unsupported_keys)
    if not unsupported.issubset(keys):
        raise ValueError("unsupported_keys not in catalog.")
    identity_limited = set(identity_limited_keys)
    if not identity_limited.issubset(keys):
        raise ValueError("identity_limited_keys not in catalog.")
    qualities = (
        tuple(offset_quality) if offset_quality is not None else ("nominal_zero_unknown",) * m
    )
    allowed_quality = {"calibrated", "nominal_zero_unknown", "unsupported"}
    if len(qualities) != m or any(q not in allowed_quality for q in qualities):
        raise ValueError("offset_quality must align with catalog and use supported values.")
    unsupported.update(keys[j] for j, quality in enumerate(qualities) if quality == "unsupported")
    cc, tt = cp.asarray(c), cp.asarray(t)
    lo1, la1 = cp.deg2rad(cc[:, 0])[:, None], cp.deg2rad(cc[:, 1])[:, None]
    lo2, la2 = cp.deg2rad(tt[:, 0])[None, :], cp.deg2rad(tt[:, 1])[None, :]
    # Shared GPU inverse kernel accepts broadcast arrays and returns distance, azimuth, convergence.
    azi, dist, valid = _inverse(
        cp, cc[:, 0][:, None], cc[:, 1][:, None], tt[:, 0][None, :], tt[:, 1][None, :]
    )
    ok = _convergence(cp, lo1, la1, lo2, la2) & valid
    bear = cp.mod(azi, 360.0)
    same = (cc[:, None, 0] == tt[None, :, 0]) & (cc[:, None, 1] == tt[None, :, 1])
    ok = ok | same
    dist = cp.where(same, 0.0, dist)
    bear = cp.where(same, cp.nan, bear)
    inside = (
        ((dist >= minimum_range_m) & (dist <= visibility_radius_m))
        if range_inclusive
        else ((dist > minimum_range_m) & (dist < visibility_radius_m))
    )
    vis = v
    if bool(cp.any((vis == 3) & inside)):
        raise ValueError("visibility_state 3 is allowed only for out-of-range pairs.")
    bad = (
        (~ok)
        | ((vis == 2) & inside)
        | (cp.asarray([s == "unknown" for s in status])[:, None] & inside)
        | (cp.asarray([k in unsupported for k in keys])[None, :] & inside)
    )
    dh, bh, oh, ih, vh, bhbad = map(cp.asnumpy, (dist, bear, ok, inside, vis, bad))
    out = []
    for i in range(n):
        js = (
            []
            if status[i] == "land"
            else [
                j
                for j in range(m)
                if oh[i, j] and ih[i, j] and vh[i, j] == 1 and keys[j] not in unsupported
            ]
        )
        ds = [
            DetectedObject(
                keys[j],
                normalize_object_type(object_types[j], type_labels_enabled=type_labels_enabled)
                or "unknown",
                float(bh[i, j]) if np.isfinite(bh[i, j]) else 0.0,
                float(dh[i, j]),
                0.0,
                float(widths[j]),
            )
            for j in js
        ]
        gg = group_angular_detections(
            ds, angular_resolution_deg, report_type_labels=type_labels_enabled
        )
        bd = {x.object_id: x for x in ds}
        ix = {keys[j]: j for j in js}
        groups = []
        for mem in gg.truth_groups:
            rep = min(mem, key=lambda k: (bd[k].range_m, k))
            ext = max(
                _circular_separation(bd[rep].bearing_deg, bd[k].bearing_deg) + widths[ix[k]]
                for k in mem
            )
            uu = [unc[ix[k]] for k in mem if np.isfinite(unc[ix[k]])]
            groups.append(
                PredictedVisibleGroup(
                    bd[rep].bearing_deg % 360,
                    bd[rep].object_type if type_labels_enabled else None,
                    mem,
                    rep,
                    bd[rep].range_m,
                    ext,
                    max(uu) if uu else None,
                    qualities[ix[rep]] == "calibrated",
                    qualities[ix[rep]],
                )
            )
        unknown = tuple(sorted(keys[j] for j in range(m) if bhbad[i, j]))
        js_keys = {keys[j] for j in js if vh[i, j] == 1}
        out.append(
            PredictedGroupSet(
                tuple(groups),
                "unresolved" if unknown or status[i] == "unknown" else "known",
                unknown,
                bool(set(unknown) & unsupported),
                tuple(sorted(set(unknown) & unsupported)),
                tuple(sorted(k for k in identity_limited if k in js_keys)),
            )
        )
    return BatchedPredictionResult(ids, keys, dist, bear, ok, inside, vis, bad, tuple(out))


def _convergence(cp, lo1, la1, lo2, la2):
    from maritime.search.gpu.geodesic import _F

    u1, u2 = cp.arctan((1 - _F) * cp.tan(la1)), cp.arctan((1 - _F) * cp.tan(la2))
    su1, cu1, su2, cu2 = cp.sin(u1), cp.cos(u1), cp.sin(u2), cp.cos(u2)
    L = cp.deg2rad((cp.rad2deg(lo2 - lo1) + 180) % 360 - 180)
    lam = cp.broadcast_to(L, (lo1.shape[0], lo2.shape[1])).copy()
    done = cp.zeros_like(lam, dtype=cp.bool_)
    for _ in range(200):
        sl, cl = cp.sin(lam), cp.cos(lam)
        ss = cp.sqrt((cu2 * sl) ** 2 + (cu1 * su2 - su1 * cu2 * cl) ** 2)
        cs = su1 * su2 + cu1 * cu2 * cl
        sig = cp.arctan2(ss, cs)
        sa = cp.where(ss != 0, (cu1 * cu2 * sl) / cp.where(ss != 0, ss, 1.0), 0.0)
        c2 = 1 - sa**2
        c2m = cp.where(c2 > 1e-30, cs - 2 * su1 * su2 / cp.maximum(c2, 1e-30), 0)
        C = _F / 16 * c2 * (4 + _F * (4 - 3 * c2))
        nxt = L + (1 - C) * _F * sa * (sig + C * ss * (c2m + C * cs * (-1 + 2 * c2m**2)))
        delta = cp.abs(nxt - lam)
        done |= delta <= 1e-12
        lam = cp.where(done, lam, nxt)
        if bool(cp.all(done)):
            break
    return done


def _pts(x):
    a = np.asarray(x, dtype=float)
    if (
        a.ndim != 2
        or a.shape[1] != 2
        or not a.size
        or not np.isfinite(a).all()
        or np.any(abs(a[:, 0]) > 180)
        or np.any(abs(a[:, 1]) > 90)
    ):
        raise ValueError("Coordinates must be finite lon-lat (N,2).")
    return a


def _vec(x, n, d):
    a = np.full(n, d) if x is None else np.asarray(x, dtype=float)
    if a.shape != (n,) or np.any(np.isinf(a)) or np.any(a[np.isfinite(a)] < 0):
        raise ValueError("Metadata arrays must be aligned and nonnegative.")
    return a


__all__ = ["BatchedPredictionResult", "predict_candidates_cupy"]
