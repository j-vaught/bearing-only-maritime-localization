from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.run_a26_cached_finite_domain import append_jsonl, sha256_file


def test_chunk_checkpoint_appends_ordered_audit_rows(tmp_path: Path) -> None:
    path = tmp_path / "chunk-results.jsonl"
    first = {"chunk_index": 0, "candidate_ids": ["cell:a", "cell:b"]}
    second = {"chunk_index": 1, "candidate_ids": ["cell:c"]}
    append_jsonl(path, [first])
    append_jsonl(path, [second])
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows == [first, second]
    assert sha256_file(path)


def test_finite_domain_driver_refuses_nonempty_output(tmp_path: Path) -> None:
    from tools.run_a26_cached_finite_domain import run_finite_domain

    output = tmp_path / "out"
    output.mkdir()
    (output / "old.jsonl").write_text("{}\n")
    with pytest.raises(FileExistsError, match="Fresh finite-domain"):
        run_finite_domain(
            search_root=tmp_path,
            static_root=tmp_path,
            cache_path=tmp_path / "cache",
            generation_run_root=tmp_path,
            output_root=output,
        )
