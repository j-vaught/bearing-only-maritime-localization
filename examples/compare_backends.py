"""Run CPU and GPU grid requests sequentially on the same observations."""

from _common import comparison, output_directory, search, simulate
from _data import CatalogMode

CATALOG_MODE: CatalogMode = "both"  # "ais", "chart_aids", or "both".
OBSERVER_ID = "B1"
RANGE_M = 10_000.0
SEED = 11
CANDIDATE_BUDGET = 256
GRID_SPACING_M = 25_000.0
DEVICE_IDS = (0,)
REFINE = True

if __name__ == "__main__":
    output = output_directory("compare_backends")
    frame = simulate(output, observer_id=OBSERVER_ID, range_m=RANGE_M, seed=SEED)
    cpu = search(
        frame,
        catalog_mode=CATALOG_MODE,
        name="cpu",
        algorithm="grid",
        backend="cpu",
        candidate_budget=CANDIDATE_BUDGET,
        spacing_m=GRID_SPACING_M,
        refine=REFINE,
    )
    gpu = search(
        frame,
        catalog_mode=CATALOG_MODE,
        name="gpu",
        algorithm="grid",
        backend="gpu",
        candidate_budget=CANDIDATE_BUDGET,
        spacing_m=GRID_SPACING_M,
        device_ids=DEVICE_IDS,
        refine=REFINE,
    )
    comparison(output, [cpu, gpu])
