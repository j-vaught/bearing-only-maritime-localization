# Dynamic sharing across all eligible sites.

J.C. Vaught.

All 20 previously eligible observation frames were searched across the full eastern seaboard with dynamic terminal scoring on GPUs 0 through 2. Every selected estimate and every saved terminal array matches the earlier preloaded-map run exactly. Warnings, tolerances, charting-aid normalization, pruning, and land visibility are unchanged.

| Site | Charting aids | Older preloaded time, s | Dynamic time, s | Time reduction | Error in both modes, m |
|---|---:|---:|---:|---:|---:|
| B2 Portland ME | 62 | 0.974 | 0.856 | 12.1% | 2.246 |
| B4 Boston | 74 | 0.885 | 0.832 | 6.0% | 0.267 |
| B5 Providence | 32 | 24.363 | 15.190 | 37.6% | 3.565 |
| B6 New Haven | 36 | 4.624 | 3.461 | 25.2% | 0.933 |
| B7 Bridgeport CT | 24 | 16.868 | 9.638 | 42.9% | 2.338 |
| B8 Port Jefferson NY | 26 | 1.338 | 1.358 | -1.4% | 1.842 |
| B9 New York and New Jersey | 68 | 1.111 | 1.149 | -3.5% | 0.863 |
| B11 Philadelphia | 18 | 264.244 | 173.566 | 34.3% | 2.061 |
| B12 Wilmington DE | 32 | 7.496 | 5.321 | 29.0% | 1.368 |
| B13 Baltimore | 47 | 1.212 | 1.205 | 0.6% | 1.188 |
| B14 Virginia Hampton Roads | 97 | 0.774 | 0.769 | 0.5% | 1.277 |
| B15 Morehead City | 65 | 0.903 | 0.871 | 3.5% | 0.793 |
| B16 Wilmington NC | 24 | 81.533 | 47.182 | 42.1% | 0.742 |
| B17 Charleston | 67 | 0.833 | 0.859 | -3.1% | 0.856 |
| B18 Savannah | 21 | 5.787 | 5.755 | 0.6% | 23.456 |
| B19 Brunswick GA | 26 | 1.270 | 1.245 | 2.0% | 0.396 |
| B20 Jacksonville | 21 | 4.490 | 3.360 | 25.2% | 1.264 |
| B21 Port Canaveral | 57 | 0.889 | 0.887 | 0.2% | 0.217 |
| B22 Palm Beach | 33 | 5.978 | 4.240 | 29.1% | 0.408 |
| B24 PortMiami | 57 | 0.946 | 0.942 | 0.4% | 0.614 |

The sum of resident search times fell from 426.518 s to 278.685 s, a reduction of 34.7 percent. This excludes simulator generation, cached input loading, GPU map preparation, saved-result selection, accuracy evaluation, and output verification. One resident GPU session retains map and catalog buffers across the entire new suite. An additional full-coast Charleston warmup is excluded.

The older column is the saved preloaded B-site suite, and the new column is one dynamic search per site using its identical frozen observations. These are individual search measurements. Small differences in the subsecond cases occur without work handoffs and do not establish a scheduling improvement. The separate paired slow-site experiment provides repeated comparisons for the heavy cases.

Eligibility requires more than 15 detected physical charting aids. No additional sites were included, no input frames were regenerated, and no saved map or canonical-object cache was rebuilt. Both cache hashes, file sizes, and modification times remain identical. The selected estimate is saved and hashed before the evaluator reads truth. All terminal coordinate, status, flag, residual, and association arrays were compared after sorting.

Savannah retains its previous 23.456 m error. Dynamic sharing changes the execution schedule and preserves that result. It does not repair the existing localization ambiguity.

The hardware-counter attempt followed completion of all 20 timing searches. Nsight Compute 2025.3.1 was extracted into a user-owned HDD directory from the official NVIDIA package after SHA-256 verification. The profiler targeted three pp_pairs launches on GPUs 0 through 2 after skipping ten matching launches, requesting compute throughput, memory traffic, occupancy, instruction statistics, scheduler stalls, warp states, and roofline sections. GPU clocks and caches were not forcibly controlled. Instrumented timing is separate from the normal speed comparison.

The driver denied hardware-counter access on all three GPUs with ERR_NVGPUCTRPERM. The denied collector was interrupted and released its GPU allocations. No hardware counters were collected, and no compute-versus-bandwidth conclusion is claimed from this attempt. Cache fingerprints were checked again after it exited. The completed normal B11 run retains its previous accuracy; accuracy under successful counter instrumentation remains pending.

The server has RmProfilingAdminOnly set to 1. Passwordless sudo and Docker daemon access are unavailable to this account. An administrator can run the prepared command below without changing driver configuration or restarting GPUs.

```sh
cd /home/jvaught2/projects/bearing-only-maritime-localization
sudo .venv/bin/python tools/profile_philadelphia_counters.py --execute
```

The driver checks the completed timing suite, profiles only Philadelphia on GPUs 0 through 2, and independently verifies the saved terminal outputs, selected estimate, WGS84 error, and cache fingerprints. A successful capture exports hardware-metrics.csv and philadelphia.ncu-rep under its new HDD run directory. The scheduler remains opt-in.

[NVIDIA counter-access requirements](https://developer.nvidia.com/nvidia-development-tools-solutions-err_nvgpuctrperm-permission-issue-performance-counters) and [Nsight Compute collection options](https://docs.nvidia.com/nsight-compute/NsightComputeCli/) describe the permissions and kernel filters used here.

The raw timing run is /home/jvaught2/projects/bearing-only-maritime-localization/runs/dynamic-all-eligible-20261030T040818Z. The executed search implementation was revision 58695aaaebc3563f96508bd4b32ca7f39e23fbb6. The published timing driver is revision f593d27. A type-annotation-only edit followed launch; numerical search code and runtime settings were unchanged.

A subsequent [non-admin software source profile](../philadelphia-software-profile-20261030/README.md) succeeded on saved Philadelphia batches. It provides instruction-level counts and compiled-resource measurements while the hardware-counter restriction remains in place.
