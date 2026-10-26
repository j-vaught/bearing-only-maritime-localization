# Candidate preparation diagnostic

The fixed-candidate profile dated 2026-10-26 compares a baseline scorer with the prepared prediction context on three projected candidate positions. Both runs use the same four-report development measurement set, 66,668-record catalog, known-coverage B1 map, and CPU configuration. The candidate score outputs and predicted group counts are identical across runs.

The median candidate-call time changed from 455.201731 ms to 4.432294 ms, while median scorer setup changed from 131.312977 ms to 824.981926 ms. Each median covers four runs, with twelve candidate evaluations per run. The profile shows work shifting from each candidate evaluation into scorer initialization. It is a fixed-candidate diagnostic, not a controlled benchmark, accuracy result, calibration result, held-out evaluation, or GPU measurement.

The three candidate scores are 24.544416125800623, 31.45217140478276, and 26.02598193152521 in both runs. Their predicted group counts are zero, three, and four. The source JSON records hashes for the catalog, map, measurement input, audit, and raw baseline and prepared run records. The profiled implementation revision was cb0493bfa4b90101048249948fad94e87ad2c7ca. The source audit and raw trial records remain under `.agent-work/outputs/a14-profile/` and `.agent-work/outputs/a14-prepared-profile/`.
