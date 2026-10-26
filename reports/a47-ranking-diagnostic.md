# A47 wedge ranking diagnostic

This repair starts from integration commit 07d8036 and keeps complete catalog voting separate from proposal selection. The frozen full-coast input contains 66,668 catalog records and five bearings, for 333,340 source pairs. The catalog hash is 2554a1f6e574b6b9ffdefe6bb7b96636d756c9cbd87018be8f7e078738227704, the measurement hash is ed1145cee93d4f06ffa983f671319dee49e9a95cb4692a2dd14a44c4a8eeceb1, and the map hash is cce049c5911b39072d30f26c09947782a78e350e65a3f69c6923312fba3f9d93.

## Mechanism

The complete generator retains all accumulated cells and sorts them by descending observation vote followed by cell indices. On the 250 m full-coast run, 1,530,303 cells were generated from all 333,340 pairs. When many cells have five votes, this deterministic spatial tie-break sends a broad coordinate prefix to a 256-candidate scorer. The observed top score of 24.93 with one matched report is therefore consistent with a proposal prefix effect even though all five observations had matched truth-compatible cells elsewhere.

A04's independent projection audit measured up to 5.232286834 degrees of true-north versus global-frame grid-bearing discrepancy, with 89.1192 percent of catalog sources exceeding the declared 0.5 degree bound. Consequently, a global-frame planar residual cannot be presented as corrected true-bearing evidence. The geographic adapter remains an approximation until source-specific geodesic reverse rays or qualified tiles are integrated.

## Metadata interface

WedgeProposal carries normalized_angular_residual and evidence_score fields with backward-compatible defaults. Complete voting computes this scalar evidence only when collect_metadata=True, which keeps the default path free of per-source-cell residual work. Claims inside the half-diagonal enclosure receive residual 1 and zero evidence because the raster rule accepts every bearing there. Evidence also decreases with the angular quantization width. WedgeProposal.ranking_key exposes an opt-in key of vote count, residual, evidence, and stable cell indices. Existing proposal ordering remains vote count followed by cell indices, so no scoring behavior or candidate is silently changed.

WedgeProposalResult.vote_histogram exposes deterministic vote-count coverage metadata. The metadata is diagnostic until a corrected true-bearing generator supplies source-specific residuals. It does not exclude cells, alter budgets, or claim global objective optimality.

## Verification

The focused suite passed 17 tests with 5 optional GPU tests skipped. Ruff format, Ruff checks, type checks, and diff checks passed. Existing complete-voting tests retain exact candidate IDs, vote counts, source counters, budget status, and duplicate-observation semantics. A new fixture verifies histogram accounting, deterministic priority keys, and zero evidence for a source-near enclosure cell.

No evaluator or held-out input was used, and no full 250 m run was repeated after metadata instrumentation. The prior integrated 250 m generation measured 227 seconds before this metadata path. The corrected geodesic helper and any scorer opt-in ordering remain separate follow-up work.
