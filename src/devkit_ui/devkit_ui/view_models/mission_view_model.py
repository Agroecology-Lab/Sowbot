from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from devkit_ui.application_services.mission_application_service import (
        MissionApplicationService,
        StatusCallback,
    )
    from devkit_ui.view_models.run_view_model import RunViewModel


class MissionQueue:
    """One browser session's queue of (row_id, action_key, action_params) steps to run or save.

    items is the list MissionViewModel.save_queue and the mission runner take. Subscribers
    are called after every change.
    """

    def __init__(self) -> None:
        """Start empty."""
        self.items: list[tuple[int, str, dict]] = []
        self._listeners: list[Callable[[], None]] = []

    def subscribe(self, listener: Callable[[], None]) -> None:
        """Call listener, with no arguments, after every change."""
        self._listeners.append(listener)

    def _notify(self) -> None:
        """Tell every subscriber that the queue changed."""
        for listener in list(self._listeners):
            listener()

    def add(self, row_id: int, action_key: str, params: dict) -> None:
        """Append a step. Duplicates are allowed: the same row may run twice with different implements."""
        self.items.append((row_id, action_key, params))
        self._notify()

    def move(self, index: int, delta: int) -> None:
        """Swap a step with its neighbour; do nothing if either position is outside the queue."""
        target = index + delta
        if 0 <= index < len(self.items) and 0 <= target < len(self.items):
            self.items[index], self.items[target] = self.items[target], self.items[index]
            self._notify()

    def remove(self, index: int) -> None:
        """Drop a step; do nothing if the position is outside the queue."""
        if 0 <= index < len(self.items):
            del self.items[index]
            self._notify()


class MissionViewModel:
    """UI-facing state and user intents for saved missions and for running a queue."""

    def __init__(self, app_service: MissionApplicationService, run_vm: RunViewModel) -> None:
        """Bind to the mission application service.

        run_vm shows navigation progress on the Run tab while a mission drives the robot.
        """
        self._app_service = app_service
        self._run_vm = run_vm

    @staticmethod
    def new_queue() -> MissionQueue:
        """Return an empty queue for one browser session's Plan tab."""
        return MissionQueue()

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
        """Return hours until due, 0.0 if due now, or -1.0 after a failed run.

        Return None for inactive missions, completed one-shots, or unknown IDs.
        SQLite lookup errors, including ValueError for malformed IDs, propagate.
        """
        return self._app_service.next_due_in_hours(mission_id)

    def set_active(self, mission_id: str, active: bool) -> None:
        """Include or exclude a mission from scheduling."""
        self._app_service.set_active(mission_id, active)

    def delete(self, mission_id: str) -> None:
        """Delete a saved mission."""
        self._app_service.delete(mission_id)

    def save_queue(self, queue: list, name: str, repeat_every_hours: int | None) -> str | None:
        """Save (row_id, action_key, action_params) tuples; return the ID or None if rejected.

        All steps must have the same action and parameters to be saved.
        repeat_every_hours is a positive interval after success, or None for a one-shot.
        See MissionApplicationService.save_queue for entry-node resolution and status behavior.
        SQLite database and JSON serialization errors propagate.
        """
        return self._app_service.save_queue(queue, name, repeat_every_hours)

    def run(self, queue: list, on_status: StatusCallback) -> None:
        """Run (row_id, action_key, action_params) steps; progress arrives as (level, text).

        See MissionApplicationService.start for the error and threading behaviour.
        """
        self._app_service.start(queue, on_status, self._run_vm.on_nav_update)

    def cancel(self) -> None:
        """Cancel the running mission and its active navigation goal."""
        self._app_service.cancel()
