"""The point that ties the odometry frame to WGS84, as plain numbers."""
from __future__ import annotations

from typing import NamedTuple


class MapAnchor(NamedTuple):
    """The robot's map position and its GPS fix at the same moment.

    map_x and map_y are meters in the map frame; lat and lon are degrees. Projecting a lat/lon
    obstacle relative to this pair places it in the map frame without a ROS message in sight.
    """

    map_x: float
    map_y: float
    lat: float
    lon: float
