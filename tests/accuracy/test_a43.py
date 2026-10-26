from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_bounded_profile_writes_partial_artifacts_on_timeout(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    output = tmp_path / "profile"
    command = [
        sys.executable,
        str(repository / "tools" / "profile_pairwise_feasibility.py"),
        "--seconds",
        "0.05",
        "--profile-output",
        str(output),
        "--",
        "module",
        "timeit",
        "-n",
        "100000000",
        "pass",
    ]
    result = subprocess.run(command, capture_output=True, check=False, timeout=5)

    assert result.returncode == 124
    assert output.with_suffix(".prof").is_file()
    assert output.with_suffix(".txt").is_file()
    metadata = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
    assert metadata["status"] == "watchdog_timeout"
    assert metadata["elapsed_s"] < 2.0
