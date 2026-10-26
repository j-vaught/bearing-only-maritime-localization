"""GPU candidate scoring for the full-coast grid; CPU remains the score reference."""

from _common import comparison, output_directory, search, simulate

# Edit these settings, then run uv run python examples/gpu.py.
OBSERVER_ID = "B1"  # data/observer_locations.csv.
RANGE_M = 10_000.0
BEARING_SIGMA_DEG = 0.5
SEED = 11
USE_TYPES = True
CANDIDATE_BUDGET = 256
GRID_SPACING_M = 25_000.0
PROPOSAL_CELL_SIZE_M = 250.0
PROPOSAL_SOURCE_BUDGET = 333_340
REFINE = True
DEVICE_IDS = (0,)  # Change to (0, 1) or (0, 1, 2, 3) when available.

if __name__ == "__main__":
    output = output_directory("gpu")
    frame = simulate(
        output,
        observer_id=OBSERVER_ID,
        range_m=RANGE_M,
        sigma_deg=BEARING_SIGMA_DEG,
        seed=SEED,
    )
    result = search(
        frame,
        name="gpu",
        algorithm="grid",
        backend="gpu",
        use_types=USE_TYPES,
        candidate_budget=CANDIDATE_BUDGET,
        spacing_m=GRID_SPACING_M,
        proposal_cell_size_m=PROPOSAL_CELL_SIZE_M,
        proposal_source_budget=PROPOSAL_SOURCE_BUDGET,
        refine=REFINE,
        device_ids=DEVICE_IDS,
    )
    comparison(output, [result])
