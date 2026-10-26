"""Change detection range while keeping the full catalog and search domain."""

from _common import comparison, output_directory, search, simulate
from _data import CatalogMode

CATALOG_MODE: CatalogMode = "both"  # "ais", "chart_aids", or "both".
OBSERVER_ID = "B1"
RANGES_M = (1_000.0, 5_000.0, 10_000.0)
SEED = 11
ALGORITHM = "wedge_geodesic_seeded"
CANDIDATE_BUDGET = 256
REFINE = True

if __name__ == "__main__":
    output = output_directory("compare_ranges")
    rows = []
    for radius in RANGES_M:
        frame = simulate(
            output / f"range_{int(radius)}m",
            observer_id=OBSERVER_ID,
            range_m=radius,
            seed=SEED,
        )
        rows.append(
            search(
                frame,
                catalog_mode=CATALOG_MODE,
                name=ALGORITHM,
                algorithm=ALGORITHM,
                candidate_budget=CANDIDATE_BUDGET,
                refine=REFINE,
            )
        )
    comparison(output, rows)
