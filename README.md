# Bearing-only maritime localization

This project simulates a vessel observing object types and bearings measured clockwise from north while its position is unknown. The sensor is assumed to have a calibrated global bearing reference. Ship orientation is randomized in the scene but does not enter the estimator. A scene contains charted or AIS-listed objects with coordinates, as well as objects whose coordinates are hidden from the estimator. The estimator receives a candidate AIS object list and the complete sensor detections, then searches for the vessel position and object correspondences. It does not receive observation identities, ranges, or non-AIS object coordinates.

The simulator treats the maximum sensor range as an experiment parameter rather than a universal physical constant. The initial configuration uses a 5 km range cap in a larger synthetic scene and sweeps visibility down to 1 km. It assumes a flat 2D map, stationary objects at each observation instant, known object types in the primary experiment, complete detection inside the range cap, and Gaussian bearing noise. Type reporting can be switched off to measure its effect on runtime, localization success, and the minimum number of detections required.

Each trial keeps the true vessel position and hidden object identities out of the estimator. Evaluation records position error, association quality, success rate, feature counts, and runtime. Non-AIS observations count as unmatched detections and therefore test outlier handling; they do not directly provide a map-relative position constraint. The minimum-feature report counts total detections and requires at least three correctly associated AIS objects for a successful position fix.

## Install and run

Install [uv](https://docs.astral.sh/uv/), then run:

```sh
uv sync
uv run bearing-localization
```

The benchmark uses randomized trials and writes `trials.csv`, `summary.csv`, `minimum_features.csv`, and `backend_profile.csv` under `results/`. It compares exhaustive position-grid scoring with multi-start least-squares refinement. Both approaches run with type labels enabled and disabled on the same observations. The summary reports feature availability separately from localization success, plus median, 95th-percentile, and 99th-percentile latency. The minimum-feature table uses a 95% success target by default. Ten frames per second corresponds to a 100 ms frame budget; reserve some of that time for sensor input and output.

For a more informative minimum-feature estimate, increase the number of trials, for example with `--trials 100`. Adjust object density, range, bearing noise, grid resolution, and success thresholds to test other conditions. Trials for a requested feature count are marked unavailable when too few objects were visible, so scarcity is represented as a failure to form a fix rather than silently substituting fewer observations.

## Performance direction

Position hypotheses are independent and can be scored in batches, which makes grid and hypothesis search suitable for CPU vectorization and potentially GPU execution. `backend_profile.csv` measures the batched angular-residual kernel separately on NumPy and, when available, CuPy. It includes transfer timing but excludes Hungarian assignment and is not an end-to-end GPU localization result. A regional index and a previous-frame motion prior are likely necessary before scaling from a local scene to a coastline-wide catalogue.

The 10 FPS target is a future system-level requirement. Synthetic benchmark results establish algorithm and scaling behavior; they do not alone establish a real-time guarantee for an entire coastline or a live sensor pipeline.
