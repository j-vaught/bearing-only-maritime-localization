# Accuracy-region lower bounds

## Scope

This implementation is an early pruning aid for rectangular regions in projected east/north metres. It returns a structured unresolved result when visibility, angular grouping, or bearing variance can change inside the region. Its supported regime assumes those three properties remain fixed, the prediction group identities and type probabilities remain fixed, and all candidate scores use the same configured objective and miss-cost baselines.

The geometric enclosure is deliberately a full circle for each landmark. A rectangle containing a landmark has a bearing singularity and requires the full circle. A rectangle not containing its landmark also receives the full circle until a conservative WGS84 true-north geodesic arc bound, including the projected-rectangle transformation error, is derived. The implementation therefore makes no local-planar bearing claim along a coast. The full-circle enclosure is valid but can make pruning weak.

## Objective and derivation

For reports $i=1,\ldots,n$ and predicted groups $j=1,\ldots,m$, the implemented partial-assignment score is

$$
C(A)=\Lambda+\sum_{i\notin A_R}r_i+\sum_{j\notin A_G}g_j+\sum_{(i,j)\in A}c_{ij},
$$

where $A$ is an injective report/group assignment, $r_i$ is the report-clutter cost, $g_j$ is the unused-group miss cost, and $\Lambda$ is the objective baseline. The wrapped bearing mixture density is maximal at zero residual, so evaluating each matched likelihood at zero residual yields an edge floor $\underline c_{ij}\leq c_{ij}(x)$ throughout the full-circle enclosure. The legacy absolute wrapped-residual cost has the same zero-residual minimum. Predicted type probabilities, report variance, predicted uncertainty, detection probability, clutter intensity, and miss costs stay fixed.

The row relaxation removes one-to-one group capacity. Starting from the all-unmatched baseline, each report independently chooses its best edge adjustment or remains unmatched.

$$
L_{row}=\Lambda+\sum_i r_i+\sum_j g_j+
\sum_i\min\left(0,\min_j(\underline c_{ij}-r_i-g_j)\right).
$$

The tighter bound solves the exact partial one-to-one assignment on $\underline c_{ij}$ while retaining the same $r_i$, $g_j$, and $\Lambda$. Since every candidate assignment replaces its true edge costs with no-greater edge floors, the optimum of that lower-edge objective cannot exceed the full objective anywhere in the region. Negative NLL edges are retained; no nonnegativity assumption enters either relaxation.

Each finite edge floor is reduced by the configured absolute numerical margin before either relaxation. A further downward slack is subtracted from the returned score using the sum of absolute finite edge, miss, and baseline magnitudes. The default margin is $10^{-10}$ in score units. It covers arithmetic accumulation conservatively; it is not a geodesic enclosure margin because bearings use a full circle.

## Coordinate and model limits

Bounds and landmark coordinates are projected east/north metres in one common CRS. Landmark containment uses the closed rectangle edges. The actual bearing model remains WGS84 true-north geodesic, while the rectangle is only a search-coordinate region. No grid-spacing, corner-only, or projected-plane angle approximation is treated as a valid geodesic arc. A tighter bearing enclosure requires a separate derivation of the map projection and ellipsoidal geodesic error.

Changes in line of sight, range eligibility, observer water status, angular merging, group membership, catalog uncertainty, report or predicted variance, or common-bearing-bias likelihood make this bound unsupported. The caller must pass the corresponding fixed-regime flags as false; the result then contains `bound=None` and a reason. A22 can safely continue subdivision or candidate evaluation on unresolved regions.
