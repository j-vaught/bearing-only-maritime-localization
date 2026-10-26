"""Focused tests for projected raster water-mask queries."""

from __future__ import annotations

import unittest
from typing import cast

import numpy as np

from maritime.geometry.water_mask import WaterMask


class WaterMaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.water = WaterMask(
            np.array([[True, False], [True, True]], dtype=np.bool_),
            west_m=100.0,
            south_m=200.0,
            cell_size_m=10.0,
        )

    def test_bounds_and_cell_transform(self) -> None:
        self.assertEqual(self.water.bounds_m, (100.0, 200.0, 120.0, 220.0))
        self.assertEqual(self.water.world_to_cell(100.0, 200.0), (0, 0))
        self.assertEqual(self.water.world_to_cell(119.999, 219.999), (1, 1))
        self.assertIsNone(self.water.world_to_cell(120.0, 210.0))
        self.assertIsNone(self.water.world_to_cell(110.0, 220.0))
        self.assertIsNone(self.water.world_to_cell(float("nan"), 210.0))

    def test_scalar_query_classifies_cells(self) -> None:
        self.assertTrue(self.water.is_water(105.0, 205.0))
        self.assertFalse(self.water.is_water(115.0, 205.0))
        self.assertTrue(self.water.is_water(115.0, 215.0))
        self.assertFalse(self.water.is_water(120.0, 215.0))
        self.assertFalse(self.water.is_water(float("inf"), 215.0))

    def test_batch_query_matches_scalar_and_preserves_shape(self) -> None:
        points = np.array([[[105.0, 205.0], [115.0, 205.0]], [[115.0, 215.0], [120.0, 215.0]]])
        actual = self.water.is_water_batch(points)
        expected = np.array([[True, False], [True, False]])
        np.testing.assert_array_equal(actual, expected)
        self.assertEqual(actual.shape, points.shape[:-1])

    def test_batch_cell_transform_uses_sentinel_for_invalid_points(self) -> None:
        points = np.array([[100.0, 200.0], [119.0, 219.0], [120.0, 210.0], [np.nan, 210.0]])
        actual = self.water.world_to_cell_batch(points)
        expected = np.array([[0, 0], [1, 1], [-1, -1], [-1, -1]])
        np.testing.assert_array_equal(actual, expected)

    def test_invalid_cells_are_conservatively_not_water(self) -> None:
        mask = np.ones((2, 2), dtype=np.bool_)
        valid = np.array([[True, False], [True, True]], dtype=np.bool_)
        water = WaterMask(mask, west_m=0.0, south_m=0.0, cell_size_m=1.0, valid=valid)
        self.assertTrue(water.is_water(0.5, 0.5))
        self.assertFalse(water.is_water(1.5, 0.5))
        points = np.array([[0.5, 0.5], [1.5, 0.5]])
        np.testing.assert_array_equal(water.is_water_batch(points), [True, False])

    def test_constructor_rejects_bad_shapes_and_cell_sizes(self) -> None:
        with self.assertRaises(ValueError):
            WaterMask(np.ones((0, 2), dtype=np.bool_), 0.0, 0.0, 1.0)
        with self.assertRaises(ValueError):
            WaterMask(np.ones((2, 2), dtype=np.bool_), 0.0, 0.0, 0.0)
        with self.assertRaises(ValueError):
            WaterMask(
                np.ones((2, 2), dtype=np.bool_),
                0.0,
                0.0,
                1.0,
                valid=np.ones((1, 2), dtype=np.bool_),
            )
        with self.assertRaises(TypeError):
            WaterMask(
                cast(np.ndarray, np.ones((2, 2), dtype=np.int8)),
                0.0,
                0.0,
                1.0,
            )

    def test_queries_are_not_mutable_through_input_arrays(self) -> None:
        values = np.array([[True]], dtype=np.bool_)
        water = WaterMask(values, west_m=0.0, south_m=0.0, cell_size_m=1.0)
        values[0, 0] = False
        self.assertTrue(water.is_water(0.5, 0.5))


if __name__ == "__main__":
    unittest.main()
