"""Tests for minimum feature-count summaries."""

from __future__ import annotations

import unittest

from maritime.evaluation.minimum_features import summarize_minimum_features


def trial(
    requested_features: int | str,
    *,
    success: bool | str = True,
    status: str = "estimated",
    visibility_m: float = 5_000.0,
    type_labels: bool = True,
    algorithm: str = "grid",
) -> dict[str, object]:
    return {
        "visibility_m": visibility_m,
        "type_labels": type_labels,
        "algorithm": algorithm,
        "requested_features": requested_features,
        "status": status,
        "success": success,
    }


class MinimumFeatureTests(unittest.TestCase):
    def test_finds_smallest_count_that_meets_target_for_every_trial(self) -> None:
        trials = [
            trial(3, success=True),
            trial(3, success="false"),
            trial(5, success=True),
            trial(5, success=True),
            trial(5, success=True),
        ]

        result = summarize_minimum_features(trials, target_success_rate=0.95)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["minimum_total_features"], 5)
        self.assertEqual(result[0]["target_success_rate"], 0.95)

    def test_insufficient_trials_count_against_success_and_availability(self) -> None:
        result = summarize_minimum_features(
            [
                trial(4, success=True),
                trial(4, success=False, status="insufficient_features"),
                trial(6, success=True),
                trial(6, success=True),
            ],
            target_success_rate=0.5,
        )

        self.assertEqual(result[0]["minimum_total_features"], 6)

    def test_all_features_rows_are_not_counted_as_swept_settings(self) -> None:
        self.assertEqual(
            summarize_minimum_features([trial("all")]),
            [],
        )

    def test_summarizes_independent_search_modes(self) -> None:
        result = summarize_minimum_features(
            [
                trial(3, algorithm="grid"),
                trial(5, algorithm="randomized"),
            ],
            target_success_rate=1.0,
        )

        self.assertEqual([row["algorithm"] for row in result], ["grid", "randomized"])
        self.assertEqual([row["minimum_total_features"] for row in result], [3, 5])

    def test_rejects_invalid_target_and_trial_rows(self) -> None:
        with self.assertRaises(ValueError):
            summarize_minimum_features([], target_success_rate=0.0)
        with self.assertRaises(ValueError):
            summarize_minimum_features([{"requested_features": 3}])
        with self.assertRaises(ValueError):
            summarize_minimum_features([trial("three")])
        with self.assertRaises(ValueError):
            summarize_minimum_features([trial(3, success="maybe")])


if __name__ == "__main__":
    unittest.main()
