"""Detach the approved sequential suite from the client. J.C. Vaught."""

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
repo = Path(__file__).resolve().parents[3]
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=True)
pid_file = output / "supervisor.pid"
if pid_file.exists() and Path(f"/proc/{int(pid_file.read_text())}").exists():
    raise RuntimeError("An existing supervisor is alive. No duplicate launched")
argv = [
    str(repo / ".venv/bin/python"),
    "-u",
    str(Path(__file__).with_name("run_suite.py")),
    "--output",
    str(output),
    "--minimum-positive-aids",
    "16",
]
with (output / "supervisor.log").open("ab", buffering=0) as log:
    process = subprocess.Popen(
        argv, cwd=repo, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True
    )
pid_file.write_text(f"{process.pid}\n")
launch = {
    "pid": process.pid,
    "launched_utc": datetime.now(UTC).isoformat(),
    "argv": argv,
    "log": str(output / "supervisor.log"),
    "status": str(output / "status.json"),
    "output": str(output),
    "approved_scope": "Sequential B1 through B24 full coast searches, GPUs 0,1,2, after checks",
    "detached_session": True,
}
(output / "launch.json").write_text(json.dumps(launch, indent=2) + "\n")
print(json.dumps(launch, indent=2))
