# A26 Assignment Expansion Suite Wiring

The accuracy runner now carries assignment expansion as an explicit search option. The command template supplies the declared defaults for local radius, evaluation limit, and branch limit while leaving --expand-assignments absent. Existing suite runs therefore retain the prior search behavior.

When a command explicitly enables expansion, the runner adds the CLI request deadline from the suite timeout value. The same value enters the calibration context only for that enabled mode. The external process-group watchdog remains in place, so an expired internal request deadline is reported through the search output while the outer watchdog still bounds the whole command.

The CLI settings parser rejects nonfinite or nonpositive radii and deadlines, and rejects evaluation or branch limits outside the CLI contract. Numeric expansion options without the opt-in flag remain inactive. Tests verify stable fingerprints, opt-in behavior, exact suite-deadline propagation, malformed values, and the standalone search executable on a small synthetic input set with no truth file.

Validation passed with 21 focused A26 expansion tests and 24 combined A26/A32 tests. Ruff formatting, Ruff lint, and the targeted type check also passed. No development, validation, or held-out benchmark trials were run. The standalone CLI checks are synthetic plumbing tests and do not establish localization accuracy or runtime performance.

The suite remains calibration-ineligible when assignment expansion is enabled. Its telemetry identifies the expansion settings and reports pending modes when the request deadline stops work. A dedicated calibration population would be required before expansion results could support an acceptance decision.

A separate coarse-seed opt-in, `--expand-assignments-from-coarse`, starts the same bounded assignment search from coarse candidate modes and permits refinement to be disabled. It is mutually exclusive with `--expand-assignments`. The seed stage is included in the expansion fingerprint so refined-seed and coarse-seed executions remain distinct. The shared command template leaves both opt-ins disabled.
