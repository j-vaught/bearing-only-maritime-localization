"""Prepare compact drawing inputs from saved exports. Does not run the model."""

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def read(name):
    return json.loads((DATA / name).read_text())


def write(name, value):
    (DATA / name).write_text(json.dumps(value, separators=(",", ":")) + "\n")


def main():
    simulation = read("simulation.json")
    outcome = read("run-result.json")
    terminals = read("terminal-cells.json")
    clear = simulation["land_examples"]["clear"]
    frame = {
        "measurements": simulation["reports"],
        "target_xy": clear["target_en_m"],
        "true_north_xy": read("map-charleston.json")["metadata"]["north_ray"][1],
        "los_examples": [
            {"xy": item["target_en_m"], "blocked": key == "blocked", **item}
            for key, item in simulation["land_examples"].items()
        ],
        "merged_xy": [
            member["xy"] for member in simulation["merged_groups"][0]["members"]
        ],
        "merged_objects": [
            next(
                obj
                for obj in simulation["objects_in_range"]
                if obj["key"] == member["key"]
            )
            for member in simulation["merged_groups"][0]["members"]
        ],
        "source": "simulation.json; saved representative/report correspondences",
    }
    write("frame.json", frame)
    range_scenes = []
    for radius in (1000, 3000, 5000, 10000):
        inside = [
            o for o in simulation["objects_in_range"] if o["distance_m"] <= radius
        ]
        clear_objects = [o for o in inside if o["saved_visibility"] == "clear"]
        range_scenes.append(
            {
                "radius_m": radius,
                "in_range_count": len(inside),
                "visible_count": len(clear_objects),
                "aids": [o["xy"] for o in clear_objects if o["source"] == "chart"],
                "ais": [o["xy"] for o in clear_objects if o["source"] == "ais"],
            }
        )
    write("range-scenes.json", range_scenes)
    write(
        "result.json",
        {
            "estimate": terminals["estimate"],
            "truth": terminals["observer"],
            "terminals": terminals["cells"],
            "accuracy": outcome["accuracy"],
            "source": "terminal-cells.json; run-result.json",
        },
    )
    errors = [
        report["actual_error_deg"]
        for report in simulation["reports"]
        if report.get("actual_error_deg") is not None
    ]
    bins = [
        {"left": -0.5 + i * 0.1, "right": -0.4 + i * 0.1, "count": 0} for i in range(10)
    ]
    for error in errors:
        bins[min(9, max(0, int((error + 0.5) / 0.1)))]["count"] += 1
    write(
        "noise-histogram.json",
        {"bins": bins, "n": len(errors), "max_abs_deg": max(map(abs, errors))},
    )
    sigma = 1 / 6
    normalizer = math.erf(0.5 / sigma / math.sqrt(2))
    write(
        "noise-density.json",
        [
            [
                x / 1000,
                math.exp(-0.5 * (x / 1000 / sigma) ** 2)
                / (sigma * math.sqrt(2 * math.pi) * normalizer),
            ]
            for x in range(-500, 501, 5)
        ],
    )


if __name__ == "__main__":
    main()
