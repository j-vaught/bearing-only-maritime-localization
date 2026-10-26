"""Replay saved synthetic bearings against the full catalog, without simulation.

The saved bearings have no evaluator truth in the current checkout, so their
position error is left null. Edit data/example-bearings.json for your own input.
"""

from _common import comparison, output_directory, replay, search

ALGORITHM = "wedge_geodesic_seeded"
RANGE_M = 10_000.0
CANDIDATE_BUDGET = 256
REFINE = True

if __name__ == "__main__":
    output = output_directory("replay")
    frame = replay(output, range_m=RANGE_M)
    result = search(
        frame,
        name=ALGORITHM,
        algorithm=ALGORITHM,
        use_types=False,
        candidate_budget=CANDIDATE_BUDGET,
        refine=REFINE,
    )
    comparison(output, [result])
