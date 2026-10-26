import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import deduplicate_accuracy_archives as dedup


def _manifest(path: Path, *trial_ids: str) -> Path:
    path.write_text("trial_id,status\n" + "".join(f"{item},completed\n" for item in trial_ids))
    return path


def _plan(tmp_path: Path, contents: list[tuple[str, bytes]]) -> tuple[Path, Path]:
    root = tmp_path / "evidence"
    root.mkdir()
    rows = []
    for relative, payload in contents:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        rows.append(str(Path(relative).parent))
    manifest = tmp_path / "preparation.csv"
    trials = sorted(set(rows))
    _manifest(manifest, *trials)
    plan, _ = dedup.inventory(root, manifest)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    return root, plan_path


def test_duplicate_contents_are_proposed_and_applied_with_paths_preserved(tmp_path: Path) -> None:
    root, plan_path = _plan(
        tmp_path,
        [("t1/a.json", b"same evidence"), ("t2/a.json", b"same evidence")],
    )
    before = {
        p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    plan = json.loads(plan_path.read_text())
    assert len(plan["duplicate_groups"]) == 1
    assert dedup.apply_plan(plan_path)["applied_replacements"] == 1
    paths = sorted(root.rglob("*.json"))
    assert len(paths) == 2
    assert paths[0].read_bytes() == paths[1].read_bytes() == b"same evidence"
    assert os.stat(paths[0]).st_ino == os.stat(paths[1]).st_ino
    assert {p.relative_to(root).as_posix(): p.read_bytes() for p in paths} == before


def test_equal_size_different_bytes_are_not_candidates(tmp_path: Path) -> None:
    _, plan_path = _plan(tmp_path, [("t1/a.json", b"abc"), ("t2/a.json", b"xyz")])
    plan = json.loads(plan_path.read_text())
    assert plan["duplicate_groups"] == []


def test_symlinks_fail_closed_on_apply(tmp_path: Path) -> None:
    root, plan_path = _plan(
        tmp_path,
        [("t1/a.json", b"same"), ("t2/a.json", b"same")],
    )
    (root / "t3").mkdir()
    (root / "t3/link").symlink_to(root / "t1/a.json")
    with pytest.raises(RuntimeError, match="symlink"):
        dedup.apply_plan(plan_path)


def test_changed_file_fails_closed_before_any_replacement(tmp_path: Path) -> None:
    root, plan_path = _plan(
        tmp_path,
        [("t1/a.json", b"same"), ("t2/a.json", b"same")],
    )
    (root / "t2/a.json").write_bytes(b"else")
    with pytest.raises(RuntimeError, match="changed"):
        dedup.apply_plan(plan_path)
    assert os.stat(root / "t1/a.json").st_ino != os.stat(root / "t2/a.json").st_ino


def test_completed_trial_filter_excludes_incomplete_outputs(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    (root / "t1").mkdir(parents=True)
    (root / "t2").mkdir()
    (root / "t1/a.json").write_bytes(b"same")
    (root / "t2/a.json").write_bytes(b"same")
    manifest = tmp_path / "preparation.csv"
    manifest.write_text("trial_id,status\nt1,completed\nt2,failed\n")
    plan, summary = dedup.inventory(root, manifest)
    assert summary["completed_trial_count"] == 1
    assert plan["duplicate_groups"] == []


def test_apply_failure_rolls_back_prior_replacements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, plan_path = _plan(
        tmp_path,
        [
            ("t1/a.json", b"same"),
            ("t2/a.json", b"same"),
            ("t3/a.json", b"same"),
        ],
    )
    targets = [root / f"t{n}/a.json" for n in (1, 2, 3)]
    before = {path: path.read_bytes() for path in targets}
    original_replace = dedup.os.replace
    calls = 0

    def fail_second_replace(
        source: str | os.PathLike[str], destination: str | os.PathLike[str]
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected replacement failure")
        original_replace(source, destination)

    monkeypatch.setattr(dedup.os, "replace", fail_second_replace)
    with pytest.raises(OSError, match="injected"):
        dedup.apply_plan(plan_path)
    assert {path: path.read_bytes() for path in targets} == before
    assert len({os.stat(path).st_ino for path in targets}) == 3


def test_reinventory_after_apply_is_idempotent(tmp_path: Path) -> None:
    root, plan_path = _plan(
        tmp_path,
        [("t1/a.json", b"same"), ("t2/a.json", b"same")],
    )
    dedup.apply_plan(plan_path)
    manifest = Path(json.loads(plan_path.read_text())["completion_manifest"])
    next_plan, summary = dedup.inventory(root, manifest)
    assert summary["eligible_duplicate_count"] == 0
    assert next_plan["duplicate_groups"] == []
