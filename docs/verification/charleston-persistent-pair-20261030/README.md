# Charleston persistent GPU pair.

J.C. Vaught.

Two approved full Eastern Seaboard searches used GPUs 0 through 2 and the same corrected Charleston observation frame. The first request took 2.856234 s and the second took 0.843451 s. Both selected the same position with WGS84 error 0.856063 m. The second request was 3.386 times faster within this matched pair.

The simulator generated one seed-11 frame from the corrected 20 cm catalog. It contains 67 detected chart-aid reports and 73 total reports. Bearing perturbations retain the existing bounded 0.5-degree configuration. Both requests copied the same measurements, whose SHA-256 is 21f48711f3c9808f62b0a6384777ffbaebf9c19a36e2fc4cc426793332c07e4b. Only positive chart-aid bearings enter the search. Observer truth is used for simulation and post-freeze accuracy evaluation. It does not enter search or saved-output selection.

Both searches completed all 39,435 initial roots, evaluated 56,855 cells, pruned 52,329, and produced 171 terminal centers. The two sets of terminal cells, statuses, warning flags, angular residuals, matched object indices, and rejection reasons agree exactly after sorting by cell coordinates. Each result contains 34 consistent, 42 inconsistent, and 95 unresolved terminal centers. The selected center has status zero and zero warning flags. No warning or certification rule was relaxed.

Worker PID 2219284 served both requests. Per-device diagnostics show construction on the first request and reuse on the second. Device initialization took approximately 1.93 s per GPU in parallel on the first request and zero on the second. Owner-thread identities agree across requests for all three devices. The same map/catalog allocations and GPU modules remained resident between searches. Worker cleanup left no live process-group members, and GPUs 0 through 2 were released after the two checks.

Search time includes first-request device initialization, traversal, and lifecycle finalization. Shared CPU input/cache and simulator-scene preparation took 51.711707 s once and is excluded from both search timings. Final saved-output selection took 0.956349 s and 0.950299 s separately. Request roundtrip durations were 2.965372 s and 0.857854 s; the first additionally includes the 0.084440 s worker startup.

The search used 128 roots per task, retained the existing 512-cell processing batch, stopped at 1 m terminal cells, and preserved the 2 GB workspace ceiling. The original full coast bounds and saved caches were reused. The earlier 7.819 s Charleston measurement used a different 64-aid observation frame, so the controlled comparison here is the matched first-request versus reused-request pair.

An independent read-only audit recomputed the WGS84 errors, verified frozen estimate hashes and truth isolation, compared every terminal array, and recomputed both saved-cache SHA-256 hashes. The hashes, sizes, and modification times agree with the before/after pair records and the prior corrected-catalog suite. No additional searches were executed.

The complete raw run remains at runs/charleston-persistent-pair-20261030T005122Z on the server. This folder preserves the observations, simulation diagnostics, settings, result summaries, estimates, profiles, compact canonical terminal arrays, and cache fingerprints. The check runner is tools/check_persistent_charleston_pair.py at source revision 645ceb9.
