from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from devkit_ui.application_services.obstacle_application_service import (
        ObstacleApplicationService,
    )


class ObstacleViewModel:
    """UI-facing state and user intents for marking and listing obstacles."""

    def __init__(self, service: ObstacleApplicationService) -> None:
        """Bind to the obstacle service."""
        self._service = service
        # Bound to the radius inputs on both tabs, so they stay in step.
        self.default_radius: float = 0.5

    @property
    def obstacles(self) -> tuple[dict[str, Any], ...]:
        """Return the current in-memory obstacles, which may still be awaiting persistence."""
        return self._service.obstacles

    @property
    def version(self) -> int:
        """Return a counter the view compares against to know when to rebuild the list."""
        return self._service.version

    @property
    def status(self) -> str:
        """Return the last action's status text; an error starts with 'ERROR'."""
        return self._service.status

    def set_status(self, text: str) -> None:
        """Show a status message, for errors the view detects itself."""
        self._service.set_status(text)

    def rings_ll(self) -> list[list[tuple[float, float]]]:
        """Return the obstacles as WGS84 rings for the planner."""
        return self._service.rings_ll()

    def mark_here(self, name: str = '') -> str | None:
        """Add a circle at the latest GPS fix using the default radius in meters.

        Return the allocated name, or None if there is no GPS message or the
        service rejected it; the status carries the reason either way.
        TypeError or ValueError from converting the radius propagate, before the GPS
        message is looked up, as do errors raised by the service.
        """
        return self._service.mark_here(float(self.default_radius), name)

    def add_circle(self, lat: float, lon: float) -> str | None:
        """Add a circle at WGS84 (lat, lon) in degrees using the default radius in meters.

        Return its allocated name or None on rejection, with details in status.
        Saving runs in the background.
        TypeError or ValueError from converting the radius and errors raised by
        the service propagate.
        """
        return self._service.add_circle(lat, lon, float(self.default_radius))

    def add_polygon(self, points_ll: list[tuple[float, float]]) -> str | None:
        """Add a polygon from WGS84 (lat, lon) corners in degrees.

        Require at least three corners; repeating the first corner is unnecessary.
        Return its allocated name or None if rejected, with details in status.
        Saving runs in the background.
        Malformed coordinates can raise TypeError or ValueError through the service.
        """
        return self._service.add_polygon(points_ll)

    def delete(self, name: str) -> bool:
        """Delete the named obstacle; return False if it does not exist.

        Return True after in-memory removal and background saving have started.
        Persistence failures update status; thread startup errors propagate.
        """
        return self._service.delete(name)
