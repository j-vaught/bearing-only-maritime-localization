# Savannah error and Wilmington runtime diagnosis.

J.C. Vaught.

This diagnosis uses saved observations, cached object positions, saved candidate arrays, and existing sampled GPU profiles. It performs posthoc bearing geometry and coordinate-distance calculations. It does not execute a localization search, simulate another observation frame, query land visibility, rerun assignment, rebuild a cache, or change the estimator.

Savannah B18 uses the corrected 20 cm catalog rerun. All 21 selected object identities equal the generating simulator identities. Detected aid ranges span 7,170.7–9,993.3 m, with median 8,210.8 m. At that median distance, a 0.1-degree angular change corresponds to approximately 14.3 m of transverse displacement. Angular noise is bounded within 0.5 degrees, and this frame's largest actual perturbation is 0.306 degrees.

The selected center has a summed absolute angular residual of 1.962862 degrees. The true location's generating-object residual sum is 2.168428 degrees. The nearest saved certified center is only 0.107 m from truth but has residual sum 2.165140 degrees. Therefore the minimum-residual selector favors the displaced point under this noise realization. No generating-object association was wrong, and the search retained the true neighborhood. There are 18,705 certified centers over an approximately 119 m by 91 m bounding rectangle. A certificate establishes consistency with the bounded-bearing model rather than closeness to the hidden ground truth.

The local bearing Jacobian has information condition ratio 8.07. Using the underlying 1/6-degree Gaussian standard deviation in a local linearized model gives principal sensitivity scales of 16.03 m and 5.64 m. The observed displacement aligns 97.4 percent with the weak axis. These are descriptive local sensitivity calculations under known associations, not a calibrated uncertainty region or an additional estimator output.

Wilmington B16 uses its earlier-catalog saved result, since it was successful and was not one of the eight corrected-catalog reruns. It evaluated 800,623 cells and retained 325,435 terminal centers for detailed scoring. Their actual saved output counts by GPU are 3,807 on GPU 0, 319,174 on GPU 1, and 2,454 on GPU 2. GPU 1 therefore handled 98.08 percent of terminal scoring. The latest batch records from GPUs 0 and 2 were at 10.97 s and 10.77 s, while GPU 1 continued until 371.50 s.

The current worker scheduler assigns eight coarse roots at a time and processes all their descendants on that device before requesting another task. It cannot redistribute an active descendant queue to idle workers. A dense surviving region can consequently create a long single-device tail even though the initial search uses three devices. The relevant scheduling code is maritime/search/gpu/resident/engine.py, lines 477–535.

GPU 1's existing sampled profile attributes 35,080.286 ms of 36,771.084 ms total sampled stream duration to kernel.prediction.pp_pairs, or 95.40 percent. That kernel computes geodesic bearings and ranges and checks polygon line-of-sight. Cooperative assignment accounts for 73.303 ms in the same sampled profile. CUDA stream duration includes host launch gaps; samples cover every tenth batch, so these values are not a full-run kernel-time decomposition. The profile nonetheless directly identifies prediction rather than assignment as the dominant sampled operation. The host duration around scoring includes waiting for those GPU operations.

The complete numerical record, paths, hashes, candidate statistics, per-device counts, and selected profile values are preserved in saved-data-diagnosis.json. The analysis script reproduces the record from the original saved server data. No additional run is implied for B16 under the corrected catalog.
