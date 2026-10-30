"""Execute the resident CLI using existing map and canonical-object caches only.

Accepts the ordinary resident CLI arguments. No observer pose or evaluator
truth is accepted. A missing cache fails before its preparation starts.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

from maritime.search.gpu.resident import aid_view
from maritime.search.gpu.resident import cache as _base_cache
from maritime.search.gpu.resident.preparation import PreparedWorld
from tools.run_resident_marker_search import main as resident_main

_original_loader = _base_cache.load_or_prepare


def _forbid_rebuild(*args, **kwargs) -> NoReturn:
    raise RuntimeError("Required saved cache is missing; rebuilding is disabled for this run")


def _required_cache(
    cache_dir: Path, key: str, builder: Callable[[], PreparedWorld]
) -> tuple[PreparedWorld, bool]:
    return _original_loader(cache_dir, key, _forbid_rebuild)


def main() -> None:
    setattr(_base_cache, "load_or_prepare", _required_cache)
    setattr(aid_view, "_build_view", _forbid_rebuild)
    resident_main()


if __name__ == "__main__":
    main()
