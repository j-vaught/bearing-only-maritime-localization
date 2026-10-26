"""Dependency-light exact spatial queries for local and geographic catalogs.

The planar index uses projected east/north meter coordinates. The geographic
index uses a WGS84 Earth-centered chord query as a conservative prefilter and
then exact WGS84 ellipsoidal distances for inclusion.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Hashable, Iterable, Mapping
from math import floor, hypot, isfinite

from pyproj import Geod, Transformer
from scipy.spatial import cKDTree  # ty: ignore[unresolved-import]

Point2D = tuple[float, float]
LonLat = tuple[float, float]
_GEOD = Geod(ellps="WGS84")
_TO_ECEF = Transformer.from_crs("EPSG:4979", "EPSG:4978", always_xy=True)


def _catalog_keys(keys: Iterable[Hashable] | None, count: int) -> tuple[Hashable, ...]:
    result = tuple(range(count)) if keys is None else tuple(keys)
    if len(result) != count:
        raise ValueError("keys must contain one key for every point.")
    try:
        unique = set(result)
    except TypeError as exc:
        raise ValueError("keys must be hashable.") from exc
    if len(unique) != len(result):
        raise ValueError("keys must be unique.")
    return result


class SpatialIndex2D:
    """Uniform-grid radius index for projected east/north meter coordinates.

    Query results refer to integer indices in the immutable catalog order.
    Optional stable keys support catalog-facing queries and immutable rebuilds
    when records move.
    """

    def __init__(
        self,
        points: Iterable[Point2D],
        cell_size_m: float = 1_000.0,
        *,
        keys: Iterable[Hashable] | None = None,
    ) -> None:
        if not isfinite(cell_size_m) or cell_size_m <= 0:
            raise ValueError("cell_size_m must be finite and positive.")
        self._cell_size_m = float(cell_size_m)
        self._points = tuple(
            self._validate_point(point, f"points[{index}]") for index, point in enumerate(points)
        )
        self._keys = _catalog_keys(keys, len(self._points))
        self._key_to_index = {key: index for index, key in enumerate(self._keys)}
        buckets: defaultdict[tuple[int, int], list[int]] = defaultdict(list)
        for index, (east_m, north_m) in enumerate(self._points):
            buckets[self._cell(east_m, north_m)].append(index)
        self._buckets = {cell: tuple(indices) for cell, indices in buckets.items()}
        self._cells = tuple(sorted(self._buckets))

    @staticmethod
    def _validate_point(point: Point2D, name: str) -> Point2D:
        try:
            east_m, north_m = point
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must contain exactly two coordinates.") from exc
        if not isfinite(east_m) or not isfinite(north_m):
            raise ValueError(f"{name} coordinates must be finite.")
        return float(east_m), float(north_m)

    def _cell(self, east_m: float, north_m: float) -> tuple[int, int]:
        return floor(east_m / self._cell_size_m), floor(north_m / self._cell_size_m)

    @property
    def points(self) -> tuple[Point2D, ...]:
        """Return indexed points in their original order."""
        return self._points

    @property
    def keys(self) -> tuple[Hashable, ...]:
        """Return stable record keys in catalog order."""
        return self._keys

    @property
    def cell_size_m(self) -> float:
        """Return the grid-cell width in meters."""
        return self._cell_size_m

    def index_for_key(self, key: Hashable) -> int:
        """Return the stable catalog index for a key."""
        try:
            return self._key_to_index[key]
        except (KeyError, TypeError) as exc:
            raise KeyError(f"Unknown spatial-index key: {key!r}.") from exc

    def with_updated_points(self, updates: Mapping[Hashable, Point2D]) -> SpatialIndex2D:
        """Rebuild with selected keyed points moved, retaining catalog order."""
        updated = list(self._points)
        for key, point in updates.items():
            updated[self.index_for_key(key)] = self._validate_point(point, f"updates[{key!r}]")
        return SpatialIndex2D(updated, self._cell_size_m, keys=self._keys)

    def query_radius(self, east_m: float, north_m: float, radius_m: float) -> tuple[int, ...]:
        """Return input indices within an inclusive radius, in catalog order."""
        if not isfinite(east_m) or not isfinite(north_m):
            raise ValueError("Query coordinates must be finite.")
        if not isfinite(radius_m) or radius_m < 0:
            raise ValueError("radius_m must be finite and nonnegative.")
        east_m = float(east_m)
        north_m = float(north_m)
        radius_m = float(radius_m)
        min_cell_x, min_cell_y = self._cell(east_m - radius_m, north_m - radius_m)
        max_cell_x, max_cell_y = self._cell(east_m + radius_m, north_m + radius_m)
        result: list[int] = []
        for cell_x, cell_y in self._cells:
            if not (min_cell_x <= cell_x <= max_cell_x and min_cell_y <= cell_y <= max_cell_y):
                continue
            for index in self._buckets[(cell_x, cell_y)]:
                point_east_m, point_north_m = self._points[index]
                if hypot(point_east_m - east_m, point_north_m - north_m) <= radius_m:
                    result.append(index)
        result.sort()
        return tuple(result)

    def query_radius_keys(
        self, east_m: float, north_m: float, radius_m: float
    ) -> tuple[Hashable, ...]:
        """Return stable keys for all records within the inclusive radius."""
        return tuple(self._keys[index] for index in self.query_radius(east_m, north_m, radius_m))


class GeographicSpatialIndex:
    """Exact WGS84 radius queries over an immutable longitude/latitude catalog.

    Input positions are (longitude degrees, latitude degrees). The ECEF chord
    tree only supplies a conservative candidate set. Every result is decided
    using the WGS84 ellipsoidal geodesic distance.
    """

    def __init__(
        self,
        points: Iterable[LonLat],
        *,
        keys: Iterable[Hashable] | None = None,
    ) -> None:
        self._points = tuple(
            self._validate_point(point, f"points[{index}]") for index, point in enumerate(points)
        )
        self._keys = _catalog_keys(keys, len(self._points))
        self._key_to_index = {key: index for index, key in enumerate(self._keys)}
        self._ecef = tuple(self._to_ecef(point) for point in self._points)
        self._tree = cKDTree(self._ecef) if self._ecef else None

    @staticmethod
    def _validate_point(point: LonLat, name: str) -> LonLat:
        try:
            longitude_deg, latitude_deg = point
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must contain longitude and latitude.") from exc
        if (
            not isfinite(longitude_deg)
            or not isfinite(latitude_deg)
            or not -180.0 <= longitude_deg <= 180.0
            or not -90.0 <= latitude_deg <= 90.0
        ):
            raise ValueError(f"{name} must contain valid finite WGS84 coordinates.")
        return float(longitude_deg), float(latitude_deg)

    @staticmethod
    def _to_ecef(point: LonLat) -> tuple[float, float, float]:
        x, y, z = _TO_ECEF.transform(point[0], point[1], 0.0)
        return float(x), float(y), float(z)

    @property
    def points(self) -> tuple[LonLat, ...]:
        """Return indexed geographic points in catalog order."""
        return self._points

    @property
    def keys(self) -> tuple[Hashable, ...]:
        """Return stable record keys in catalog order."""
        return self._keys

    def index_for_key(self, key: Hashable) -> int:
        """Return the stable catalog index for a key."""
        try:
            return self._key_to_index[key]
        except (KeyError, TypeError) as exc:
            raise KeyError(f"Unknown spatial-index key: {key!r}.") from exc

    def with_updated_points(self, updates: Mapping[Hashable, LonLat]) -> GeographicSpatialIndex:
        """Rebuild with selected keyed points moved, retaining catalog order."""
        updated = list(self._points)
        for key, point in updates.items():
            updated[self.index_for_key(key)] = self._validate_point(point, f"updates[{key!r}]")
        return GeographicSpatialIndex(updated, keys=self._keys)

    def query_radius(
        self, longitude_deg: float, latitude_deg: float, radius_m: float
    ) -> tuple[int, ...]:
        """Return catalog indices within an inclusive WGS84 geodesic radius."""
        query = self._validate_point((longitude_deg, latitude_deg), "query")
        if not isfinite(radius_m) or radius_m < 0:
            raise ValueError("radius_m must be finite and nonnegative.")
        if self._tree is None:
            return ()
        candidates = self._tree.query_ball_point(
            self._to_ecef(query), float(radius_m) + max(1e-6, float(radius_m) * 1e-12)
        )
        result = []
        for index in candidates:
            point = self._points[index]
            _, _, distance_m = _GEOD.inv(query[0], query[1], point[0], point[1])
            if distance_m <= radius_m:
                result.append(index)
        return tuple(sorted(result))

    def query_radius_keys(
        self, longitude_deg: float, latitude_deg: float, radius_m: float
    ) -> tuple[Hashable, ...]:
        """Return stable keys within an inclusive WGS84 geodesic radius."""
        return tuple(
            self._keys[index] for index in self.query_radius(longitude_deg, latitude_deg, radius_m)
        )
