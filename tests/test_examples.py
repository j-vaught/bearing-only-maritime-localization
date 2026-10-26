"""Example scripts resolve the installed package without running at import time."""

from __future__ import annotations

import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = tuple(
    sorted(path for path in (ROOT / "examples").glob("*.py") if not path.name.startswith("_"))
)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda path: path.stem)
def test_example_imports_without_execution(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "examples"))
    namespace = runpy.run_path(str(path), run_name="example_import_check")
    assert namespace["__name__"] == "example_import_check"


def test_example_data_modes_reference_existing_archives(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "examples"))
    from examples import _data

    for mode in ("ais", "chart_aids"):
        spec = _data.catalog_spec(mode)
        assert (_data.DATA / spec["archive_filename"]).is_file()
        assert spec["record_count"] > 0
    combined = _data.catalog_spec("both")
    assert combined["record_count"] == sum(
        _data.catalog_spec(mode)["record_count"] for mode in ("ais", "chart_aids")
    )
    assert combined["merge_order"] == ["ais", "chart_aids"]
