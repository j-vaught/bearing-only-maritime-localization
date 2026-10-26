# A21 pre-LOS catalog row-min bound

## Scope

This report records an experimental lower bound for the exact `bearing_set_map_v1` candidate objective. It is deliberately unwired from candidate pruning. The bound accepts the factory-minted `PreparedPreLOSTargets` bundle from `prepare_pre_los_targets`, so callers cannot provide a shortened catalog, fabricated target geometry, or an unverified coverage flag.

The public function is `pre_los_catalog_row_min_bound(measurements, prepared_targets, config, *, numerical_margin=1e-10)`. It returns a finite `bound` only when the prepared bundle is bound to the exact scorer, measurement object, configuration object, candidate, target set, and prepared coverage map. Unsupported objective settings, zero clutter intensity, common bearing bias, non-diagonal observation covariance, unsupported nearby feature models, absent coverage proof, and any bundle mismatch return `supported=False` and `bound=None`.

## Bound derivation

For a candidate position, the exact pre-LOS preparation enumerates the complete in-range physical catalog set before land visibility is evaluated. Circular components are recomputed with the scorer's angular resolution. Every post-LOS visible group is a subset of one such component because deleting blocked bearings can increase adjacent gaps but cannot merge components.

For a visible group, the representative is its nearest member with the stable key tie break. The group uncertainty is the maximum nonnegative member uncertainty, with zero used if every member has unknown uncertainty. For each possible representative record $j$, the implementation considers every group-maximum uncertainty compatible with that representative in its full pre-LOS component. It then calls the canonical `build_likelihood_costs` routine for each report $i$, using the actual representative bearing and type and the exact predicted angular uncertainty `degrees(atan2(U, range_m))`. The resulting edge floor is

$$
e_{ij} = \min_{U \in \mathcal{U}_{C(j),j}} c_{ij}(U),
$$

where $\mathcal{U}_{C(j),j}$ contains all component member uncertainties that can be the maximum while still including representative $j$. This explicitly accounts for the fact that the wrapped-Gaussian mixture is not assumed monotone in uncertainty.

Let $b_i$ be the exact per-report clutter cost from the same canonical likelihood routine and let $\Lambda$ be its baseline constant. Every complete injective assignment either matches report $i$ to one visible group or pays $b_i$ for that unmatched report. The relaxation removes one-to-one competition by allowing every row to choose its best catalog edge independently, and it removes unused-group miss costs. Since $-\log(1-P_d) \ge 0$ for the validated $P_d \in (0,1)$, omitted miss costs cannot lower the exact score. Thus the unrounded relaxation is

$$
L = \Lambda + \sum_i \min\left(b_i, \min_j e_{ij}\right).
$$

The row relaxation permits repeated use of a catalog record and can only reduce the minimum relative to an injective assignment. Each exact representative edge is at least its corresponding $e_{ij}$, while each unmatched report contributes the same exact $b_i$. Therefore $L$ is a lower bound, including when canonical matched negative log likelihood costs are negative. The finite-domain API rejects $\lambda=0$, for which the exact scorer's unmatched report cost is infinite.

The returned bound subtracts a configurable scale-aware numerical margin and applies `nextafter(..., -inf)` before returning a finite value. The downward margin is $m(1 + |\Lambda| + \sum_i |r_i|)$, where $m$ is the caller's nonnegative margin and $r_i$ is the relaxed row term. This guards the computed lower-bound arithmetic from rounding upward; it is not a statistical uncertainty interval.

## Supported semantics

The implementation preserves report-specific heteroscedastic sigma, the exact wrapped Gaussian/outlier mixture, detection probability, normalized hard type labels, and the exact report clutter cost by calling `build_likelihood_costs`. Hard cross-type edges stay finite under the scorer's four-class confusion model. Catalog preparation currently produces normalized hard labels, so a caller cannot inject a soft type distribution through the trusted bundle. The canonical likelihood supports explicit soft distributions, but this row-min API does not broaden the catalog data contract.

The prepared bundle binds the candidate and every target map coordinate into an A11 coverage certificate and checks that certificate against the same prepared map context. A14 binds the full catalog extraction, feature-model support, prediction context, scorer, measurements, and configuration. A13's exact geodesic query path is used to preserve predictor parity while preparing target geometry. A certificate alone is not treated as proof of catalog completeness.

## Validation

The exhaustive fixture uses five catalog records and enumerates all $2^5=32$ clear/blocked visibility subsets. It includes a north-crossing component, mixed types, a representative tie by key, known and unknown uncertainties, heterogeneous report sigma, negative canonical matched costs, more possible groups than reports, and more reports than groups. Each subset is scored with an independent exhaustive partial-assignment oracle, and every exact score is at least the returned bound. Separate checks cover common-bias and non-diagonal covariance rejection, zero clutter intensity, unsupported nearby feature models, unknown coverage, candidate/measurement/config binding, boolean numeric rejection, and raw-record rejection.

No performance benchmark or search-result comparison was run. The full frozen exact run remains unchanged. This bound remains experimental until an independent review approves the proof, the preparation/scorer parity, and any future caller wiring.
