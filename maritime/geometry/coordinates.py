"""Geographic to local-grid coordinate conversion and reference geodesics.

Local projected coordinates support neighborhood operations. True-north bearings
and distances are computed independently on the WGS84 ellipsoid.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees, isfinite

from pyproj import CRS, Geod, Transformer

_GEOD = Geod(ellps="WGS84")


@dataclass(frozen=True, slots=True)
class BearingDistance:
    """Geodesic and projected direction diagnostics from an origin to a target.

    Bearings are degrees clockwise from north, normalized to ``[0, 360)``.
    Meridian convergence is the signed ``true_bearing - grid_bearing`` angle
    wrapped to ``[-180, 180)``. ``bearing_error_deg`` is its absolute value.
    """

    true_bearing_deg: float
    distance_m: float
    grid_bearing_deg: float
    meridian_convergence_deg: float
    bearing_error_deg: float


class LocalFrame:
    """Project geographic coordinates into an origin-relative meter frame.

    ``origin_lonlat`` is ``(longitude_deg, latitude_deg)``. ``projected_crs``
    may be any suitable projected CRS whose axis units are meters. If omitted,
    an origin-centered WGS84 azimuthal-equidistant projection is created. This
    local default avoids selecting one map projection for a coastwide domain;
    callers handling a tile should pass that tile's projected CRS explicitly.

    Local ``(east_m, north_m)`` coordinates are projected coordinates offset
    by the projected origin. They are grid-relative, not guaranteed to be
    geodesic east/north over larger areas.
    """

    def __init__(
        self,
        origin_lonlat: tuple[float, float],
        projected_crs: str | int | CRS | None = None,
    ) -> None:
        self._origin_lon, self._origin_lat = _validate_lonlat(origin_lonlat, "origin_lonlat")
        if projected_crs is None:
            projected_crs = CRS.from_proj4(
                f"+proj=aeqd +lat_0={self._origin_lat} +lon_0={self._origin_lon} "
                "+datum=WGS84 +units=m +no_defs +type=crs"
            )
        self._crs = CRS.from_user_input(projected_crs)
        if not self._crs.is_projected:
            raise ValueError("projected_crs must describe a projected coordinate reference system.")
        axes = self._crs.axis_info
        if len(axes) < 2 or any(
            abs(axis.unit_conversion_factor - 1.0) > 1e-12 for axis in axes[:2]
        ):
            raise ValueError("projected_crs must use meters on both horizontal axes.")
        self._forward = Transformer.from_crs("EPSG:4326", self._crs, always_xy=True)
        self._inverse = Transformer.from_crs(self._crs, "EPSG:4326", always_xy=True)
        self._origin_x, self._origin_y = self._forward.transform(self._origin_lon, self._origin_lat)

    @property
    def origin_lonlat(self) -> tuple[float, float]:
        """Return the frame origin as ``(longitude_deg, latitude_deg)``."""
        return self._origin_lon, self._origin_lat

    @property
    def projected_crs(self) -> CRS:
        """Return the frame's projected coordinate reference system."""
        return self._crs

    def to_local(self, lonlat: tuple[float, float]) -> tuple[float, float]:
        """Convert ``(longitude, latitude)`` to origin-relative ``(east, north)`` meters."""
        lon, lat = _validate_lonlat(lonlat, "lonlat")
        x, y = self._forward.transform(lon, lat)
        return float(x - self._origin_x), float(y - self._origin_y)

    def to_geographic(self, east_m: float, north_m: float) -> tuple[float, float]:
        """Convert origin-relative local meters to ``(longitude, latitude)``."""
        if not isfinite(east_m) or not isfinite(north_m):
            raise ValueError("Local coordinates must be finite.")
        lon, lat = self._inverse.transform(self._origin_x + east_m, self._origin_y + north_m)
        return float(lon), float(lat)

    def bearing_distance(self, lonlat: tuple[float, float]) -> BearingDistance:
        """Measure a target from this origin using geodesic and grid references."""
        target_lon, target_lat = _validate_lonlat(lonlat, "lonlat")
        true_bearing, _, distance = _GEOD.inv(
            self._origin_lon, self._origin_lat, target_lon, target_lat
        )
        true_bearing %= 360.0
        east_m, north_m = self.to_local((target_lon, target_lat))
        grid_bearing = degrees(atan2(east_m, north_m)) % 360.0
        convergence = _wrap_signed(true_bearing - grid_bearing)
        return BearingDistance(
            true_bearing_deg=float(true_bearing),
            distance_m=float(distance),
            grid_bearing_deg=float(grid_bearing),
            meridian_convergence_deg=float(convergence),
            bearing_error_deg=float(abs(convergence)),
        )


def geodesic_bearing_distance(
    origin_lonlat: tuple[float, float], target_lonlat: tuple[float, float]
) -> BearingDistance:
    """Return WGS84 true-north initial bearing and ellipsoidal distance.

    Grid fields are set to the true-bearing reference because this callable has
    no projected grid. Use :meth:`LocalFrame.bearing_distance` for convergence.
    """
    origin_lon, origin_lat = _validate_lonlat(origin_lonlat, "origin_lonlat")
    target_lon, target_lat = _validate_lonlat(target_lonlat, "target_lonlat")
    bearing, _, distance = _GEOD.inv(origin_lon, origin_lat, target_lon, target_lat)
    bearing %= 360.0
    return BearingDistance(
        true_bearing_deg=float(bearing),
        distance_m=float(distance),
        grid_bearing_deg=float(bearing),
        meridian_convergence_deg=0.0,
        bearing_error_deg=0.0,
    )


def _validate_lonlat(lonlat: tuple[float, float], name: str) -> tuple[float, float]:
    try:
        lon, lat = lonlat
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain longitude and latitude.") from exc
    if not isfinite(lon) or not isfinite(lat):
        raise ValueError(f"{name} coordinates must be finite.")
    if not -180.0 <= lon <= 180.0:
        raise ValueError(f"{name} longitude must be between -180 and 180 degrees.")
    if not -90.0 <= lat <= 90.0:
        raise ValueError(f"{name} latitude must be between -90 and 90 degrees.")
    return float(lon), float(lat)


def _wrap_signed(angle_deg: float) -> float:
    return (angle_deg + 180.0) % 360.0 - 180.0
