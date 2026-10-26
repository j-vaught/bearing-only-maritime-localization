# A14 visibility screen

Implemented `bearing_localization.search.visibility_screen.screen_candidate_visibility` as an exact line-of-sight screen over an immutable `PreparedPredictionContext`. It uses the context spatial index, WGS84 geodesic range and initial bearing, calibrated visible-feature offsets, inclusive range configuration, and shared circular angular grouping.

The screen groups all in-range physical targets before checking occlusion. The shared grouping algorithm partitions bearings at circular gaps larger than the merge threshold. Removing occluded members can only enlarge gaps between retained bearings, so distinct original components cannot merge. Each component with one exact clear member therefore certifies one distinct surviving visible group. A requested lower bound may stop ray checks early. `certified` indicates that the reported integer K is valid, while `complete` indicates every applicable in-range target was resolved.

Component evidence includes original group members, exact ray states, the confirmed clear key, unresolved keys, and unexamined keys. Unsupported feature offsets or unknown observer/LOS coverage produce an explicit unresolved reason and invalidate certification. No truth or target cap is used.

Validation passed. The exhaustive tiny angular fixture enumerated every occlusion subset, including circular north wrap, bridge-connected bearings, duplicate bearings, and a separate component. Full scan output matched scalar prediction group count on a blocked foreground/background and wraparound fixture. Early-stop, unknown LOS, unsupported model metadata, unknown observer with empty catalog, and shoreline endpoint coverage cases passed. Focused suite result was 38 passed across the new screen tests and existing A14, angular grouping, and visibility tests. Ruff formatting/check and ty check passed.

This implementation was not benchmarked and does not change search/scoring integration.

## Follow-up metadata certificate gate

The screen now uses a versioned explicit allowlist for top-level model and inert metadata keys. It accepts the complete observed top-level key set from the frozen 66,668-record catalog, including AIS `identity_kind`, `motion_status`, `motion_valid`, and `position_interpolated`, along with ENC identity, linkage, source and uncertainty keys. It treats provenance as inert because provenance is a separate CatalogV2 field. It allows A34 source-component rows only with the known seven-field schema, including the stored source longitude and latitude. It scans nested source attributes for model-like fields. The common ENC `height` field is ignored only when empty; nonempty values are unsupported dimensional models. This policy does not modify record coordinates or prediction outputs.

On the frozen catalog, all 18 observed top-level metadata keys are explicitly recognized. The screen classifies 6,597 records as unsupported because they have a nonempty nested source-attribute `height`. The other 60,071 records are metadata-supported. In particular, no records are rejected for AIS motion/identity metadata or source-component coordinate provenance.

Added regressions for adversarial offset/shift keys, unknown top-level metadata, nested dimensions, A34-shaped resolved source-component metadata, AIS motion metadata, supported visible-feature offsets, and empty versus nonempty ENC height. Focused validation now passes 48 tests across screen, A14 predictor, shared grouping and search visibility. Ruff and ty pass. This follow-up is a certificate-safety gate only. The predictor and scorer remain unchanged.
