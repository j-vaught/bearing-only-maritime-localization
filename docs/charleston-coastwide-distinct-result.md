# Updated Charleston search across the Eastern Seaboard

The updated full-coast search completed in 8.461936 seconds on GPUs 0, 1 and 2 and selected a position 2.060304 meters from the simulated Charleston observer. It matched all 64 positive marker reports with consistent status and zero flags. The position and summed bearing residual exactly match the previous full-coast run. All 148 consistent terminal rectangles are unchanged. Selection was frozen before the separate evaluator read truth.

The search completed the same 39,435 initial 10-kilometer cells across the original full Eastern Seaboard rectangle, refining every surviving cell until its maximum width was at most 1 meter. It evaluated 64,875 regions and retained 397 terminal centers. The previous run evaluated 1,337,411 regions and took 98.495290 seconds. The new run therefore evaluated95.149 percent fewer regions and had an observed search-loop speed ratio of 11.640. Both runs used the same configuration, GPUs 0–2 and every-tenth-batch profiling. These are observations for this corrected simulated frame.

The complete pipeline took 23.449 seconds, including 7.388 seconds of input/cache loading, frozen saved-output selection, separate accuracy evaluation and before/after cache preservation checks. Search-loop timing includes device setup, coordination and output work within the search API. CPU coordination remains; the numerical pruning, predictions and assignment calculations run on the GPUs. Saved GPU health samples and per-device profiles confirm work on all three selected devices. GPU 3 was excluded.

The final centers comprise 148 consistent, 67 inconsistent and 182 unresolved centers. The previous full-coast output had 15,441 unresolved centers. None of those old unresolved centers survived the new region pruning. The 182 new unresolved centers were previously marked inconsistent by nominal grouping alone; the independent raw-object graph now preserves them. Every remaining unresolved center is within 12.135 meters of the selected estimate, and every terminal center is within 12.717 meters. No distant coast-wide candidate remains in this completed search domain.

The remaining 182 nominal assignments match 60–63 of the 64 reports and have zero numerical flags. The raw feasibility graph does not yet remove edges using full polygon sight-line tests, and the saved universal rectangle-barrier list is empty. Final nominal prediction does use full shoreline sight lines. The raw graph can consequently retain occluded or grouping-incompatible explanations. A further certified point-visibility or grouping proof is needed to resolve those local cases; this run does not assign a cause to every individual center.

Independent review verified that all 4,930 completed root-task records cover every initial cell exactly once. The per-device root counts are 13,899 on GPU 0, 13,552 on GPU 1 and 11,984 on GPU 2. It reproduced the frozen selection and WGS84 error, verified output hashes, and confirmed that all 148 previously consistent cells, flags, residuals and 64-object assignments are bitwise unchanged.

Both saved caches were hits, cache rebuilding was forbidden, and their full SHA-256 digests, sizes and nanosecond modification times match before and after. Earlier search artifacts and estimates were preserved.

The [complete result](verification/charleston-coastwide-distinct-result.json), [all terminal centers](verification/charleston-coastwide-distinct-terminal-results.npz), and [raw profiles and GPU health records](verification/charleston-coastwide-distinct-profiles.zip) are saved together. The terminal archive contains numeric arrays and a UTF-8 manifest and requires no pickle.

Written by J.C. Vaught.
