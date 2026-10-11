from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from devkit_ui.application_services.mission_application_service import (
        MissionApplicationService,
    )


class MissionViewModel:
    """UI-facing state and user intents for the saved-missions list."""

    def __init__(self, app_service: MissionApplicationService) -> None:
        """Bind to the mission application service."""
        self._app_service = app_service

    @property
    def missions(self) -> tuple[dict, ...]:
        """Return the saved missions to display."""
        return self._app_service.missions

    @property
    def version(self) -> int:
        """Return a counter the view compares against to know when to rebuild the list."""
        return self._app_service.version

    @property
    def status(self) -> str:
        """Return the last action's status text; an error starts with 'ERROR'."""
        return self._app_service.status

    @property
    def running(self) -> bool:
        """Return whether a mission is executing, which disables starting another."""
        return self._app_service.running

    def next_due_in_hours(self, mission_id: str) -> float | None:
        """Return hours until the mission is due, 0.0 if due now, or None if it is done."""
        return self._app_service.next_due_in_hours(mission_id)

    def set_active(self, mission_id: str, active: bool) -> None:
        """Include or exclude a mission from scheduling."""
        self._app_service.set_active(mission_id, active)

    def delete(self, mission_id: str) -> None:
        """Delete a saved mission."""
        self._app_service.delete(mission_id)

    def save_queue(self, queue: list, name: str, repeat_every_hours: int | None) -> str | None:
        """Save the row queue as a mission; return its id, or None if it was rejected."""
        return self._app_service.save_queue(queue, name, repeat_every_hours)
