"""Capture partial cProfile data for one bounded localization CLI run.

The watchdog writes the profile from a signal handler before terminating, so an
unfinished pairwise proposal stream still leaves inspectable diagnostics.
"""

from __future__ import annotations

import argparse
import cProfile
import json
import os
import pstats
import runpy
import signal
import sys
import time
from pathlib import Path
from typing import Any


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--profile-output", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.seconds <= 0:
        raise SystemExit("--seconds must be positive")
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command or command[0] not in {"search-cli", "module"}:
        raise SystemExit("command must start with search-cli or module")
    output = args.profile_output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    profile_path = output.with_suffix(".prof")
    summary_path = output.with_suffix(".txt")
    metadata_path = output.with_suffix(".json")
    profiler = cProfile.Profile()
    started = time.monotonic()
    state: dict[str, Any] = {"status": "running", "command": command}
    wrote = False

    def save_profile(status: str, error: str | None = None) -> None:
        nonlocal wrote
        if wrote:
            return
        profiler.disable()
        profiler.dump_stats(profile_path)
        with summary_path.open("w", encoding="utf-8") as stream:
            stats = pstats.Stats(profiler, stream=stream)
            stats.strip_dirs().sort_stats("cumulative").print_stats(80)
        state.update(
            {
                "status": status,
                "elapsed_s": time.monotonic() - started,
                "error_type": error,
                "profile_path": str(profile_path),
                "summary_path": str(summary_path),
            }
        )
        metadata_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        wrote = True

    def watchdog(_signum: int, _frame: Any) -> None:
        save_profile("watchdog_timeout", "TimeoutError")
        os._exit(124)

    signal.signal(signal.SIGALRM, watchdog)
    try:
        signal.setitimer(signal.ITIMER_REAL, args.seconds)
        profiler.enable()
        if command[0] == "search-cli":
            sys.argv = ["bearing-localization-search", *command[1:]]
            runpy.run_module("bearing_localization.search_cli", run_name="__main__")
        else:
            if len(command) < 2:
                raise SystemExit("module command requires a module name")
            sys.argv = [command[1], *command[2:]]
            runpy.run_module(command[1], run_name="__main__")
    except SystemExit as exc:
        save_profile("completed", None)
        return int(exc.code) if isinstance(exc.code, int) else 1
    except BaseException as exc:
        save_profile("failed", type(exc).__name__)
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        if not wrote:
            save_profile("completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
