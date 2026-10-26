# A22 pre-LOS bound profile preparation

The score-only profile caller is implemented in `tools/profile_pre_los_bound.py`. It verifies the frozen candidate IDs and positions, configuration hash, static-input hashes, and exact score audits before constructing the scorer. The saved incumbent is exactly re-evaluated before use. Unsupported preparation or bounds fall back to exact scoring.

Every proposed prune is independently scored after the timed traversal. The validation rejects unsupported certificates, invalid lower bounds, exact-best ties, and duplicate IDs. The profile derives the exact best score and tie set from the saved A22 score audits and checks them against the cached-profile audit.

The caller now acquires the canonical benchmark lock nonblocking and fails without running if another benchmark owns it. The timing experiment has not run yet. Per the benchmark schedule, the real screening run is waiting for the A20 owner to finish and for the independent caller review to complete. No latency claim is made.

Focused coverage includes incumbent revalidation, duplicate-ID rejection, supported pruning with tie preservation, unsupported-bound exact fallback, and rejection of an injected overlarge bound during independent post-pass validation. Ruff, ty, and the four focused tests pass using the existing environment without dependency installation.
