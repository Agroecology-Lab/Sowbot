"""Use cases for obstacles: list, mark at the robot, add a circle or polygon, and delete.

This sits between ObstacleViewModel and ObstacleManager, which owns the obstacle list,
its YAML file and the /obstacles publisher. The service takes plain numbers and returns plain
results; the radius default and its conversion from a bound input stay in the view model.
No ROS or NiceGUI imports.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from devkit_ui.application_services.telemetry_application_service import (
        TelemetryApplicationService,
    )
    from devkit_ui.obstacles import ObstacleManager

MIN_POLYGON_CORNERS = 3


class ObstacleApplicationService:
    """Use cases for the obstacles drawn on the Run and Plan tabs."""

    def __init__(self, manager: ObstacleManager, telemetry: TelemetryApplicationService) -> None:
        """Keep the obstacle manager and the telemetry service that supplies the latest GPS fix."""
        self._manager = manager
        self._telemetry = telemetry

    @property
    def obstacles(self) -> tuple[dict[str, Any], ...]:
        """Return the current in-memory obstacles, which may still be awaiting persistence."""
        return self._manager.obstacles

    @property
    def version(self) -> int:
        """Return a counter that increases whenever the obstacles change."""
        return self._manager.version

    @property
    def status(self) -> str:
        """Return the last action's status text; an error starts with 'ERROR'."""
        return self._manager.status

    def set_status(self, text: str) -> None:
        """Show a status message, for errors a caller detects itself."""
        self._manager.set_status(text)

    def rings_ll(self) -> list[list[tuple[float, float]]]:
        """Return the obstacles as WGS84 rings for the planner."""
        return self._manager.rings_ll()

    def mark_here(self, radius_m: float, name: str = '') -> str | None:
        """Add a circle of radius_m meters at the latest GPS fix.

        Return the allocated name, or None if there is no GPS message yet or the
        manager rejected the circle; the status carries the reason either way.
        Name normalization and background saving follow ObstacleManager.add().
        Errors raised by the manager propagate.
        """
        gps = self._telemetry.latest_gps
        if gps is None:
            self._manager.set_status('ERROR: no GPS message yet')
            return None
        return self._manager.add(
            'circle', lat=gps.latitude, lon=gps.longitude, radius_m=radius_m, name=name)

    def add_circle(self, lat: float, lon: float, radius_m: float) -> str | None:
        """Add a circle of radius_m meters at WGS84 (lat, lon) in degrees.

        Return its allocated name, or None on rejection with the reason in status.
        Saving runs in the background. Errors raised by the manager propagate.
        """
        return self._manager.add('circle', lat=lat, lon=lon, radius_m=radius_m)

    def add_polygon(self, points_ll: list[tuple[float, float]]) -> str | None:
        """Add a polygon from WGS84 (lat, lon) corners in degrees.

        Require at least three corners; repeating the first corner is unnecessary.
        Return its allocated name, or None if rejected with the reason in status.
        Saving runs in the background.
        Malformed coordinates can raise TypeError or ValueError through ObstacleManager.add().
        """
        if len(points_ll) < MIN_POLYGON_CORNERS:
            self._manager.set_status('ERROR: polygon needs 3+ corners')
            return None
        return self._manager.add('polygon', points_ll=points_ll)

    def delete(self, name: str) -> bool:
        """Delete the named obstacle; return False if it does not exist.

        Return True after in-memory removal and background saving have started.
        Persistence failures update status; thread startup errors propagate.
        """
        return self._manager.delete(name)
