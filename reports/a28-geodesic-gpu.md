## Boundary correction and bounded throughput comparison

The A04 frozen replay identified 209 of 83,001,660 prior CPU samples whose first forward residual passed the requested tolerance but whose independent inverse check exceeded it slightly. The retained worst case used catalog key `enc-component:approach:Approach.Buoy_Lateral_point:US4GA1CH.000:approach.boylat.fid=986615`, catalog coordinate `(-80.98065189999943, 31.796930200000133)`, bearing `215.65789052601954°`, and range `625 m`. The GPU correction loop now targets half the declared tolerance; final GPU inverse validation still enforces the full public limit. On that input the independent PyProj residual was `-8.58335624798201e-11°`, with range error `-1.4538272807840258e-9 m`. The regression is in `test_gpu_internal_half_tolerance_resolves_captured_a04_roundoff_case`. This tests the captured worst case, not all 209 or the 83-million replay.

Default stable IDs now use a lazy `range(N)` sequence instead of allocating a tuple of one million or more Python integers. Supplied stable IDs retain their sequence order and are checked for uniqueness.

One bounded, instrumented comparison ran under the exclusive `.agent-work/benchmark.lock` using the idle physical device 3. The 1,000,000 samples used seed 280028, longitude uniform on `[-80,-67]`, latitude uniform on `[23,50]`, bearing uniform on `[0,360)`, and range uniform on `[125,20,000]` metres. The GPU path converged on all samples. Its maximum independent bearing residual was `5.020848448111792e-8°` and range error was `1.4758916222490373e-7 m`. It processed 62 chunks, transferring 32,000,000 bytes in and 48,000,000 bytes out. The measured components were 0.01037 s for host-to-device input, 1.32926 s for synchronized GPU work, 0.01546 s for device-to-host results, and 1.52276 s total inside the call. The outer wall measurement was 1.52278 s. A 1,024-sample first call, including runtime/kernel initialization, took 0.15734 s; its measured H2D/work/D2H components were 0.09985/0.05626/0.00070 s. The A04-corrected CPU helper processed the same million samples in 1.73394 s and independently passed all checks. This is one bounded run, not a statistical speedup claim. The device-2 utilization was high during the run, but the process was explicitly pinned to physical device 3; no competing process used device 3 in the pre-run activity snapshot. No fused-kernel variant was needed for this comparison.

The reproducible harness setup and distribution were:

`python
seed, count = 280028, 1_000_000
rng = np.random.default_rng(seed)
catalog = np.column_stack((
    rng.uniform(-80.0, -67.0, count),
    rng.uniform(23.0, 50.0, count),
))
bearing = rng.uniform(0.0, 360.0, count)
ranges = rng.uniform(125.0, 20_000.0, count)
# Time a 1,024-pair first call, then a small warm call.
cold = solve_reverse_bearing_cupy(
    catalog[:1024], bearing[:1024], ranges[:1024],
    max_pairs_per_chunk=1024, collect_timings=True,
)
solve_reverse_bearing_cupy(catalog[:1024], bearing[:1024], ranges[:1024])
# Time exactly one full call, component timings synchronized per chunk.
gpu = solve_reverse_bearing_cupy(
    catalog, bearing, ranges,
    max_pairs_per_chunk=16_384, collect_timings=True,
)
# Run A04 corrected CPU function on identical arrays with
# tolerance_deg=1e-7. Independently recompute bearing/range with
# pyproj.Geod(ellps="WGS84").inv after each timed solve.
`

The GPU implementation contains no FP32 stage. Exact CPU scoring remains the final authority.
