# Accuracy map inputs and field replay

The packaged map inputs preserve the exact NOAA Electronic Navigational Chart (ENC) data used by the Eastern Seaboard accuracy work. The indexed inventory is in `data/maps/accuracy-inputs/manifest.json`. It records source, version, retrieval time, coverage status, original byte counts, SHA-256 hashes, archive hashes, and the provenance manifest hash. NOAA ENC Direct provides the chart source, and NOAA attribution is retained in the inventory and bibliography. The coast collection has incomplete scale bands, nineteen port maps have known coverage, and five port maps have partial coverage. The derived coast mosaic remains partial and marks unknown regions explicitly.

Each compressed archive is below 100 MB. The 268.6 MB GeoJSON collection compresses to 63.7 MB (60.8 MiB), the partial coast MapV2 compresses to 23.1 MB (22.0 MiB), and the 24 port maps compress to 19.0 MB (18.1 MiB) as one tar archive. These archives preserve original bytes and filenames. The archive index preserves the SHA-256 of every uncompressed input and every archive.

## Verify and restore map files

Run the verifier from the repository root. It checks archive hashes, decompresses every payload as a streaming operation, checks the original payload hashes and sizes, and validates the nineteen-known/five-partial coverage index.

```sh
uv run python tools/package_accuracy_map_inputs.py
```

To restore the archived files into a map cache directory, use the following commands and replace `CACHE_DIR` with the cache path configured for the local checkout. The collection and mosaic retain their original filenames, and the port archive contains `noaa-enc-port-B1-15km.map-v2.json` through `noaa-enc-port-B24-15km.map-v2.json`.

```sh
CACHE_DIR=/path/to/map-cache
mkdir -p "$CACHE_DIR"
gzip -dc data/maps/accuracy-inputs/noaa-enc-east-coast-24-ports.geojson.gz > "$CACHE_DIR/noaa-enc-east-coast-24-ports.geojson"
gzip -dc data/maps/accuracy-inputs/noaa-enc-east-coast-fullcoast-map-v2.json.gz > "$CACHE_DIR/noaa-enc-east-coast-fullcoast-map-v2.json"
tar -xzf data/maps/accuracy-inputs/noaa-enc-port-B1-B24-15km-maps.tar.gz -C "$CACHE_DIR"
```

After extraction, rerun the verifier to validate the committed archives. The checksum inventory also supplies the expected hashes for extracted files. The committed raw AIS and chart-aid CSV files remain the experiment inputs; this package adds the exact ENC source collection and map outputs without duplicating those catalogs.

## Reproduction scope and limits

The map package supports reconstruction of the saved partial map inputs without contacting a live chart service. The exact service acquisition manifest is retained at `data/maps/accuracy-east-coast-enc.manifest.json`, while the compressed archives avoid dependence on its former server cache paths. The search experiment uses the full-coast catalog and full-coast map with explicit unknown coverage. The current spatial study uses a single AIS snapshot with mixed chart vintages and partial ENC coverage, so its map results do not establish continuous coast coverage or field accuracy.

## Field replay interface and collection protocol

The field replay interface accepts a version 2 measurement set through the search command's `--measurements` input. Replay also supplies the full-coast catalog, the full-coast map, a frozen configuration, the projected coordinate reference system, and a scope manifest. The search process receives the estimator-visible bearing measurements and map/catalog inputs. Independent truth, support diagnostics, and difficulty records remain evaluator inputs and are excluded from the search process.

For each field sequence, retain the original image or video timestamps and the independently measured observer position, observer heading, and sensor calibration records. Generate a timestamped bearing measurement for each detected feature with its angular uncertainty and any optional type label or angular extent supported by the input contract. Record observer pose and truth on a separate evaluator-only channel. Preserve acquisition time, sensor calibration version, feature-selection procedure, map/catalog hashes, and the full command configuration with every replay. Use independent position truth, such as surveyed or RTK-backed ground truth, to evaluate the resulting estimate. Do not substitute AIS positions for observer truth or report synthetic bearings as field measurements.

The current evidence is a synthetic spatial evaluation using one AIS snapshot, mixed chart vintages, and incomplete coast coverage. Field trials using measured bearings and independent truth, including correct-port and wrong-port examples, remain unvalidated until those observations are collected and evaluated. Benchmark outcome tables, ambiguity examples, difficulty strata, scaling plots, and error summaries are added only after the finalized trial outputs and independent review are available.

## Versioned MapV3 benchmark input

The exact MapV3 candidate payload is preserved at `data/maps/accuracy-inputs-v3/noaa-enc-east-coast-fullcoast-map-v3-candidate.json.gz`. Its descriptor at `data/maps/accuracy-inputs-v3/benchmark-input.json` identifies the artifact as revision 3 of the MapV2 contract for new benchmark configurations. Existing MapV2 inputs and defaults remain available as the historical comparison. The source validation manifest is preserved beside the archive, and the package manifest records the original payload hash, compressed archive hash, source manifests, and NOAA ENC Direct attribution. The source manifests do not provide a separate license field.

Restore and verify the exact 56,326,616-byte payload with the following commands. The extracted map hash is `cce049c5911b39072d30f26c09947782a78e350e65a3f69c6923312fba3f9d93`, and the compressed archive hash is `fda522364582a20d74a643e7e854844b2ce2490a45e469cb210fd9a8babd13de`.

```sh
CACHE_DIR=/path/to/map-cache
mkdir -p "$CACHE_DIR"
gzip -dc data/maps/accuracy-inputs-v3/noaa-enc-east-coast-fullcoast-map-v3-candidate.json.gz > "$CACHE_DIR/noaa-enc-east-coast-fullcoast-map-v3-candidate.json"
test "$(wc -c < "$CACHE_DIR/noaa-enc-east-coast-fullcoast-map-v3-candidate.json")" -eq 56326616
printf '%s  %s\n' cce049c5911b39072d30f26c09947782a78e350e65a3f69c6923312fba3f9d93 "$CACHE_DIR/noaa-enc-east-coast-fullcoast-map-v3-candidate.json" | sha256sum -c -
sha256sum data/maps/accuracy-inputs-v3/noaa-enc-east-coast-fullcoast-map-v3-candidate.json.gz
```

The MapV3 coverage comparison measures both versions over the same 3,383,691.331 km² bounding box using EPSG:6933 equal-area projection. The previous port-only MapV2 has 0.482% known area. The validated MapV3 candidate has 76.260149% known area and 23.739851% explicitly unknown area. The 20-map Harbour detail union covers 10,500.268 km², or 0.310320% of the domain, wholly within the coarse known region. It is a higher-resolution overlay and does not add to the known-area total. All 24 port maps were byte-verified; no Approach or Berthing maps contribute detail. This chart-derived map remains partial and does not establish navigable depth or field accuracy.

The equal-area coverage comparison is rendered in `docs/figures/accuracy-map-coverage-2026-10-26.pdf` and `docs/figures/accuracy-map-coverage-2026-10-26.png`. Its source data and Typst/CeTZ/Lilaq source are adjacent to those outputs.
