"""Summaries of the feature count needed to meet a localization target."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from numbers import Real

_GROUP_FIELDS = ("visibility_m", "type_labels", "algorithm")


def summarize_minimum_features(
    trials: Iterable[Mapping[str, object]],
    target_success_rate: float = 0.95,
) -> list[dict[str, object]]:
    """Find the fewest requested features meeting a success target per search mode.

    Each input row is one trial and must contain ``visibility_m``,
    ``type_labels``, ``algorithm``, ``requested_features``, ``status``, and
    ``success`` fields. Rows with ``requested_features="all"`` are excluded
    because they do not identify a controlled feature-count setting.

    A feature-count setting qualifies only when every trial produced an
    estimate and the fraction of successful trials meets the target. Thus
    insufficient-feature trials count as failures in the success rate and also
    prevent a setting from being reported as a reliable minimum.
    """
    if not isinstance(target_success_rate, Real) or not 0.0 < target_success_rate <= 1.0:
        raise ValueError("target_success_rate must be greater than 0 and at most 1")

    groups: dict[tuple[object, object, object], dict[int, list[Mapping[str, object]]]] = (
        defaultdict(lambda: defaultdict(list))
    )
    for index, trial in enumerate(trials):
        missing = [
            field
            for field in (*_GROUP_FIELDS, "requested_features", "status", "success")
            if field not in trial
        ]
        if missing:
            raise ValueError(f"trial {index} is missing required fields: {', '.join(missing)}")

        requested = trial["requested_features"]
        if requested is None or str(requested).strip().lower() == "all":
            continue
        if not isinstance(requested, (str, int, float)):
            raise ValueError(
                f"trial {index} has a non-integer requested_features value: {requested!r}"
            )
        try:
            feature_count = int(requested)
        except ValueError as exc:
            raise ValueError(
                f"trial {index} has a non-integer requested_features value: {requested!r}"
            ) from exc
        if feature_count < 1 or str(requested).strip() not in {
            str(feature_count),
            f"{feature_count}.0",
        }:
            raise ValueError(f"trial {index} requested_features must be a positive integer")

        key = (trial["visibility_m"], trial["type_labels"], trial["algorithm"])
        groups[key][feature_count].append(trial)

    summaries: list[dict[str, object]] = []
    for key in sorted(groups, key=lambda values: tuple(str(value) for value in values)):
        feature_groups = groups[key]
        eligible_counts: list[int] = []
        for feature_count, rows in feature_groups.items():
            estimated_count = sum(row["status"] == "estimated" for row in rows)
            success_count = sum(
                row["status"] == "estimated" and _as_bool(row["success"], row_index)
                for row_index, row in enumerate(rows)
            )
            availability = estimated_count / len(rows)
            success_rate = success_count / len(rows)
            if availability == 1.0 and success_rate >= target_success_rate:
                eligible_counts.append(feature_count)

        summaries.append(
            {
                **dict(zip(_GROUP_FIELDS, key, strict=True)),
                "target_success_rate": float(target_success_rate),
                "minimum_total_features": min(eligible_counts, default=""),
            }
        )
    return summaries


def _as_bool(value: object, row_index: int) -> bool:
    """Interpret CSV and in-memory booleans without treating "False" as true."""
    if isinstance(value, bool):
        return value
    if isinstance(value, Real) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0", ""}:
            return False
    raise ValueError(f"trial {row_index} has an invalid success value: {value!r}")
