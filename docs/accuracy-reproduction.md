# Accuracy map inputs and field replay

The packaged map inputs preserve the exact NOAA Electronic Navigational Chart (ENC) data used by the Eastern Seaboard accuracy work. The indexed inventory is in `data/maps/accuracy-inputs/manifest.json`. It records source, version, retrieval time, coverage status, original byte counts, SHA-256 hashes, archive hashes, and the provenance manifest hash. NOAA ENC Direct provides the chart source, and NOAA attribution is retained in the inventory and bibliography. The coast collection has incomplete scale bands, nineteen port maps have known coverage, and five port maps have partial coverage. The derived coast mosaic remains partial and marks unknown regions explicitly.

Each compressed archive is below 100 MB. The 269 MB GeoJSON collection compresses to about 61 MB, the partial coast MapV2 compresses to about 23 MB, and the 24 port maps compress to about 19 MB as one tar archive. These archives preserve original bytes and filenames. The archive index preserves the SHA-256 of every uncompressed input and every archive.

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

The map package supports reconstruction of the saved partial map inputs without contacting a live chart service. The exact service acquisition manifest is retained at `data/maps/accuracy-east-coast-enc.manifest.json`, while the compressed archives avoid dependence on its former server cache paths. The search experiment uses the full-coast catalog and full-coast map with explicit unknown coverage. The current spatial study uses a single chart epoch and partial ENC coverage, so its map results do not establish continuous coast coverage or field accuracy.

## Field replay interface and collection protocol

The field replay interface accepts a version 2 measurement set through the search command's `--measurements` input. Replay also supplies the full-coast catalog, the full-coast map, a frozen configuration, the projected coordinate reference system, and a scope manifest. The search process receives the estimator-visible bearing measurements and map/catalog inputs. Independent truth, support diagnostics, and difficulty records remain evaluator inputs and are excluded from the search process.

For each field sequence, retain the original image or video timestamps and the independently measured observer position, observer heading, and sensor calibration records. Generate a timestamped bearing measurement for each detected feature with its angular uncertainty and any optional type label or angular extent supported by the input contract. Record observer pose and truth on a separate evaluator-only channel. Preserve acquisition time, sensor calibration version, feature-selection procedure, map/catalog hashes, and the full command configuration with every replay. Use independent position truth, such as surveyed or RTK-backed ground truth, to evaluate the resulting estimate. Do not substitute AIS positions for observer truth or report synthetic bearings as field measurements.

The current evidence is a synthetic, single-epoch spatial evaluation with incomplete coast coverage. Field trials using measured bearings and independent truth, including correct-port and wrong-port examples, remain unvalidated until those observations are collected and evaluated. Benchmark outcome tables, ambiguity examples, difficulty strata, scaling plots, and error summaries are added only after the finalized trial outputs and independent review are available.
