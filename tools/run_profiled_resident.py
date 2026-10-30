"""Cache-only optimized resident search with saved stage and host profiles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from threading import Lock, local
from time import perf_counter

from maritime.search.gpu.resident import engine
from maritime.search.gpu.resident.profiling import ProfiledResidentDevice
from tools.run_cached_resident_only import main as cache_only_main


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument("--profile-dir", type=Path, required=True)
    parser.add_argument("--profile-every", type=int, default=1)
    parser.add_argument("--host-profile", action="store_true")
    parser.add_argument("--nvtx", action="store_true")
    options, remaining = parser.parse_known_args()
    if options.profile_every < 1:
        parser.error("profile-every must be positive")
    if "--execute" in remaining and "--optimized" not in remaining:
        parser.error("This profiling runner requires the optimized path")
    options.profile_dir.mkdir(parents=True, exist_ok=True)
    sys.argv = [sys.argv[0], *remaining]
    devices = []
    registry_lock, owner = Lock(), local()
    original_run, original_write = engine.run_resident_search, engine._atomic_npz

    def factory(world, config, device):
        resident = ProfiledResidentDevice(
            world,
            config,
            device,
            profile_dir=options.profile_dir,
            sample_every=options.profile_every,
            host_profile=options.host_profile,
            nvtx=options.nvtx,
        )
        owner.device = device
        with registry_lock:
            devices.append(resident)
        return resident

    def write_output(path, arrays):
        start = perf_counter()
        original_write(path, arrays)
        record = {
            "device": getattr(owner, "device", None),
            "operation": "frontier_write" if path.name == "frontier.npz" else "terminal_write",
            "host_wall_s": perf_counter() - start,
            "payload_bytes": sum(array.nbytes for array in arrays.values()),
            "path": str(path),
            "transfer_included": False,
        }
        with registry_lock:
            with (options.profile_dir / "output-writes.jsonl").open("a") as stream:
                stream.write(json.dumps(record) + "\n")

    def run(*args, **kwargs):
        kwargs["device_factory"] = factory
        started = perf_counter()
        try:
            return original_run(*args, **kwargs)
        finally:
            summaries = [device.finish_profile() for device in devices]
            metadata = {
                "wall_including_profile_finish_s": perf_counter() - started,
                "sample_every_batches": options.profile_every,
                "host_profile_enabled": options.host_profile,
                "device_profiles": summaries,
                "interpretation": (
                    "CUDA event stream spans include host launch gaps; "
                    "nested spans are not additive; native array kernels "
                    "may be outside custom kernel counters"
                ),
            }
            (options.profile_dir / "run-profile.json").write_text(
                json.dumps(metadata, indent=2, default=str) + "\n"
            )

    setattr(engine, "run_resident_search", run)
    setattr(engine, "_atomic_npz", write_output)
    cache_only_main()


if __name__ == "__main__":
    main()
