"""Tests for reproducible, water-only observer placement."""

from __future__ import annotations

import unittest
from typing import cast

import numpy as np

from maritime.geometry.water_mask import WaterMask
from maritime.schemas.map_input import MapInput
from maritime.simulator.observer import sample_observers


class ObserverPlacementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.map_input = MapInput(
            crs="local-metric",
            west_m=100.0,
            south_m=200.0,
            cell_size_m=10.0,
            water_mask=(
                (True, False, True),
                (False, True, False),
            ),
        )

    def test_samples_are_cell_centers_of_water_cells(self) -> None:
        positions = sample_observers(
            self.map_input,
            3,
            np.random.default_rng(10),
        )

        self.assertEqual(len(positions), 3)
        for position in positions:
            self.assertTrue(self.map_input.water_mask[position.row][position.column])
            self.assertEqual(position.east_m, 100.0 + (position.column + 0.5) * 10.0)
            self.assertEqual(position.north_m, 200.0 + (position.row + 0.5) * 10.0)

    def test_same_seed_produces_same_positions(self) -> None:
        first = sample_observers(self.map_input, 3, np.random.default_rng(22))
        second = sample_observers(self.map_input, 3, np.random.default_rng(22))

        self.assertEqual(first, second)

    def test_unique_cell_sampling_does_not_repeat_positions(self) -> None:
        positions = sample_observers(self.map_input, 3, np.random.default_rng(5))

        self.assertEqual(len({(p.row, p.column) for p in positions}), 3)

    def test_sampling_with_replacement_allows_more_samples_than_water_cells(self) -> None:
        positions = sample_observers(
            self.map_input,
            8,
            np.random.default_rng(5),
            unique_cells=False,
        )

        self.assertEqual(len(positions), 8)
        self.assertTrue(all(self.map_input.water_mask[p.row][p.column] for p in positions))

    def test_empty_request_returns_no_positions(self) -> None:
        self.assertEqual(sample_observers(self.map_input, 0, np.random.default_rng(1)), ())

    def test_negative_count_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            sample_observers(self.map_input, -1, np.random.default_rng(1))

    def test_unique_sample_larger_than_water_mask_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unique observer cells"):
            sample_observers(self.map_input, 4, np.random.default_rng(1))

    def test_mask_without_water_is_rejected(self) -> None:
        land_only = MapInput(
            crs="local-metric",
            west_m=0.0,
            south_m=0.0,
            cell_size_m=1.0,
            water_mask=((False, False),),
        )
        with self.assertRaisesRegex(ValueError, "no water cells"):
            sample_observers(land_only, 1, np.random.default_rng(1))

    def test_non_generator_rng_is_rejected(self) -> None:
        with self.assertRaisesRegex(TypeError, "Generator"):
            sample_observers(self.map_input, 1, cast(np.random.Generator, 1))

    def test_query_water_mask_respects_invalid_cells(self) -> None:
        mask = WaterMask(
            water=np.array([[True, True], [True, False]], dtype=bool),
            west_m=-5.0,
            south_m=20.0,
            cell_size_m=4.0,
            valid=np.array([[True, False], [True, True]], dtype=bool),
        )

        positions = sample_observers(mask, 2, np.random.default_rng(4))

        self.assertEqual({(p.row, p.column) for p in positions}, {(0, 0), (1, 0)})


if __name__ == "__main__":
    unittest.main()
