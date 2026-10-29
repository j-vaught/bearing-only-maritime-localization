"""Detached cache-only resident run with health, frozen selection and evaluation."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from shutil import disk_usage

from maritime.search.gpu.resident.cache import file_digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    job = args.job.resolve()
    os.chdir(root)

    def write(name, value):
        path = job / name
        temporary = path.with_suffix(path.suffix + ".pending")
        temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
        temporary.replace(path)

    def status(stage, **fields):
        value = {"utc": datetime.now(UTC).isoformat(), "stage": stage, **fields}
        write("status.json", value)
        print(json.dumps(value), flush=True)

    def run_step(argv, log_name):
        with (job / log_name).open("w") as stream:
            result = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f"Step failed ({result.returncode}); see {log_name}")

    try:
        old = root / "runs/resident-charleston-refining-20261028-job"
        proof = json.loads((old / "cache-verification.json").read_text())
        archive = Path(proof["cache_path"])
        base = archive.parent
        objects = base.resolve().parent / "resident-aid-objects"
        protocol = json.loads((job / "protocol-before-full-search.json").read_text())
        config = json.loads((job / "full-settings.json").read_text())
        assert config["search"]["devices"] == [0, 1, 2]
        assert file_digest(Path(config["measurements"])) == protocol["measurement_sha256"]
        assert not any("truth" in key or "observer" in key for key in config)
        before = {
            "key": proof["cache_key"],
            "sha256": file_digest(archive),
            "stat": [archive.stat().st_size, archive.stat().st_mtime_ns],
        }
        write("base-cache-before.json", before)
        argv = [
            sys.executable,
            "-m",
            "tools.run_profiled_resident",
            "--profile-dir",
            str(job / "full-profile"),
            "--profile-every",
            "10",
            "--execute",
            "--optimized",
            "--settings",
            str(job / "full-settings.json"),
            "--cache-dir",
            str(base),
            "--object-cache-dir",
            str(objects),
            "--output",
            str(job / "search"),
        ]
        write(
            "full-launch.json",
            {
                "argv": argv,
                "settings_sha256": file_digest(job / "full-settings.json"),
                "selector_sha256": file_digest(root / "tools/select_resident_result.py"),
                "truth_passed_to_solver": False,
                "started_utc": datetime.now(UTC).isoformat(),
            },
        )
        with (job / "search.log").open("w") as log:
            process = subprocess.Popen(
                argv, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
            )
            (job / "search.pid").write_text(str(process.pid) + "\n")
            status("full_search_running", pid=process.pid, devices=[0, 1, 2])
            while process.poll() is None:
                free = disk_usage(job).free
                health = {
                    "utc": datetime.now(UTC).isoformat(),
                    "pid": process.pid,
                    "free_disk_bytes": free,
                    "clock_ticks_per_second": os.sysconf("SC_CLK_TCK"),
                }
                for name in ("status", "io", "stat"):
                    path = Path(f"/proc/{process.pid}/{name}")
                    if path.exists():
                        health["process_" + name] = path.read_text()
                gpu = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=index,utilization.gpu,memory.used,power.draw",
                        "--format=csv,noheader",
                    ],
                    capture_output=True,
                    text=True,
                )
                health["gpu_readings"] = gpu.stdout.strip()
                with (job / "health.jsonl").open("a") as stream:
                    stream.write(json.dumps(health) + "\n")
                if free < 20 * 1024**3:
                    os.killpg(process.pid, signal.SIGTERM)
                    raise RuntimeError(
                        "Stopped this search because free HDD space fell below 20 GiB"
                    )
                time.sleep(10)
            if process.returncode:
                raise RuntimeError(f"Search exited {process.returncode}; see search.log")
        summary = json.loads((job / "search/summary.json").read_text())
        if not summary["complete"]:
            raise RuntimeError("Search ended with an incomplete domain")
        status("selecting_frozen_estimate", summary=summary)
        launch = json.loads((job / "full-launch.json").read_text())
        assert file_digest(root / "tools/select_resident_result.py") == launch["selector_sha256"]
        run_step(
            [
                sys.executable,
                "tools/select_resident_result.py",
                str(job / "search"),
                str(job / "estimate-before-truth.json"),
                "--device",
                "0",
            ],
            "selection.log",
        )
        write(
            "estimate-freeze.json",
            {
                "estimate_sha256": file_digest(job / "estimate-before-truth.json"),
                "selector_source_sha256": launch["selector_sha256"],
                "frozen_utc": datetime.now(UTC).isoformat(),
                "evaluation_truth_read": False,
            },
        )
        status("evaluating_frozen_estimate")
        run_step(
            [
                sys.executable,
                "tools/evaluate_frozen_resident_position.py",
                str(job / "estimate-before-truth.json"),
                str(job / "estimate-freeze.json"),
                str(job / "evaluation/truth.json"),
                str(job / "evaluation/accuracy.json"),
            ],
            "evaluation.log",
        )
        after = {
            "sha256": file_digest(archive),
            "stat": [archive.stat().st_size, archive.stat().st_mtime_ns],
        }
        assert after["sha256"] == before["sha256"] and after["stat"] == before["stat"]
        write("base-cache-after.json", {**after, "unchanged": True})
        accuracy = json.loads((job / "evaluation/accuracy.json").read_text())
        status("complete", position_error_m=accuracy["position_error_m"], summary=summary)
    except BaseException as error:
        status("failed", error=repr(error))
        raise


if __name__ == "__main__":
    main()
