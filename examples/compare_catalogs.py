"""Compare available catalogs while preserving the same full-world observations."""

from _common import comparison, output_directory, search, simulate
from _data import CatalogMode

OBSERVER_ID = "B1"
RANGE_M = 10_000.0
SEED = 11
ALGORITHM = "wedge_geodesic_seeded"
CATALOG_MODES: tuple[CatalogMode, ...] = ("ais", "chart_aids", "both")
CANDIDATE_BUDGET = 256
USE_TYPES = True
REFINE = True

if __name__ == "__main__":
    output = output_directory("compare_catalogs")
    # Simulate once with both sources present. Do not remove uncataloged detections.
    frame = simulate(output, observer_id=OBSERVER_ID, range_m=RANGE_M, seed=SEED)
    rows = [
        search(
            frame,
            name=mode,
            algorithm=ALGORITHM,
            catalog_mode=mode,
            candidate_budget=CANDIDATE_BUDGET,
            use_types=USE_TYPES,
            refine=REFINE,
        )
        for mode in CATALOG_MODES
    ]
    comparison(output, rows)
