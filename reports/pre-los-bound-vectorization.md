# Pre-LOS bound batching implementation

The row-min bound now prepares component membership and sorted uncertainty sets once per candidate. It constructs one ordered variant column for every prepared record and every uncertainty value already admitted by the scalar implementation, then passes those columns together to the canonical likelihood function. The per-record floor is still the minimum over that record's own columns, so row minima, clutter competition, baseline, and the downward numerical margin retain their prior definition.

The empty-prediction likelihood result supplies both the baseline and the per-report clutter cost. This removes the separate dummy-prediction call. When there are no reports, no variant matrix is needed; the bound still returns the same baseline-only result. No likelihood equations, objective terms, uncertainty choices, pruning rules, or search defaults changed.

## Validation

The added scalar-reference test compares the complete edge-floor matrix, baseline, clutter, row terms, raw row-min score, applied downward margin, and final bound using exact equality on a fixture with wrapped bearings, heteroscedastic observations, negative edge costs, and multiple component uncertainty variants. A call-count test confirms one empty-prediction construction and at most one batched matrix construction. Empty-report and empty-catalog cases are covered.

Focused validation passed 34 tests across the A21 bound and vectorization tests, A06 independent pre-LOS subset audit and caller checks, and A14 preparation checks. The A21 fixtures enumerate all 32 visibility subsets for five records against the independent complete-assignment oracle. Exact-score tie handling, unsupported-model fallback, and uncertainty preparation checks also passed. Ruff formatting and checks and the targeted type check passed.

## Scope and limits

No timing run, profile run, full test suite, real-data evaluation, or GPU work was performed. The focused tests establish scalar parity and bound admissibility for their fixtures; they do not establish a runtime improvement. Candidate-specific pruning remains experimental, and the production pruning default and controller are unchanged.
