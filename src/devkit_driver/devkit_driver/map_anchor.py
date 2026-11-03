"""Anchor the map frame to a surveyed geodetic origin (no ROS imports, unit tested).

The map frame is the topological map's local frame in metres. Its origin is a surveyed
point (origin_lat, origin_lon) and its x axis points map_yaw_rad counter-clockwise from
east. The odom frame is east-north-up from wherever the localiser started (EKF3 origin,
or the first GNSS fix for FusionCore), so odom axes are parallel to ENU.

Given one fix and the robot's odom position at the same moment, map -> odom is a
rotation of -map_yaw_rad and a translation that makes the two positions agree.
"""
import math
from collections import deque
from statistics import median

_WGS84_A = 6378137.0
_WGS84_E2 = 6.69437999014e-3


def geodetic_offset_en(lat: float, lon: float, origin_lat: float, origin_lon: float) -> tuple[float, float]:
    """Return the (east, north) offset in metres of (lat, lon) from the origin, in degrees.

    Local tangent-plane approximation with WGS84 radii; error is well under a centimetre
    over a field. Longitude difference is wrapped, so the antimeridian is handled.
    """
    phi = math.radians(origin_lat)
    denominator = 1.0 - _WGS84_E2 * math.sin(phi) ** 2
    prime_vertical = _WGS84_A / math.sqrt(denominator)
    meridional = _WGS84_A * (1.0 - _WGS84_E2) / denominator ** 1.5
    d_lon = (lon - origin_lon + 180.0) % 360.0 - 180.0
    return (math.radians(d_lon) * prime_vertical * math.cos(phi),
            math.radians(lat - origin_lat) * meridional)


def map_odom_transform(fix_lat: float, fix_lon: float, odom_x: float, odom_y: float,
                       origin_lat: float, origin_lon: float, map_yaw_rad: float) -> tuple[float, float, float]:
    """Return (tx, ty, yaw) of map -> odom from one fix and the odom position taken with it."""
    east, north = geodetic_offset_en(fix_lat, fix_lon, origin_lat, origin_lon)
    cos_y, sin_y = math.cos(map_yaw_rad), math.sin(map_yaw_rad)
    map_x, map_y = east * cos_y + north * sin_y, -east * sin_y + north * cos_y
    return (map_x - (odom_x * cos_y + odom_y * sin_y),
            map_y - (-odom_x * sin_y + odom_y * cos_y),
            -map_yaw_rad)


class AnchorEstimator:
    """Pair fixes with odom positions and return map -> odom once the estimates agree.

    Times are seconds on any one monotonic clock. A fix and an odom sample are paired when
    they arrive within max_skew_s, each sample is used once, and a result is returned only
    when the last `samples` pairs agree within max_spread_m, using their median. That keeps
    a noisy start-up fix from anchoring the map.
    """

    def __init__(self, origin_lat: float, origin_lon: float, map_yaw_rad: float,
                 samples: int = 10, max_skew_s: float = 0.5, max_spread_m: float = 0.5):
        """Store the surveyed origin and the acceptance limits."""
        if samples < 1 or max_skew_s <= 0.0 or max_spread_m <= 0.0:
            raise ValueError('samples, max_skew_s and max_spread_m must be positive')
        self._origin = (origin_lat, origin_lon)
        self._map_yaw = map_yaw_rad
        self._max_skew_s = max_skew_s
        self._max_spread_m = max_spread_m
        self._window: deque[tuple[float, float]] = deque(maxlen=samples)
        self._fix: tuple[float, float, float] | None = None
        self._odom: tuple[float, float, float] | None = None
        self.spread_m = 0.0

    def add_fix(self, t: float, lat: float, lon: float) -> tuple[float, float, float] | None:
        """Record a valid fix; return map -> odom (tx, ty, yaw) if this completes an agreeing window."""
        self._fix = (t, lat, lon)
        return self._pair()

    def add_odom(self, t: float, x: float, y: float) -> tuple[float, float, float] | None:
        """Record an odom position; return map -> odom (tx, ty, yaw) if this completes an agreeing window."""
        self._odom = (t, x, y)
        return self._pair()

    def _pair(self) -> tuple[float, float, float] | None:
        if self._fix is None or self._odom is None or abs(self._fix[0] - self._odom[0]) > self._max_skew_s:
            return None
        tx, ty, yaw = map_odom_transform(self._fix[1], self._fix[2], self._odom[1], self._odom[2],
                                         *self._origin, self._map_yaw)
        self._fix = self._odom = None
        self._window.append((tx, ty))
        if len(self._window) < self._window.maxlen:
            return None
        xs, ys = [p[0] for p in self._window], [p[1] for p in self._window]
        self.spread_m = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        if self.spread_m > self._max_spread_m:
            return None
        return median(xs), median(ys), yaw
