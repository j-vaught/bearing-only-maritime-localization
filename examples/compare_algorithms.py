"""Compare algorithms on exactly the same detections and full search domain."""

from _common import comparison, output_directory, search, simulate
from _data import CatalogMode

CATALOG_MODE: CatalogMode = "both"  # "ais", "chart_aids", or "both".
OBSERVER_ID = "B1"
RANGE_M = 10_000.0
SEED = 11
ALGORITHMS = ("grid", "hierarchical", "randomized", "pairwise", "wedge_geodesic_seeded")
CANDIDATE_BUDGET = 256
GRID_SPACING_M = 25_000.0
USE_TYPES = True
REFINE = True

if __name__ == "__main__":
    output = output_directory("compare_algorithms")
    frame = simulate(output, observer_id=OBSERVER_ID, range_m=RANGE_M, seed=SEED)
    rows = [
        search(
            frame,
            catalog_mode=CATALOG_MODE,
            name=algorithm,
            algorithm=algorithm,
            candidate_budget=CANDIDATE_BUDGET,
            spacing_m=GRID_SPACING_M,
            use_types=USE_TYPES,
            refine=REFINE,
        )
        for algorithm in ALGORITHMS
    ]
    comparison(output, rows)
