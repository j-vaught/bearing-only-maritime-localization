"""Compare labels on/off without regenerating bearings or physical detections."""

from _common import comparison, output_directory, search, simulate
from _data import CatalogMode

CATALOG_MODE: CatalogMode = "both"  # "ais", "chart_aids", or "both".
OBSERVER_ID = "B1"
RANGE_M = 10_000.0
SEED = 11
ALGORITHM = "wedge_geodesic_seeded"
CANDIDATE_BUDGET = 256
REFINE = True

if __name__ == "__main__":
    output = output_directory("compare_types")
    frame = simulate(output, observer_id=OBSERVER_ID, range_m=RANGE_M, seed=SEED)
    rows = [
        search(
            frame,
            catalog_mode=CATALOG_MODE,
            name="types_on" if enabled else "types_off",
            algorithm=ALGORITHM,
            use_types=enabled,
            candidate_budget=CANDIDATE_BUDGET,
            refine=REFINE,
        )
        for enabled in (True, False)
    ]
    comparison(output, rows)
