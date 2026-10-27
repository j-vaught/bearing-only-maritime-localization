"""Rate-based progress reporting for long localization search stages."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import TextIO

ProgressCallback = Callable[[str, int, int], None]


@dataclass(slots=True)
class _StageRate:
    start_time: float
    start_completed: int
    last_report_time: float
    rate_per_s: float = 0.0


class SearchProgressReporter:
    """Print completion and a rolling ETA for named, countable search stages."""

    def __init__(self, interval_s: float = 5.0, *, stream: TextIO = sys.stderr) -> None:
        if interval_s < 0.0:
            raise ValueError("interval_s must be nonnegative.")
        self.interval_s = interval_s
        self.stream = stream
        self._stages: dict[str, _StageRate] = {}

    def __call__(self, stage: str, completed: int, total: int) -> None:
        if not stage or completed < 0 or total < 1 or completed > total:
            raise ValueError("Progress updates require a stage and 0 <= completed <= total.")
        now = monotonic()
        state = self._stages.get(stage)
        if state is None:
            state = _StageRate(now, completed, now)
            self._stages[stage] = state
        elapsed = now - state.start_time
        if elapsed > 0.0 and completed > state.start_completed:
            state.rate_per_s = (completed - state.start_completed) / elapsed
        if completed < total and now - state.last_report_time < self.interval_s:
            return
        remaining_s = (total - completed) / state.rate_per_s if state.rate_per_s > 0.0 else None
        eta = "unknown" if remaining_s is None else _format_duration(remaining_s)
        rate = "unknown" if state.rate_per_s <= 0.0 else f"{state.rate_per_s:.0f}/s"
        print(
            f"progress stage={stage} completed={completed}/{total} rate={rate} eta={eta}",
            file=self.stream,
            flush=True,
        )
        state.last_report_time = now


def _format_duration(seconds: float) -> str:
    whole = max(0, int(seconds + 0.5))
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


__all__ = ["ProgressCallback", "SearchProgressReporter"]
