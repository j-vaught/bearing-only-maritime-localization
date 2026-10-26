# A35 geodesic-wedge omission diagnosis

## Scope and inputs

This evaluator-only diagnosis uses the frozen development trial `development/port-001/placement-001/r0/range10000m/typesoff/nominal`. The truth was scored only after the A26 search estimate and telemetry were written. No truth-derived point entered candidate generation or search. Search settings, objective, acceptance thresholds, and frozen inputs were unchanged.

The input, truth, search configuration, estimate, and telemetry SHA-256 values are recorded in the machine-readable artifact. The truth CRS is EPSG:32619; the map and search geometry use EPSG:32618. After converting through geographic coordinates into the bounds-centered local azimuthal-equidistant frame, truth is at `(398722.157030, 1040404.904479)` and lies in 250 m cell `(1594,4161)`. The truth point and all nine adjacent cell centers classify as water.

## Objective at truth and nearby cells

At truth, the exact scorer assigns all five reports with total objective 5.206587168. The decomposition is matched cost 4.206587168, clutter cost 1.0, and missed cost 0. The containing cell center scores 17.849166741 with four assignments and four predicted groups. The adjacent cell `(1594,4160)` is the best of the nine centers at 16.355641148, with four assignments and six predicted groups. The saved estimate's best score is 23.786192787, so exact scoring never received the truth basin.

The containing center has four predicted groups and four assignments. Its report 0 assignment has the wrong group member set; reports 2, 3, and 4 match their truth groups. At the adjacent best center, reports 0, 1, 2, and 4 match the truth groups, while two additional predicted groups incur missed-group cost. These exact objective terms show that a high witness priority does not reliably identify the best complete objective.

## Proposal inclusion audit

The cell-center predicate was evaluated against every one of the 333,340 configured observation/catalog pairs. The containing cell has five report votes, a five-way distinct matching, and 13 unique witness keys across 18 report/source incidences. Its mean normalized angular residual is 0.236674680 and summed source evidence is 0.358545753. Four of the five evaluator bearing residuals at truth are inside ±0.5 degrees; the fifth is+ßuÁ‚ùÁR0.997875 degrees. Quantization expands the exact cell-center witness interval enough that the cell still reaches the full 5/5 witness tier.

I then replayed the configured reverse-ray sampler only for those 18 eligible source/report pairs, using its three angular offsets, 83 range samples, solver acceptance, projection, and nine-cell neighbor splat. All 18 of 18 pairs emitted the containing cell, with 333 valid sample-neighbor hits. The water filter also retains the cell. The target was therefore generated, then omitted before exact scoring by the 256-candidate priority cutoff. This rules out the sigma window, land-center rejection, and reverse-ray sampling as the cause for this cell.

The generation histogram contains 239,528 cells in the 5-match/5-vote tier, and all 256 evaluated witnesses are in that tier. The last evaluated witness has normalized residual 0.026743427 and evidence 0.713767292, both ranking ahead of the target cell under the configured witness-priority ordering. The run evaluated only 256 proposals, so this priority cutoff is the direct omission mechanism. The exact truth and center objectives show the ranking proxy is not objective-aware.

## Next stage

The next stage should retain witness-ranked proposals while reserving deterministic exact-score slots for spatially diverse, water-valid cells from the full 5/5 tier. A pre-score proxy should include predicted-group merging, assignment, clutter, and missed-group penalties. Candidate recall and exact-objective agreement can then be measured on frozen inputs without using evaluator truth as search input.

## Artifact

The reproducible output is `.agent-work/outputs/a35-wedge-omission/diagnostic.json`, SHA-256 `58eed8123e615e92127fb8f2511e44c02d6139f7456a49c080a2cc952651b0d7`. It contains the exact scores and assignments at truth and all nine centers, per-report cell witnesses, the all-pair predicate counts, exact sampler-and-splat replay counts, evaluated-witness cutoff, and hashes for all frozen inputs.
