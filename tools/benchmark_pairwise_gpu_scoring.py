#!/usr/bin/env python3
"""Benchmark type-aware bearing residual evaluation across candidate locations."""

import argparse
import csv
import os
import platform
import time
from importlib import import_module

import numpy as np

torch = import_module("torch")


def make_scene(seed, m, k):
    rng = np.random.default_rng(seed)
    objects = np.column_stack(
        (rng.uniform(-60000, 60000, m), rng.uniform(-170000, 170000, m))
    ).astype(np.float32)
    types = np.zeros(m, dtype=np.int64)
    types[int(m * 0.55) :] = 1
    truth = np.array([rng.uniform(-20000, 20000), rng.uniform(-60000, 60000)], dtype=np.float32)
    chosen = rng.choice(m, size=k, replace=False)
    delta = objects[chosen] - truth
    bearings = (np.degrees(np.arctan2(delta[:, 0], delta[:, 1])) % 360).astype(np.float32)
    bearings = (bearings + rng.normal(0, 0.25, k)).astype(np.float32) % 360
    return objects, types, bearings, types[chosen].copy()


def score_numpy(hyp, objects, types, bearings, obs_types, use_types, chunk=2048):
    out = np.zeros(len(hyp), dtype=np.float32)
    for lo in range(0, len(hyp), chunk):
        h = hyp[lo : lo + chunk]
        score = np.zeros(len(h), dtype=np.float32)
        dx = objects[:, 0][None, :] - h[:, 0, None]
        dy = objects[:, 1][None, :] - h[:, 1, None]
        pred = np.degrees(np.arctan2(dx, dy))
        for j, b in enumerate(bearings):
            err = np.abs((pred - b + 180) % 360 - 180)
            if use_types:
                err[:, types != obs_types[j]] = np.inf
            score += np.min(err, axis=1)
        out[lo : lo + len(h)] = score
    return out


def score_torch(hyp, objects, types, bearings, obs_types, use_types, chunk=4096):
    out = torch.empty((hyp.shape[0],), dtype=torch.float32, device=hyp.device)
    for lo in range(0, hyp.shape[0], chunk):
        h = hyp[lo : lo + chunk]
        score = torch.zeros((h.shape[0],), dtype=torch.float32, device=hyp.device)
        dx = objects[None, :, 0] - h[:, None, 0]
        dy = objects[None, :, 1] - h[:, None, 1]
        pred = torch.rad2deg(torch.atan2(dx, dy))
        for j in range(len(bearings)):
            err = torch.abs(torch.remainder(pred - bearings[j] + 180, 360) - 180)
            if use_types:
                err = err.masked_fill(types != obs_types[j], float("inf"))
            score += torch.amin(err, dim=1)
        out[lo : lo + len(h)] = score
    return out


def timed(fn, repeats=3, synchronize=lambda: None):
    vals = []
    result = None
    for _ in range(repeats):
        synchronize()
        t = time.perf_counter()
        result = fn()
        synchronize()
        vals.append((time.perf_counter() - t) * 1000)
    return float(np.median(vals)), result, vals


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--device", default="cuda:0")
    p.add_argument("--out", default="/tmp/pairwise_hypothesis_gpu_results.csv")
    p.add_argument("--seed", type=int, default=20261024)
    a = p.parse_args()
    dev = torch.device(a.device)
    print(
        "HOST",
        platform.node(),
        "PYTHON",
        platform.python_version(),
        "NUMPY",
        np.__version__,
        "TORCH",
        torch.__version__,
        "TORCH_CUDA",
        torch.version.cuda,
    )
    print("DEVICE", dev, torch.cuda.get_device_name(dev), "GPU_COUNT", torch.cuda.device_count())
    print("PID", os.getpid(), "VISIBLE", os.environ.get("CUDA_VISIBLE_DEVICES", "(all)"))

    def sync():
        return torch.cuda.synchronize(dev)

    x = torch.zeros((1,), device=dev)
    (x + 1).sum().item()
    sync()
    cases = [
        (1024, 400, 5),
        (8192, 800, 10),
        (32768, 800, 10),
        (32768, 2000, 10),
        (32768, 800, 20),
        (65536, 800, 10),
    ]
    rows = []
    for ci, (hn, m, k) in enumerate(cases):
        objects, types, bearings, obs_types = make_scene(a.seed + ci, m, k)
        rng = np.random.default_rng(a.seed + 99 + ci)
        hyp = np.column_stack(
            (rng.uniform(-60000, 60000, hn), rng.uniform(-170000, 170000, hn))
        ).astype(np.float32)
        for use_types in (False, True):
            cpu_ms, cpu, cr = timed(
                lambda: score_numpy(hyp, objects, types, bearings, obs_types, use_types), 3
            )

            def gpu_e2e():
                th = torch.from_numpy(hyp).to(dev)
                to = torch.from_numpy(objects).to(dev)
                tt = torch.from_numpy(types).to(dev)
                tb = torch.from_numpy(bearings).to(dev)
                ty = torch.from_numpy(obs_types).to(dev)
                return score_torch(th, to, tt, tb, ty, use_types).cpu().numpy()

            gpu_ms, gpu, gr = timed(gpu_e2e, 5, sync)
            maxdiff = float(np.max(np.abs(cpu - gpu)))
            corr = float(np.corrcoef(cpu, gpu)[0, 1])
            ci0 = int(np.argmin(cpu))
            gi0 = int(np.argmin(gpu))
            row = dict(
                hypotheses=hn,
                objects=m,
                observations=k,
                type_gated=use_types,
                cpu_ms=round(cpu_ms, 3),
                gpu_e2e_ms=round(gpu_ms, 3),
                speedup=round(cpu_ms / gpu_ms, 3),
                max_abs_score_delta_deg=round(maxdiff, 5),
                score_corr=round(corr, 8),
                cpu_best_idx=ci0,
                gpu_best_idx=gi0,
                best_idx_agree=ci0 == gi0,
                cpu_best_score=round(float(cpu[ci0]), 5),
                gpu_best_score=round(float(gpu[gi0]), 5),
                cpu_repeats_ms=";".join(f"{v:.3f}" for v in cr),
                gpu_repeats_ms=";".join(f"{v:.3f}" for v in gr),
            )
            rows.append(row)
            print(row, flush=True)
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print("CSV", a.out)


if __name__ == "__main__":
    main()
