# A47 geodesic wedge proposal generator

This implementation adds bearing_localization.search.proposals.geodesic_wedge. It samples WGS84 reverse-bearing rays with A04's vectorized solver, projects sampled positions only to obtain grid cell indices, and checks each splatted cell center with a geographic inverse bearing. The generator never claims continuous coastwide coverage or certifies exclusions.

## Interface and accounting

generate_geographic_reverse_wedge_proposals_geodesic accepts sorted WedgeObservation and GeographicWedgeCatalogObject records, a LocalFrame, finite range and cell geometry, explicit source-pair and candidate-cell budgets, source chunk size, optional range step, and an optional report-index-to-sigma mapping. The default range step is half a cell. Angular offsets are sampled so the maximum gap at the requested range has a lateral chord no greater than half a cell. Every source pair and geodesic sample is counted. Nonconverged finite samples remain unresolved and increment failure counters; zero-range samples are retained for geometry but carry no angular evidence.

Each proposal retains all distinct catalog witness keys and a report-to-witness mapping. The ranking key is maximum bipartite matching cardinality between reports and distinct catalog keys, then report vote count, lower normalized residual, higher evidence, and stable cell indices. This prevents one catalog source from satisfying multiple reports while retaining every generated cell. Candidate storage exhaustion returns all cells accumulated before the explicit stop with budget_exhausted/candidate_cells; source exhaustion reports the exact processed count. No final top-K truncation is performed.

## Frozen inputs and scope

The implementation was developed against the frozen A26 catalog hash 2554a1f6e574b6b9ffdefe6bb7b96636d756c9cbd87018be8f7e078738227704, measurement hash ed1145cee93d4f06ffa983f671319dee49e9a95cb4692a2dd14a44c4a8eeceb1, and map hash cce049c5911b39072d30f26c09947782a78e350e65a3f69c6923312fba3f9d93. No full-coast generation or evaluator run was repeated. The global frame's measured convergence error remains a separate limitation; this generator does not silently convert projected grid bearings into true bearings.

## Verification

The geodesic and wedge focused suite passed 21 tests with 5 optional hardware skips. Ruff formatting and checks, type checks, and existing A23/A28 wedge tests passed. Tiny exhaustive fixtures cover distinct-source matching, source-near zero evidence, wraparound, variable sigma, source budgets, and storage budgets. A04's shared helper prerequisite is commit 458f644; the helper's strict convergence behavior is preserved in the reported failure counters.
