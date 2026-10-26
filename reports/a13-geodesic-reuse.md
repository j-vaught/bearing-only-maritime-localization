# A13 exact geodesic reuse

## Change

GeographicSpatialIndex now exposes query_radius_with_geometry. It returns rows of catalog index, exact WGS84 initial bearing in degrees, and exact WGS84 distance in meters. The query uses the existing ECEF chord prefilter, the same maximum-offset radius supplied by prediction, the same inclusive exact geodesic radius test, and sorted catalog order. The existing query_radius API remains available and delegates to the new query while returning only indices.

Prediction now carries each query result through the visibility checks. When both prepared feature offsets are exactly zero, it uses the already computed WGS84 bearing and distance. For calibrated nonzero offsets, prediction retains the forward geodesic offset and then computes the inverse geometry from observer to target. Unsupported offsets remain unresolved as before. Minimum and maximum range gates, line-of-sight checks, merging, and scoring inputs remain unchanged.

## Evidence

The index regression covers antimeridian proximity, distinct duplicate-coordinate catalog entries, stable catalog order, exact inverse bearing and distance values, and parity between the geometry query indices and query_radius. The prediction regression uses a north-wrap mixed-offset catalog. It verifies complete visible member keys and the bearing and range values for both records. Instrumentation confirms one prediction-side inverse helper call for the nonzero-offset physical record while the zero-offset record reuses query geometry.

The scoped validation used Python 3.11.15 after uv sync --locked --all-groups --python 3.11. Ruff format, Ruff check, ty check, and git diff --check passed. The scoped pytest run covered the new regression, A04, A13, A14, spatial-index, and pairwise-search tests. All 65 tests passed. These checks establish operation-count and functional behavior; no timing profile was run and no speed claim is made. Existing A14 tests also compare prepared and unprepared full PredictedGroupSet results. This change did not add a separate CandidateEvaluation score fixture.

## Boundaries

The exact inverse in radius filtering is still required to decide geodesic membership. The optimization removes the repeated prediction inverse only for returned physical records with exact zero east and north offsets. It does not alter range membership because both phases use the same observer-target coordinates and WGS84 inverse result. Calibrated offsets continue to use the prior two-step geometry. No changes were made to the frozen full-run source or package exports.

## Revision

Branch accuracy/a13-geodesic-reuse was created from accuracy/integration at 9f6c4ee. Commit SHA is recorded after push.
