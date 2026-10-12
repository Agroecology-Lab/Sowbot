from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from devkit_ui.obstacles import ObstacleManager


class ObstacleViewModel:
    """UI-facing state and user intents for marking and listing obstacles."""

    def __init__(self, manager: ObstacleManager,
                 get_gps: Callable[[], object | None]) -> None:
        """Bind to the obstacle manager and the getter for the latest GPS fix."""
        self._manager = manager
        self._get_gps = get_gps
        # Bound to the radius inputs on both tabs, so they stay in step.
        self.default_radius: float = 0.5

    @property
    def obstacles(self) -> tuple[dict, ...]:
        """Return the current in-memory obstacles, which may still be awaiting persistence."""
        return self._manager.obstacles

    @property
    def version(self) -> int:
        """Return a counter the view compares against to know when to rebuild the list."""
        return self._manager.version

    @property
    def status(self) -> str:
        """Return the last action's status text; an error starts with 'ERROR'."""
        return self._manager.status

    def set_status(self, text: str) -> None:
        """Show a status message, for errors the view detects itself."""
        self._manager.set_status(text)

    def rings_ll(self) -> list[list[tuple[float, float]]]:
        """Return the obstacles as WGS84 rings for the planner."""
        return self._manager.rings_ll()

    def mark_here(self, name: str = '') -> str | None:
        """Add a circle at the latest GPS fix using the default radius in meters.

        Return the allocated name, or None if there is no GPS message or the
        manager rejected it; the status carries the reason either way.
        Name normalization and background saving follow ObstacleManager.add().
        GPS getter and field-access errors propagate, as do TypeError or ValueError
        from converting the radius and errors raised by the manager.
        """
        gps = self._get_gps()
        if gps is None:
            self._manager.set_status('ERROR: no GPS message yet')
            return None
        return self._manager.add(
            'circle', lat=gps.latitude, lon=gps.longitude,
            radius_m=float(self.default_radius), name=name)

    def add_circle(self, lat: float, lon: float) -> str | None:
        """Add a circle at WGS84 (lat, lon) in degrees using the default radius in meters.

        Return its allocated name or None on rejection, with details in status.
        Saving runs in the background.
        TypeError or ValueError from converting the radius and errors raised by
        ObstacleManager.add() propagate.
        """
        return self._manager.add(
            'circle', lat=lat, lon=lon, radius_m=float(self.default_radius))

    def add_polygon(self, points_ll: list[tuple[float, float]]) -> str | None:
        """Add a polygon from WGS84 (lat, lon) corners in degrees.

        Require at least three corners; repeating the first corner is unnecessary.
        Return its allocated name or None if rejected, with details in status.
        Saving runs in the background.
        Malformed coordinates can raise TypeError or ValueError through ObstacleManager.add().
        """
        if len(points_ll) < 3:
            self._manager.set_status('ERROR: polygon needs 3+ corners')
            return None
        return self._manager.add('polygon', points_ll=points_ll)

    def delete(self, name: str) -> bool:
        """Delete the named obstacle; return False if it does not exist.

        Return True after in-memory removal and background saving have started.
        Persistence failures update status; thread startup errors propagate.
        """
        return self._manager.delete(name)
