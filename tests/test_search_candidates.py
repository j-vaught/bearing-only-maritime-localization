"""Tests for deterministic water-filtered search candidate grids."""

from __future__ import annotations

import unittest

import numpy as np

from bearing_localization.search.candidates import generate_candidates


class _BandWaterMask:
    def is_water_batch(self, points_xy_m: np.ndarray) -> np.ndarray:
        return (points_xy_m[:, 1] >= 1.0) & (points_xy_m[:, 1] <= 2.0)


class _MalformedWaterMask:
    def is_water_batch(self, points_xy_m: np.ndarray) -> np.ndarray:
        return np.ones((len(points_xy_m), 1), dtype=np.bool_)


class _AllLandMask:
    def is_water_batch(self, points_xy_m: np.ndarray) -> np.ndarray:
        return np.zeros(len(points_xy_m), dtype=np.bool_)


class SearchCandidateTests(unittest.TestCase):
    def test_grid_is_deterministic_and_includes_bounds(self) -> None:
        actual = generate_candidates((0.0, 5.0, 10.0, 16.0), 2.0)
        expected = np.array(
            [
                [0.0, 10.0],
                [2.0, 10.0],
                [4.0, 10.0],
                [5.0, 10.0],
                [0.0, 12.0],
                [2.0, 12.0],
                [4.0, 12.0],
                [5.0, 12.0],
                [0.0, 14.0],
                [2.0, 14.0],
                [4.0, 14.0],
                [5.0, 14.0],
                [0.0, 16.0],
                [2.0, 16.0],
                [4.0, 16.0],
                [5.0, 16.0],
            ]
        )
        np.testing.assert_array_equal(actual, expected)
        np.testing.assert_array_equal(actual, generate_candidates((0.0, 5.0, 10.0, 16.0), 2.0))

    def test_divisible_extent_does_not_duplicate_maximum(self) -> None:
        actual = generate_candidates((-2.0, 2.0, 5.0, 7.0), 2.0)
        self.assertEqual(actual.shape, (6, 2))
        self.assertEqual(len(np.unique(actual, axis=0)), len(actual))
        self.assertEqual(float(np.max(actual[:, 0])), 2.0)
        self.assertEqual(float(np.max(actual[:, 1])), 7.0)

    def test_water_mask_filters_candidates_without_changing_order(self) -> None:
        actual = generate_candidates((0.0, 2.0, 0.0, 3.0), 1.0, _BandWaterMask())
        expected = np.array(
            [[0.0, 1.0], [1.0, 1.0], [2.0, 1.0], [0.0, 2.0], [1.0, 2.0], [2.0, 2.0]]
        )
        np.testing.assert_array_equal(actual, expected)

    def test_all_land_returns_empty_array_with_two_columns(self) -> None:
        actual = generate_candidates((0.0, 2.0, 0.0, 3.0), 1.0, _AllLandMask())
        self.assertEqual(actual.shape, (0, 2))

    def test_rejects_invalid_bounds_and_spacing(self) -> None:
        for bounds in (
            (0.0, 0.0, 0.0, 1.0),
            (0.0, 1.0, 2.0, 1.0),
            (0.0, float("inf"), 0.0, 1.0),
        ):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                generate_candidates(bounds, 1.0)
        for spacing in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(spacing=spacing), self.assertRaises(ValueError):
                generate_candidates((0.0, 1.0, 0.0, 1.0), spacing)

    def test_rejects_malformed_water_mask_output(self) -> None:
        with self.assertRaises(ValueError):
            generate_candidates((0.0, 1.0, 0.0, 1.0), 1.0, _MalformedWaterMask())


if __name__ == "__main__":
    unittest.main()
