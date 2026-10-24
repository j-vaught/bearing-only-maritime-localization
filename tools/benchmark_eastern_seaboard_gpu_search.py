"""Benchmark GPU coarse and exhaustive bearing search on the full East Coast catalog.

The GPU score is an independent nearest-compatible-object surrogate used to rank
candidates in parallel. The best 100 candidates are reranked with the project's
exact one-to-one Hungarian association, so surrogate quality is measured rather
than assumed. This is a search-stage benchmark; water and land visibility are
not available coast-wide.
"""

from __future__ import annotations

import argparse
import csv
import math
import platform
import runpy
import time
from importlib import import_module
from pathlib import Path
from typing import Any

import numpy as np

torch: Any = import_module("torch")
cKDTree: Any = import_module("scipy.spatial").__dict__["cKDTree"]

ROOT = Path(__file__).resolve().parents[1]
RANGE_M = 10_000.0
UNMATCHED_DEG = 12.0
SIGMA_FLOOR_DEG = 0.1
BATCH = 1024
torch_objects: Any = None
torch_types: Any = None
torch_bearings: Any = None
torch_observation_types: Any = None
torch_sigmas: Any = None


def candidates_on_grid(bounds: tuple[float, float, float, float], spacing: float) -> np.ndarray:
    xmin, xmax, ymin, ymax = bounds
    xs = np.arange(xmin, xmax + spacing * 0.5, spacing, dtype=np.float64)
    ys = np.arange(ymin, ymax + spacing * 0.5, spacing, dtype=np.float64)
    xx, yy = np.meshgrid(xs, ys, indexing="ij")
    return np.column_stack((xx.ravel(), yy.ravel()))


def pack_neighbors(tree: cKDTree, points: np.ndarray) -> tuple[np.ndarray, int]:
    neighbors = tree.query_ball_point(points, RANGE_M, workers=1)
    width = max((len(row) for row in neighbors), default=0)
    packed = np.full((len(points), width), -1, dtype=np.int32)
    for i, row in enumerate(neighbors):
        packed[i, : len(row)] = row
    return packed, width


def score_cpu(
    candidates: np.ndarray,
    packed: np.ndarray,
    objects: np.ndarray,
    object_types: np.ndarray,
    bearings: np.ndarray,
    observation_types: np.ndarray,
    sigmas: np.ndarray,
    *,
    use_types: bool,
) -> np.ndarray:
    if packed.shape[1] == 0:
        return np.full(len(candidates), float(np.sum(UNMATCHED_DEG / sigmas)))
    valid = packed >= 0
    safe = np.maximum(packed, 0)
    targets = objects[safe]
    delta = targets - candidates[:, None, :]
    predicted = np.degrees(np.arctan2(delta[:, :, 0], delta[:, :, 1]))
    result = np.zeros(len(candidates), dtype=np.float32)
    for bearing, kind, sigma in zip(bearings, observation_types, sigmas, strict=True):
        error = np.abs((predicted - bearing + 180.0) % 360.0 - 180.0) / sigma
        compatible = valid.copy()
        if use_types:
            compatible &= object_types[safe] == kind
        error[~compatible] = np.inf
        best = np.min(error, axis=1)
        result += np.where(
            np.isfinite(best), np.minimum(best, UNMATCHED_DEG / sigma), UNMATCHED_DEG / sigma
        )
    return result


def score_gpu(
    candidates: np.ndarray,
    packed: np.ndarray,
    objects: torch.Tensor,
    object_types: torch.Tensor,
    bearings: torch.Tensor,
    observation_types: torch.Tensor,
    sigmas: torch.Tensor,
    *,
    device: torch.device,
    use_types: bool,
) -> np.ndarray:
    if packed.shape[1] == 0:
        return np.full(len(candidates), float((UNMATCHED_DEG / sigmas.cpu().numpy()).sum()))
    pos = torch.as_tensor(candidates, dtype=torch.float32, device=device)
    index = torch.as_tensor(packed, dtype=torch.int64, device=device)
    valid = index >= 0
    safe = index.clamp_min(0)
    delta = objects[safe] - pos[:, None, :]
    predicted = torch.rad2deg(torch.atan2(delta[:, :, 0], delta[:, :, 1]))
    score = torch.zeros((len(candidates),), dtype=torch.float32, device=device)
    for j in range(len(bearings)):
        error = torch.abs(torch.remainder(predicted - bearings[j] + 180.0, 360.0) - 180.0)
        error = error / sigmas[j]
        compatible = valid
        if use_types:
            compatible = compatible & (object_types[safe] == observation_types[j])
        error = error.masked_fill(~compatible, float("inf"))
        best = torch.amin(error, dim=1)
        penalty = UNMATCHED_DEG / sigmas[j]
        score += torch.where(torch.isfinite(best), torch.clamp(best, max=penalty), penalty)
    return score.cpu().numpy()


def exact_rerank(
    candidate_indices: np.ndarray,
    candidates: np.ndarray,
    tree: cKDTree,
    all_records: tuple,
    measurements,
    *,
    use_types: bool,
) -> tuple[int, float]:
    from bearing_localization.search.association import associate_observations_to_ais

    best_index, best_score = int(candidate_indices[0]), float("inf")
    for index in candidate_indices:
        pos = candidates[int(index)]
        local_ids = tree.query_ball_point(pos, RANGE_M)
        local_records = tuple(all_records[i] for i in local_ids)
        result = associate_observations_to_ais(
            pos,
            measurements.observations,
            local_records,
            use_types=use_types,
            max_range_m=RANGE_M,
            unmatched_penalty_deg=UNMATCHED_DEG,
            sigma_floor_deg=SIGMA_FLOOR_DEG,
        )
        if result.cost < best_score:
            best_index, best_score = int(index), result.cost
    return best_index, best_score


def run_grid(
    candidates: np.ndarray,
    tree: cKDTree,
    xy: np.ndarray,
    kinds: np.ndarray,
    bearings: np.ndarray,
    observation_types: np.ndarray,
    sigmas: np.ndarray,
    *,
    device: torch.device,
    use_types: bool,
    mode: str,
) -> tuple[np.ndarray, float, float, int]:
    scores = np.empty(len(candidates), dtype=np.float32)
    query_s = score_s = 0.0
    max_neighbors = 0
    for lo in range(0, len(candidates), BATCH):
        points = candidates[lo : lo + BATCH]
        t = time.perf_counter()
        packed, width = pack_neighbors(tree, points)
        query_s += time.perf_counter() - t
        max_neighbors = max(max_neighbors, width)
        t = time.perf_counter()
        if mode == "gpu":
            out = score_gpu(
                points,
                packed,
                torch_objects,
                torch_types,
                torch_bearings,
                torch_observation_types,
                torch_sigmas,
                device=device,
                use_types=use_types,
            )
        else:
            out = score_cpu(
                points, packed, xy, kinds, bearings, observation_types, sigmas, use_types=use_types
            )
        if mode == "gpu":
            torch.cuda.synchronize(device)
        score_s += time.perf_counter() - t
        scores[lo : lo + len(points)] = out
    return scores, query_s, score_s, max_neighbors


def hierarchical_gpu(
    bounds,
    tree,
    xy,
    kinds,
    bearings,
    observation_types,
    sigmas,
    *,
    device,
    use_types,
    coarse_spacing=100_000.0,
    levels=4,
    beam_width=8,
):
    t0 = time.perf_counter()
    candidates = candidates_on_grid(bounds, coarse_spacing)
    evaluations = 0
    max_neighbors = 0

    def evaluate(points):
        nonlocal evaluations, max_neighbors
        points = np.unique(np.asarray(points, dtype=np.float64), axis=0)
        score_parts = []
        for lo in range(0, len(points), BATCH):
            chunk = points[lo : lo + BATCH]
            packed, width = pack_neighbors(tree, chunk)
            max_neighbors = max(max_neighbors, width)
            out = score_gpu(
                chunk,
                packed,
                torch_objects,
                torch_types,
                torch_bearings,
                torch_observation_types,
                torch_sigmas,
                device=device,
                use_types=use_types,
            )
            score_parts.append(out)
        evaluations += len(points)
        return points, np.concatenate(score_parts)

    points, scores = evaluate(candidates)
    best_points, best_scores = points, scores
    step = coarse_spacing
    for _ in range(levels):
        step /= 2.0
        leaders = best_points[np.argsort(best_scores)[:beam_width]]
        next_points = []
        for x, y in leaders:
            for dx in (-step, 0.0, step):
                for dy in (-step, 0.0, step):
                    nx, ny = x + dx, y + dy
                    if bounds[0] <= nx <= bounds[1] and bounds[2] <= ny <= bounds[3]:
                        next_points.append((nx, ny))
        points, scores = evaluate(next_points)
        best_points = np.concatenate((best_points, points))
        best_scores = np.concatenate((best_scores, scores))
    winner = int(np.argmin(best_scores))
    torch.cuda.synchronize(device)
    return (
        best_points[winner],
        float(best_scores[winner]),
        evaluations,
        time.perf_counter() - t0,
        max_neighbors,
    )


def main():
    global torch_objects, torch_types, torch_bearings, torch_observation_types, torch_sigmas
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--spacings-km", nargs="+", type=float, default=[100.0, 10.0, 5.0, 2.0])
    parser.add_argument(
        "--out", type=Path, default=ROOT / "results/eastern-seaboard-gpu-search/gpu-search.csv"
    )
    parser.add_argument("--top-k", type=int, default=100)
    args = parser.parse_args()

    torch.set_num_threads(1)
    device = torch.device(args.device)
    if device.type != "cuda":
        raise ValueError("This benchmark requires CUDA.")
    import sys

    sys.path.insert(0, str(ROOT))
    module = runpy.run_path(str(ROOT / "tools/benchmark_eastern_seaboard_search.py"))
    measurements, truth, snapshots, bounds, counts = module["build_inputs"]()
    catalog = snapshots["all_scales_deduplicated"]
    records = catalog.records
    xy = np.asarray([(item.east_m, item.north_m) for item in records], dtype=np.float64)
    kinds = np.asarray([0 if item.ais_class == "vessel" else 1 for item in records], dtype=np.int8)
    observations = measurements.observations
    bearings = np.asarray([item.bearing_deg for item in observations], dtype=np.float32)
    observation_types = np.asarray(
        [0 if item.object_type in ("vessel", "ship") else 1 for item in observations],
        dtype=np.int64,
    )
    sigmas = np.asarray(
        [max(item.bearing_sigma_deg, SIGMA_FLOOR_DEG) for item in observations], dtype=np.float32
    )
    tree = cKDTree(xy)
    torch_objects = torch.as_tensor(xy, dtype=torch.float32, device=device)
    torch_types = torch.as_tensor(kinds, dtype=torch.int64, device=device)
    torch_bearings = torch.as_tensor(bearings, dtype=torch.float32, device=device)
    torch_observation_types = torch.as_tensor(observation_types, dtype=torch.int64, device=device)
    torch_sigmas = torch.as_tensor(sigmas, dtype=torch.float32, device=device)
    print(
        f"HOST {platform.node()} DEVICE {torch.cuda.get_device_name(device)} "
        f"CATALOG {len(records)} AIS {sum(kinds == 0)} AIDS {sum(kinds == 1)} "
        f"OBSERVATIONS {len(observations)} "
        f"B1_TRUTH_M {truth.observer_east_m:.1f},{truth.observer_north_m:.1f}",
        flush=True,
    )

    # Warm CUDA context and kernels on a tiny batch.
    warm = np.asarray(
        [
            [truth.observer_east_m, truth.observer_north_m],
            [truth.observer_east_m + 1, truth.observer_north_m + 1],
        ]
    )
    packed, _ = pack_neighbors(tree, warm)
    score_gpu(
        warm,
        packed,
        torch_objects,
        torch_types,
        torch_bearings,
        torch_observation_types,
        torch_sigmas,
        device=device,
        use_types=True,
    )
    torch.cuda.synchronize(device)

    rows = []
    for spacing_km in args.spacings_km:
        candidates = candidates_on_grid(bounds, spacing_km * 1000)
        print(f"GRID {spacing_km:g}km CANDIDATES {len(candidates):,}", flush=True)
        # Type-aware and type-blind GPU/CPU scoring use the identical sparse
        # neighborhood and surrogate objective for numerical parity.
        for use_types in (True, False):
            cpu, qcpu, ccpu, max_neigh = run_grid(
                candidates,
                tree,
                xy,
                kinds,
                bearings,
                observation_types,
                sigmas,
                device=device,
                use_types=use_types,
                mode="cpu",
            )
            gpu, qgpu, cgpu, gpu_max_neigh = run_grid(
                candidates,
                tree,
                xy,
                kinds,
                bearings,
                observation_types,
                sigmas,
                device=device,
                use_types=use_types,
                mode="gpu",
            )
            torch.cuda.synchronize(device)
            delta = np.abs(cpu - gpu)
            gpu_order = np.argsort(gpu)[: args.top_k]
            cpu_order = np.argsort(cpu)[: args.top_k]
            gpu_exact_index, gpu_exact_score = exact_rerank(
                gpu_order, candidates, tree, records, measurements, use_types=use_types
            )
            cpu_exact_index, cpu_exact_score = exact_rerank(
                cpu_order, candidates, tree, records, measurements, use_types=use_types
            )
            winner = int(np.argmin(gpu))
            row = {
                "algorithm": "exhaustive_grid",
                "spacing_km": spacing_km,
                "candidate_count": len(candidates),
                "catalog_count": len(records),
                "ais_count": int(sum(kinds == 0)),
                "chart_aid_count": int(sum(kinds == 1)),
                "observation_count": len(observations),
                "type_labels_used": use_types,
                "visibility_range_km": RANGE_M / 1000,
                "max_neighbors_in_candidate_range": max(gpu_max_neigh, max_neigh),
                "cpu_neighbor_query_s": qcpu,
                "cpu_surrogate_score_s": ccpu,
                "gpu_neighbor_query_s": qgpu,
                "gpu_score_and_transfer_s": cgpu,
                "gpu_search_total_s": qgpu + cgpu,
                "score_speedup_excluding_query": ccpu / cgpu if cgpu else float("inf"),
                "cpu_gpu_max_score_delta": float(delta.max(initial=0.0)),
                "cpu_gpu_best_index_agrees": int(np.argmin(cpu)) == winner,
                "cpu_gpu_top_k_overlap": len(set(cpu_order) & set(gpu_order)),
                "gpu_best_surrogate_error_m": math.hypot(
                    candidates[winner, 0] - truth.observer_east_m,
                    candidates[winner, 1] - truth.observer_north_m,
                ),
                "gpu_best_surrogate_score": float(gpu[winner]),
                "gpu_top_k_exact_best_error_m": math.hypot(
                    candidates[gpu_exact_index, 0] - truth.observer_east_m,
                    candidates[gpu_exact_index, 1] - truth.observer_north_m,
                ),
                "gpu_top_k_exact_best_score": gpu_exact_score,
                "cpu_top_k_exact_best_error_m": math.hypot(
                    candidates[cpu_exact_index, 0] - truth.observer_east_m,
                    candidates[cpu_exact_index, 1] - truth.observer_north_m,
                ),
                "cpu_top_k_exact_best_score": cpu_exact_score,
                "top_k_exact_rerank": min(args.top_k, len(candidates)),
            }
            rows.append(row)
            print(
                f"RESULT spacing={spacing_km:g}km types={use_types} n={len(candidates):,} "
                f"CPU={ccpu:.3f}s GPU={cgpu:.3f}s+query({qgpu:.3f}s) "
                f"speedup={row['score_speedup_excluding_query']:.2f}x "
                f"max_delta={row['cpu_gpu_max_score_delta']:.6g} "
                f"best_error={row['gpu_best_surrogate_error_m']:.0f}m "
                f"exact_top{args.top_k}={row['gpu_top_k_exact_best_error_m']:.0f}m",
                flush=True,
            )

    point, score, evaluations, elapsed, max_neighbors = hierarchical_gpu(
        bounds, tree, xy, kinds, bearings, observation_types, sigmas, device=device, use_types=True
    )
    hi_error = math.hypot(point[0] - truth.observer_east_m, point[1] - truth.observer_north_m)
    rows.append(
        {
            "algorithm": "hierarchical_gpu",
            "spacing_km": 100.0,
            "candidate_count": evaluations,
            "catalog_count": len(records),
            "ais_count": int(sum(kinds == 0)),
            "chart_aid_count": int(sum(kinds == 1)),
            "observation_count": len(observations),
            "type_labels_used": True,
            "visibility_range_km": RANGE_M / 1000,
            "max_neighbors_in_candidate_range": max_neighbors,
            "gpu_search_total_s": elapsed,
            "gpu_best_surrogate_error_m": hi_error,
            "gpu_best_surrogate_score": score,
            "top_k_exact_rerank": args.top_k,
        }
    )
    print(
        f"RESULT algorithm=hierarchical_gpu evals={evaluations} time={elapsed:.3f}s "
        f"best_error={hi_error:.0f}m score={score:.3f}",
        flush=True,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(dict.fromkeys(k for row in rows for k in row))
        )
        writer.writeheader()
        writer.writerows(rows)
    print(f"CSV {args.out}", flush=True)


if __name__ == "__main__":
    main()
