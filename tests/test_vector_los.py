"""Tests for the dependency-light vector visibility reference."""

from __future__ import annotations

import unittest

from bearing_localization.geospatial.vector_los import line_of_sight_clear

RECTANGLE = ((4.0, -1.0), (6.0, -1.0), (6.0, 1.0), (4.0, 1.0))


class VectorLineOfSightTests(unittest.TestCase):
    def test_clear_ray_when_no_land_intersects(self) -> None:
        self.assertTrue(line_of_sight_clear((0.0, 0.0), (10.0, 0.0), []))
        self.assertTrue(line_of_sight_clear((0.0, 3.0), (10.0, 3.0), [RECTANGLE]))

    def test_land_interior_blocks_ray(self) -> None:
        self.assertFalse(line_of_sight_clear((0.0, 0.0), (10.0, 0.0), [RECTANGLE]))

    def test_boundary_tangent_and_boundary_following_do_not_block(self) -> None:
        self.assertTrue(line_of_sight_clear((0.0, 1.0), (10.0, 1.0), [RECTANGLE]))
        self.assertTrue(line_of_sight_clear((0.0, 1.0), (10.0, 3.0), [RECTANGLE]))

    def test_shoreline_target_contact_is_allowed(self) -> None:
        self.assertTrue(line_of_sight_clear((0.0, 0.0), (4.0, 0.0), [RECTANGLE]))

    def test_interior_crossing_from_shoreline_observer_blocks(self) -> None:
        self.assertFalse(line_of_sight_clear((4.0, 0.0), (10.0, 0.0), [RECTANGLE]))

    def test_open_and_closed_rings_and_winding_directions_match(self) -> None:
        closed_reversed = tuple(reversed((*RECTANGLE, RECTANGLE[0])))
        self.assertFalse(line_of_sight_clear((0.0, 0.0), (10.0, 0.0), [RECTANGLE]))
        self.assertFalse(line_of_sight_clear((0.0, 0.0), (10.0, 0.0), [closed_reversed]))

    def test_concave_polygon_is_supported(self) -> None:
        concave = ((3.0, -2.0), (7.0, -2.0), (7.0, -1.0), (4.0, -1.0),
                   (4.0, 1.0), (7.0, 1.0), (7.0, 2.0), (3.0, 2.0))
        self.assertTrue(line_of_sight_clear((4.5, 0.0), (10.0, 0.0), [concave]))
        self.assertFalse(line_of_sight_clear((3.5, -3.0), (3.5, 3.0), [concave]))

    def test_tolerance_ignores_only_short_interior_penetration(self) -> None:
        narrow_land = ((4.0, -1.0), (4.0005, -1.0), (4.0005, 1.0), (4.0, 1.0))
        self.assertTrue(
            line_of_sight_clear((0.0, 0.0), (10.0, 0.0), [narrow_land], tolerance_m=0.001)
        )
        self.assertFalse(
            line_of_sight_clear((0.0, 0.0), (10.0, 0.0), [narrow_land], tolerance_m=0.0001)
        )

    def test_coincident_observer_and_target_is_clear(self) -> None:
        self.assertTrue(line_of_sight_clear((5.0, 0.0), (5.0, 0.0), [RECTANGLE]))

    def test_invalid_tolerance_and_degenerate_polygon_raise(self) -> None:
        with self.assertRaises(ValueError):
            line_of_sight_clear((0.0, 0.0), (1.0, 0.0), [], tolerance_m=-1.0)
        with self.assertRaises(ValueError):
            line_of_sight_clear((0.0, 0.0), (1.0, 0.0), [((0.0, 0.0), (1.0, 1.0))])


if __name__ == "__main__":
    unittest.main()
