# Cached-mode continuous refinement

The runner selects at most 64 spatially separated local minima from the committed coarse-score population. Selection uses score, position, and stable candidate identity. Missing neighboring scores remain unknown. The original proposal domain and the 64-mode cap remain incomplete search.

The first invocation used harness 07d74f0 and the frozen scoring package 0d34a17. It stopped after 2.598717 seconds before refining any mode because the candidate plan uses implicit line ordinals rather than an input_index field. The original failed output remains intact, and its manifest is tracked in reports/experiments/a20-cached-refinement-v1.

The corrected reader at 0c0271d accepts implicit ordinals and rejects conflicting explicit indices. Seventeen focused selection, integrity, and refinement tests passed, along with Ruff and ty. The verified Python 3.11 environment was reused without downloading packages. The second invocation uses the identical physical inputs and exact scoring package, a 1800-second deadline, one numerical-library thread, and the canonical benchmark lock. Its maximum 64 mode outputs are written separately with per-mode assignments, iteration history, and hashes.

The running diagnostic uses initial pattern-search steps of 125 m, at most 50 iterations, and the existing objective-improvement, relative-change, and 0.01 m termination rules. Every trial recomputes the full exact score. No evaluator truth is read. Runtime is a shared-server diagnostic, not a clean throughput benchmark. Local tests ran concurrently during part of the request; formal timing requires a separate uncontaminated run.

The second invocation is still running. Its final outcomes and independent evaluation will be added after completion.
