# A14 trusted pre-LOS target preparation

## API and source binding

`bearing_localization.search.pre_los_preparation.prepare_pre_los_targets(scorer, candidate_xy_m)` is the trusted production extractor. It accepts the existing `CandidateScorer` and a candidate position, then uses the scorer's catalog, WGS84 spatial index, validated `PreparedPredictionContext`, `VectorMap`, and canonical `SearchConfig`. The caller cannot supply a hand-built target list, bearing, range, object type, uncertainty, or map coordinate.

The factory returns a frozen `PreparedPreLOSTargets` bundle. It carries the candidate coordinates, ordered complete physical target records, a typed A11 candidate coverage certificate, the prepared map context, explicit support and reason fields, unsupported nearby model keys, and identity bindings to the scorer, catalog, map, prediction context, measurements, and configuration. `matches_scorer` and `matches_inputs` allow bound consumers to check those bindings. The bundle constructor does not expose a public initializer.

A11 certificate preparation is cached once per scorer using an identity-keyed weak cache. Because `CandidateScorer` retains only its `VectorMap` and not the source `MapV2`, the certificate snapshot is derived from that exact vector map's coverage, unknown, and effective-land geometries, then projected to the same map CRS. The bundle is also bound to the exact source `VectorMap` object. This does not claim the source chart's metadata identity; it binds the geometric snapshot used by the scorer.

## Exact pre-LOS rules

The extractor uses the same geographic query radius as prediction, `max_range_m + max_offset_m`, and consumes the index's exact inverse-geodesic results. It skips virtual/nonvisual records. It detects unsupported physical feature models for all nearby index results before applying the actual target range gate, matching the exact predictor's ordering. Calibrated east/north offsets use the same WGS84 forward offset and inverse geometry; zero-offset records reuse the exact index bearing and distance. The configured inclusive or exclusive range endpoints, normalized type labels, position uncertainty, target projection, and stable catalog ordering are retained in each target record.

The extractor performs no candidate-to-target land or line-of-sight test. It asks the A11 certificate to prove that the candidate and the complete target-coordinate set lie inside known map coverage and avoid unknown coverage. Unsupported model records, a nonwater candidate, missing coverage context, invalid transformed candidate, or failed envelope proof mark the bundle unsupported. Computed target records are retained when later support checks fail. The bundle never converts unknown geometry into clear visibility.

## Verification

Python 3.11.15.

`uv sync --locked --all-groups` passed. Scoped Ruff checks and formatting passed. Scoped `ty check` passed. The new preparation tests, existing A14 prediction tests, and A11 coverage-certificate tests passed together: 34 passed. Cases cover true-north wraparound, nearby merged members, offset-corrected geometry and uncertainty, virtual-aid exclusion, out-of-range rejection, exact inclusive/exclusive range boundaries, duplicate coordinates with distinct keys, empty catalogs, nearby unsupported models, nonwater observers, unknown coverage, bundle immutability, and scorer identity mismatch.

No scorer, predictor, controller, or objective code was changed. The bound integration remains a separate change; consumers must reject unsupported bundles and verify the bound's measurement/configuration inputs against the bundle before use.
