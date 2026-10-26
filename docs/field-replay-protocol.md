# Field replay protocol

## Scope and current status

This protocol describes a reproducible path from timestamped visual bearings to a truth-free version 2 (V2) search and separate evaluation. Search receives measurements, a time-aligned catalog, a map, a serialized SearchConfig, and a projected coordinate reference system. It has no observer-pose or truth input. The repository contains the input contracts and commands, but no measured-bearing field dataset or field-accuracy result. Field accuracy remains unvalidated until measured observations are paired with independently collected evaluator truth.

Contracts are in `maritime_localization_shared/contracts/inputs_v2.py`. Search output is `LocalizationResult` in `maritime_localization_shared/contracts/localization.py`; evaluator truth is `EvaluationTruth` in `maritime_localization_shared/contracts/truth.py`; evaluator support is `SupportManifest` in `bearing_localization/evaluation/metrics.py`. The V1-to-V2 bearing adapter is `measurement_set_v2_from_observations` in `maritime_localization_ingest/measurements.py`. AIS normalization uses `catalog_from_ais_rows` and `read_ais_csv` in `maritime_localization_ingest/ais.py`.

## Collection and calibration

Capture one timestamped report set per observer pose and acquisition epoch. Preserve each original sensor frame time in ISO 8601 UTC with an explicit timezone. Record sensor model and firmware, lens or optical settings, bearing readout method, heading source and time, calibration procedure and date, applied north correction, frame identifier, and feature-selection rule. Retain unmodified imagery and extraction records under access controls so observations can be regenerated.

Bearings are degrees clockwise from true north. Establish the sensor-to-true-north correction before collection, document its sign and uncertainty, and apply it exactly once to each `bearing_deg`. Keep the raw sensor-relative angle and correction separately in the controlled record. Do not substitute grid north or vessel heading for true north. Geospatial reference bearings use the WGS84 ellipsoid; projected coordinates support search neighborhoods and must use the declared meter-based CRS.

Enter each `sigma_deg` as one-standard-deviation angular uncertainty, supported by documented calibration or repeatability. V2 permits null or a nonnegative value. Search uses the configured generated sigma when a report value is absent and applies `sigma_floor_deg`; defaults are 0.5 and 0.05 degrees. The optional `angular_half_width_deg` is a distinct half-width for an unresolved angular group or extended feature, not a Gaussian standard deviation. Use it only for measured extent and preserve the edge-definition rule.

Common angular bias uses paired V2 fields `calibrated_common_bias_mean_deg` and `calibrated_common_bias_sigma_deg`, plus the corresponding `common_bearing_bias_prior_deg` config pair. Populate them only from independent sensor calibration and preserve its evidence and reference frame. Leave them unset without defensible calibration. This represents a shared sensor-reference offset, not a per-feature correction.

## Time alignment and catalog preparation

Use actual acquisition time for every visual report and catalog epoch. Although V2 reports carry timestamps, standalone search does not synchronize reports or reject stale ones. For a moving observer, collect each report set at one pose epoch or compensate using independently recorded pose and timing before serialization. Do not treat asynchronous bearings as simultaneous at one pose.

AIS inputs require source timestamps and WGS84 positions. The adapter performs two-sided geodesic interpolation only when the bracket is at most 60 seconds by default. It records source times and a `motion_status`. Values include `reported_at_target`, `interpolated`, `stale_exact_report`, `stale_bracket_too_wide`, and `stale_extrapolation_disabled`. Extrapolation is unsupported. The adapter does not choose a trial freshness limit. Set that limit before examining outcomes, retain statuses and source times, and exclude or qualify records outside it.

The `observer_id` adapter argument can remove the observer's own AIS report during catalog preparation. Keep it local and out of measurement documents. AIS keys and provenance may contain MMSIs, so secure normalized catalogs and redact identifiers from public examples. Use pseudonymous trial identifiers and protect the identity mapping separately.

This command uses the existing CSV adapter and contract writer. Replace source and target epoch with frozen trial values. Preserve the original CSV and manifest.

```sh
uv run python - <<'PY'
from maritime_localization_ingest.ais import read_ais_csv
from maritime_localization_shared.serialization import write_contract

catalog = read_ais_csv(
    "data/ais/chesapeake_2023-07-15T1200Z.csv",
    target_time_utc="2023-07-15T12:00:30Z",
    observer_id=None,
    max_bracket_seconds=60.0,
)
write_contract("replay/catalog-v2.json", catalog)
PY
```

Record source catalog filename and SHA-256, manifest, target epoch, report window, interpolation limit, adapter version, and counts of interpolated and stale records. Preserve source and normalized catalogs. One AIS snapshot is not continuous vessel history.

## Map and coordinate provenance

Choose the map version before a trial and preserve its descriptor, source-validation manifest, archive hash, and extracted-payload hash. The approved versioned full-coast map is described by `data/maps/accuracy-inputs-v3/benchmark-input.json`; its package inventory is `data/maps/accuracy-inputs-v3/manifest.json`. It is approved for new benchmark inputs and is not the default map. Its source-validation manifest identifies NOAA ENC Direct, partial coverage, and explicit unknown regions. The package states that upstream manifests have no separate license field. Follow the source provider's terms when distributing derived inputs.

Restore the map from the repository root and verify its payload size and hash.

```sh
mkdir -p replay
gzip -dc data/maps/accuracy-inputs-v3/noaa-enc-east-coast-fullcoast-map-v3-candidate.json.gz > replay/map-v3.json
test "$(wc -c < replay/map-v3.json)" -eq 56326616
printf '%s  %s\n' cce049c5911b39072d30f26c09947782a78e350e65a3f69c6923312fba3f9d93 replay/map-v3.json | sha256sum -c -
```

The map distinguishes land, known coverage, and unknown coverage. Unknown is not water. Preserve map version and coverage in every replay record. The chart-derived surface is not a bathymetric or navigability model. Keep search bounds, `--projected-crs`, catalog transformation, and evaluator CRS consistent. Record transformation library versions and exact projected-meter bounds.

## Estimator and evaluator handoff

Serialize one `MeasurementSetV2` per trial. Each `measurements` record contains `bearing_deg`, `timestamp_utc`, and measured `sigma_deg`, with optional `object_type` and `angular_half_width_deg`. A set can carry a pseudonymous `trial_id` and independently calibrated common-bias values. Exclude observer coordinates, heading, target identities, ranges, AIS associations, truth associations, and support labels. Config must be strict `SearchConfig` JSON with `contract_type` set to `SearchConfig`, frozen bounds, range, objective, type use, and backend.

Run truth-free V2 search from the repository root after preparing the four JSON inputs.

```sh
uv sync --locked --all-groups
uv run bearing-localization-search --schema-version 2 \
  --measurements replay/measurements-v2.json \
  --catalog replay/catalog-v2.json \
  --map replay/map-v3.json \
  --config replay/search-config.json \
  --projected-crs EPSG:32618 \
  --algorithm pairwise \
  --output replay/estimate-v2.json
```

Keep independently measured observer position and heading truth in an evaluator-only file. Store truth in the result CRS and record its measurement method. Object identity links belong only in protected evaluator records. Truth heading is not a search input. Derive support before search from surveyed water status and independent visual-to-catalog review, then store it separately as `SupportManifest`. Do not infer physical support or true associations from search output.

After freezing the estimate, run the V2 evaluator. Its CRS must match estimate and truth.

```sh
uv run bearing-localization-evaluate --schema-version 2 \
  --estimate replay/estimate-v2.json \
  --truth protected/truth-v2.json \
  --support protected/support-v2.json \
  --coordinate-crs EPSG:32618 \
  --output protected/evaluation-v2.json
```

Protect imagery, AIS identifiers, observer identity and position, truth associations, and support records. Apply access controls before collection, use pseudonymous IDs in reproducibility packages, and publish only reviewed aggregates or examples. Preserve private-input hashes and collection metadata in an access-controlled manifest so authorized reviewers can verify a replay without the identity mapping.

## Reproducibility and evidence boundary

Preserve measurement, catalog, map, config, estimate, evaluator truth, support, evaluation output, command line, software commit, dependency lock, CRS, calibration record, AIS and map manifests, and SHA-256 hashes. Record rejected reports and the predeclared freshness rule. Reproduction uses identical input bytes and command.

The repository provides versioned contracts, truth-free V2 search and evaluator commands, and a small AIS normalization example in `tests/accuracy/test_a07.py`. It contains no measured-bearing field validation. A software replay alone is not evidence of field accuracy; report measured-field accuracy only after independent truth and evaluator results have been reviewed.
