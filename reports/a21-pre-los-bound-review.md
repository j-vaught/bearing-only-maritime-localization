# A21 pre-LOS catalog-constrained bound review

## Finding

A candidate-specific lower bound can be computed before land line-of-sight (LOS) queries for the fixed `bearing_set_map_v1` objective. It can be tighter than the existing A21 bound with $K=0$, which is deliberately independent of catalog bearings and labels. The new bound is admissible only when it treats each in-range catalog record as a possible group representative, gives each representative an edge floor valid for every possible post-LOS group uncertainty, omits only the nonnegative missed-group penalty, and refuses to prune candidates whose exact score would be unresolved.

The direct representative edge cost is generally not itself a safe floor. A predicted group takes its bearing, type, and range from its nearest visible member, but takes its position uncertainty from the maximum recognized uncertainty among all group members. A farther member with greater uncertainty can change the representative's matched cost. The likelihood density is not monotone in sigma for a fixed nonzero residual, so replacing a merged group's uncertainty with the representative's individual uncertainty can overstate its cost and make a purported lower bound unsound.

## Objective relaxation

For candidate position $x$, let $R(x)$ be every supported physical catalog record that passes the exact configured range gate after applying the same visible-feature offsets as prediction. This set is formed without LOS classification, so it includes records that land may later block. The current prediction code already computes WGS84 range and true bearing, applies the inclusive or exclusive range rule, and forms circular angular components before it checks LOS. This occurs in `bearing_localization/search/visibility_screen.py` at lines 203–240 and in `bearing_localization/search/prediction.py` before lines 378–400.

Let $C(j)$ denote the pre-LOS circular component containing record $j$. Removing blocked records can split a component but cannot merge distinct components, because every gap between retained bearings is a sum of intervening nonnegative circular gaps. Therefore every actual post-LOS group is a subset of one pre-LOS component. The shared grouping implementation defines a break only at a circular gap greater than the configured resolution in `maritime_localization_shared/geometry/angular_grouping.py` at lines 91–115. The same argument covers north wrap, equal bearings, and a bridge-connected component.

For report $i$ and catalog record $j$, the representative bearing, type, range, and key are fixed by that record. Prediction selects the nearest visible member with key as the tie breaker, then assigns that representative's bearing, type, and range to the group in `bearing_localization/search/prediction.py` at lines 382–400. The final scorer passes the representative bearing and type to the likelihood builder and converts group uncertainty $U$ to angular sigma with $\deg(\operatorname{atan2}(U,d_j))$, where $d_j$ is the representative's range. See `bearing_localization/search/scoring.py` at lines 164–172. The group uncertainty itself is the maximum recognized member uncertainty, or null when all member uncertainties are unknown, in `bearing_localization/search/prediction.py` at lines 482–490.

Let $u_k$ be the recognized finite nonnegative position uncertainty of record $k$, when present. The possible group uncertainties for a group represented by $j$ are contained in a finite set derived from its pre-LOS component:

$$
W_{C(j),j} =
\begin{cases}
\{u_k : k\in C(j),\ u_k\ge u_j\}, & u_j\text{ is known},\\
\{0\}\cup\{u_k : k\in C(j),\ u_k\text{ is known}\}, & u_j\text{ is unknown}.
\end{cases}
$$

The first case includes $u_j$ itself. In the second case, zero represents the predictor's null-to-zero conversion when every member of an actual group has unknown uncertainty. Since the actual group contains its representative and is a subset of $C(j)$, its maximum uncertainty is always in $W_{C(j),j}$. This may include uncertainty values from blocked members that cannot coexist in the final group; retaining those values only makes the edge floor more optimistic.

For each $u\in W_{C(j),j}$, build the canonical cost using the actual report sigma, actual wrapped residual to the exact record bearing, the representative's normalized hard type (or the configured type-blind behavior), and predicted angular uncertainty $\deg(\operatorname{atan2}(u,d_j))$. Define $e_{ij}$ as the minimum of those costs over $W_{C(j),j}$. This keeps the exact candidate/report residual and type factor while taking the minimum over all group-uncertainty values that an exact visible subset could produce. A zero compatible type probability yields $+\infty$, as it does in the canonical likelihood.

Each exact visible group has one representative key, and different groups are disjoint, so the map from an exact report/group assignment to report/representative-record pairs is injective. The exact score is

$$
S_x = \Lambda +(m-k)r + \sum_{(i,g)\in A}c_{ig} +(n-k)g_m,
$$

where $r=-\log(\lambda/360)$ is the canonical report clutter cost and $g_m=-\log(1-P_D)>0$ is the unused-group penalty. Since each actual representative is in $R(x)$, each actual edge cost is no smaller than its $e_{ij}$, and the unused-group penalty is nonnegative. Thus a safe catalog-constrained optional-assignment bound is

$$
L_{\mathrm{assign}}(x)=
\Lambda+
\min_{M\text{ partial injective in }[m]\times R(x)}
\left(
\sum_{i\text{ unmatched in }M}r+
\sum_{(i,j)\in M}e_{ij}
\right)
\le S_x.
$$

The row-min relaxation is cheaper and also admissible:

$$
L_{\mathrm{row}}(x)=
\Lambda+\sum_{i=1}^{m}
\min\left(r,\min_{j\in R(x)}e_{ij}\right)
\le L_{\mathrm{assign}}(x).
$$

It lets multiple reports use the same record, so it is a relaxation of injective assignment. The exact optional assignment preserves one-to-one record use and should be considered only if its added cost is worthwhile. The repository already provides an optional injection solver in `bearing_localization/search/assignment.py` at lines 29–70; setting all unused-group costs to zero would produce this objective for finite clutter costs. The row minimum can be evaluated directly from the edge-floor matrix.

The $K=0$ A21 bound is $L_{\mathrm{global}}=\Lambda+\sum_i\min(r,c_i^*)$, where $c_i^*$ uses zero residual, neutral type, and the report's minimum sigma. Every catalog edge floor is at least $c_i^*$, because the catalog version has a fixed actual residual, a type factor no greater than one, and sigma no smaller than the same report minimum. Consequently $L_{\mathrm{global}}\le L_{\mathrm{row}}\le L_{\mathrm{assign}}\le S_x$, whenever both bounds are supported. A21's current implementation selects cardinalities and global per-report floors in `bearing_localization/search/candidate_lower_bound.py` at lines 63–158. It is already a valid pre-LOS bound; the catalog-constrained form adds candidate-specific residuals, representative types, finite record slots, and one-to-one capacity.

## Conditions that must fail closed

The bound applies only to `bearing_set_map_v1` and finite report clutter cost. The configuration permits $\lambda=0$, but then $r=+\infty$, while the current assignment API rejects nonfinite unmatched costs. The proposed pre-LOS implementation should return unresolved for $\lambda=0$ unless the scorer and bound are separately extended and tested to give consistent meanings to finite and infinite candidate scores.

The candidate must have known water status. The exact scorer returns invalid-water or unresolved-coverage before scoring for land or unknown observers in `bearing_localization/search/scoring.py` at lines 125–132. The catalog-constrained score cannot stand in for those statuses.

The candidate must also have a certificate that any exact prediction used by scoring has known map coverage. Exact prediction returns unresolved when any relevant ray crosses unknown coverage, and it returns unresolved-model when unsupported physical metadata is encountered; the scorer exits before assignment in either case. These gates are at `bearing_localization/search/scoring.py` lines 147–162. A pre-LOS calculation cannot infer known ray coverage merely from the observer and target endpoints. It needs a separate coverage certificate for the full observer-to-target segments, such as a known convex coverage envelope containing every segment and no unknown-area intersections. Without that certificate, use unresolved and score exactly so the existing status is preserved.

All geometry must use the same calibrated visible-feature offset behavior and WGS84 bearing/range calculations as prediction. A missing offset is treated as nominal zero by the current predictor; a partially specified or unsupported offset/model causes unresolved-model. Those rules are implemented in `bearing_localization/search/prediction.py` at lines 417–454. Records with unsupported model metadata must invalidate the bound if the exact scorer would mark the candidate unresolved, including records whose range cannot be established because their offset is unsupported.

Report variances must be finite under the configured generated-sigma fallback or supplied diagonal per-report covariance. Non-diagonal covariance and common-bearing-bias integration are unsupported by the v1 likelihood builder in `bearing_localization/search/likelihood.py` at lines 71–96. The bound must return unresolved for those regimes. The configured bearing type policy must be used exactly, including disabled labels, normalized unknown labels, and hard representative types. Duplicate positions or bearings remain separate catalog slots in the relaxation. Allowing both duplicate records to match can only lower the bound because it adds assignment capacity; the exact grouping still has one representative per group. Negative matched NLL costs are valid and must not be clamped.

The missed-group cost can be omitted only while $0<P_D<1$, which the current SearchConfig enforces. If the objective or config changes that sign or changes the group uncertainty aggregation, this proof no longer applies until rederived. No heading-bias correction may be silently added; non-null common-bearing-bias configuration is unsupported by the canonical likelihood.

## Small-case acceptance specification

Before any production pruning uses this bound, implement a tiny exhaustive oracle with at most five in-range catalog records and a few reports. For each fixture, enumerate every possible clear/blocked subset of the records, form the exact post-LOS circular groups, select the exact nearest representative, compute the exact maximum-member uncertainty, build canonical costs, and solve the complete partial assignment independently. Compute $L_{\mathrm{row}}$ and $L_{\mathrm{assign}}$ from the pre-LOS edge floors. Assert for every subset that $L_{\mathrm{global}}\le L_{\mathrm{row}}\le L_{\mathrm{assign}}\le S_x$, accounting only for the documented downward numerical margin. This enumeration must include subsets that split a bridge-connected component and groups straddling north.

The fixtures must include at least two distinct report sigmas, two uncertainty levels inside one pre-LOS component, an unknown uncertainty member, different representative types, a hard type-impossible edge, a negative zero-residual cost, fewer catalog slots than reports, more slots than reports, duplicate bearings or coordinates with deterministic range/key ties, and both empty and singleton visible sets. A range-boundary and calibrated-offset fixture must verify that $R(x)$ exactly matches the predictor's inclusive/exclusive range rule and target geometry. Compare all edge values against `build_likelihood_costs`, not a separately reimplemented likelihood. The expected-unresolved fixtures must include unknown observer status, a ray-coverage gap, unsupported offset/model metadata, non-diagonal covariance, common-bearing bias, and zero clutter intensity.

The test may use arbitrary exact LOS-subset enumeration because the theorem quantifies over every subset and does not require a map simulation. It must also check representative-key injectivity for every exact grouping. No random sample is a proof. Only after these checks should a performance measurement compare no-bound exact scoring, the current A14/A21 full-screen path, row-min pruning, and optional-assignment pruning on identical finite candidates. The current A14 screen still requires a complete LOS/model scan before A22 can prune, as documented in `reports/a22-screened.md`; avoiding that work is the only path for this proposal to address the LOS-first overhead. The coordinator-reported run pruned 85 of 255 candidates but was about 26% slower than exact evaluation, so this review makes no speedup claim.

## Recommendation

Implement the row-min form first as a separately named experimental bound after the A14 screen has produced pre-LOS targets and components, but before it calls any LOS routine. Use the component uncertainty set above; do not use a representative's individual uncertainty as if it were the final group uncertainty. Keep the optional-assignment version as a measured extension. Do not prune when candidate water, model support, or coverage status is unresolved. No production integration or performance benchmark was performed in this review.
