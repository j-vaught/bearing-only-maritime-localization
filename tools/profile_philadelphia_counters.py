"""Profile Philadelphia after the completed eligible-site timing suite. J.C. Vaught."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SECTIONS = (
    "SpeedOfLight",
    "ComputeWorkloadAnalysis",
    "MemoryWorkloadAnalysis",
    "LaunchStats",
    "Occupancy",
    "SchedulerStats",
    "WarpStateStats",
    "InstructionStats",
    "SpeedOfLight_RooflineChart",
)


def read(path: Path):
    return json.loads(path.read_text())


def save(path: Path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def fingerprint(path: str):
    p = Path(path)
    h = hashlib.sha256()
    with p.open("rb") as stream:
        while chunk := stream.read(8 * 1024**2):
            h.update(chunk)
    return {"sha256": h.hexdigest(), "size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.execute:
        print(
            json.dumps(
                {
                    "executed": False,
                    "site": "B11",
                    "kernel": "pp_pairs",
                    "captured_launches": 3,
                    "skipped_matching_launches": 10,
                    "devices": [0, 1, 2],
                    "sections": SECTIONS,
                }
            )
        )
        return
    os.chdir(REPO)
    launch = read(REPO / ".agent-work/dynamic-all-eligible-launch.json")
    suite = Path(launch["output"])
    assert read(suite / "status.json")["stage"] == "finished"
    prior = read(suite / "results.json")
    assert len(prior) == 20 and all(r["terminal_outputs_identical"] for r in prior)
    before = read(suite / "cache-after.json")
    assert before == read(suite / "cache-before.json")
    assert before == {name: fingerprint(name) for name in before}
    installed = read(REPO / ".agent-work/nsight-compute-install.json")
    ncu = Path(installed["ncu"])
    assert ncu.is_file()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or REPO / "runs" / f"philadelphia-hardware-counters-{stamp}"
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    argv = [
        str(ncu),
        "--target-processes",
        "all",
        "--devices",
        "0,1,2",
        "--kernel-name-base",
        "function",
        "--kernel-name",
        "regex:^pp_pairs$",
        "--launch-skip",
        "10",
        "--launch-count",
        "3",
        "--replay-mode",
        "kernel",
        "--clock-control",
        "none",
        "--cache-control",
        "none",
    ]
    for section in SECTIONS:
        argv.extend(["--section", section])
    argv.extend(
        [
            "--export",
            str(output / "philadelphia"),
            str(REPO / ".venv/bin/python"),
            "-u",
            str(REPO / "tools/run_dynamic_eligible.py"),
            "--output",
            str(output / "trial"),
            "--site",
            "B11",
            "--no-warmup",
            "--hardware-profile",
            "--execute",
        ]
    )
    save(
        output / "protocol.json",
        {
            "source_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            "argv": argv,
            "timing_suite": str(suite),
            "suite_completed_before_profiling": True,
            "uid": os.geteuid(),
            "sections": SECTIONS,
            "normal_search_timing": False,
            "cache_control": "none",
            "clock_control": "none",
            "driver_parameters": Path("/proc/driver/nvidia/params").read_text(),
            "profiler_install": installed,
        },
    )
    save(output / "cache-before.json", before)
    save(output / "status.json", {"stage": "profiling"})
    save(
        REPO / ".agent-work/philadelphia-counter-launch.json",
        {"output": str(output), "pid": os.getpid(), "argv": argv},
    )
    print(json.dumps({"output": str(output), "stage": "profiling"}), flush=True)
    try:
        with (output / "profiler.log").open("w") as log:
            result = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT, check=False)
        text = (output / "profiler.log").read_text()
        blocked = "ERR_NVGPUCTRPERM" in text
        status = {
            "stage": "blocked_counter_permissions" if blocked else "failed",
            "returncode": result.returncode,
            "counters_collected": False,
        }
        if result.returncode == 0:
            records = read(output / "trial/results.json")
            assert len(records) == 1 and records[0]["site"] == "B11"
            assert records[0]["terminal_outputs_identical"]
            assert (output / "philadelphia.ncu-rep").is_file()
            export = subprocess.run(
                [
                    str(ncu),
                    "--import",
                    str(output / "philadelphia.ncu-rep"),
                    "--page",
                    "raw",
                    "--csv",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            (output / "hardware-metrics.csv").write_text(export.stdout)
            status = {
                "stage": "finished",
                "returncode": 0,
                "counters_collected": True,
                "accuracy_m": records[0]["position_error_m"],
                "terminal_outputs_identical": True,
            }
        save(output / "status.json", status)
        print(json.dumps(status), flush=True)
    finally:
        after = {name: fingerprint(name) for name in before}
        save(output / "cache-after.json", after)
        assert before == after, "Saved cache changed"


if __name__ == "__main__":
    main()
