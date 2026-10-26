"""Generate detections and separate truth from the complete shared dataset."""

from _common import output_directory, simulate

OBSERVER_ID = "B1"  # data/observer_locations.csv.
RANGE_M = 10_000.0
BEARING_SIGMA_DEG = 0.5
SEED = 11

if __name__ == "__main__":
    output = output_directory("simulate")
    simulate(
        output,
        observer_id=OBSERVER_ID,
        range_m=RANGE_M,
        sigma_deg=BEARING_SIGMA_DEG,
        seed=SEED,
    )
