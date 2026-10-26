from pathlib import Path
from runpy import run_path


def test_accuracy_map_archives_round_trip_and_match_index() -> None:
    root = Path(__file__).resolve().parents[2]
    module = run_path(str(root / "tools/package_accuracy_map_inputs.py"), run_name="a39_test")
    assert module["verify_manifest"](root) == []
