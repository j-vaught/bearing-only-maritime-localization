# Development feasibility pilot

This pilot measures full-domain search execution and records algorithm-reported proposal counts separately from scorer telemetry. It does not run the evaluator, inspect truth contents, calibrate thresholds, execute validation, or open held-out trials. A completed search status is an execution result only. A budget-exhausted search is retained as such and cannot count as a completed search over the full proposal stream or as an accepted localization.

The selection is generated from the development plan, preparation status, and pre-search support files. It chooses one supported nominal trial for each of the six range and label gates, one prepared unsupported nominal trial, and one preparation failure when available. The trial-table order resolves ties. Search never receives truth, support, or preparation diagnostics. The unsupported trial is still run when its measurements exist. The failed row remains in every run manifest without a process invocation when its measurements are missing.

The checked-in configuration compares pairwise, wedge, hierarchical, grid, and reference-grid search at candidate budgets 64 and 256. Every algorithm uses the same `bearing_set_map_v1` objective, the same six-gate trial inputs, a 10 km grid spacing, and a common full-domain projected bounding rectangle. The rectangle is calculated from the union of the supplied MapV2 known-coverage and explicit-unknown geometries in EPSG:32618, then padded by 10 km. The pilot keeps the partial map's unknown regions explicit. It does not use the unapproved candidate map export or restrict search to a trial's reference-water map.

Run selection-only first so its pre-search case list can be inspected. Then use the same command without `--select-only`. Outputs belong under a separate `.agent-work/outputs/a26-pilot` directory and include selected rows, source hashes, scope manifests, per-trial SearchConfig files, commands, estimates, telemetry, logs, and run manifests. No output is written into A38's prepared inputs.

```sh
uv run python tools/run_accuracy_pilot.py \
  --trial-table .agent-work/outputs/a38-corrected-dev/plan/trials.csv \
  --preparation-manifest .agent-work/outputs/a38-corrected-dev/inputs/development/preparation.csv \
  --input-root .agent-work/outputs/a38-corrected-dev/inputs \
  --catalog .agent-work/cache/maps/noaa-enc-east-coast-fullcoast-catalog-v2.json \
  --map .agent-work/cache/maps/noaa-enc-east-coast-fullcoast-map-v2.json \
  --output-root .agent-work/outputs/a26-pilot \
  --select-only

uv run python tools/run_accuracy_pilot.py \
  --trial-table .agent-work/outputs/a38-corrected-dev/plan/trials.csv \
  --preparation-manifest .agent-work/outputs/a38-corrected-dev/inputs/development/preparation.csv \
  --input-root .agent-work/outputs/a38-corrected-dev/inputs \
  --catalog .agent-work/cache/maps/noaa-enc-east-coast-fullcoast-catalog-v2.json \
  --map .agent-work/cache/maps/noaa-enc-east-coast-fullcoast-map-v2.json \
  --output-root .agent-work/outputs/a26-pilot
```

The current configuration is a feasibility probe. The small budgets deliberately expose proposal and scoring caps. The 10 km full-domain rectangular grid contains more than 20,000 cells, so even a complete 20,000-candidate budget must remain marked budget-exhausted. Per-run records distinguish algorithm-reported proposal-generation counts from scorer-generated/evaluated/retained telemetry. The CLI currently times the combined candidate-search stage, so 20,000-candidate runtime projections are labeled linear combined-stage extrapolations and do not claim a separate proposal-generation or scoring latency. They do not change the release support threshold, eligibility rules, or confidence-interval population.
