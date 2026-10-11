from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from devkit_ui.domain_services.mission_domain_service import MissionDomainService
    from devkit_ui.missions import MissionStore
    from devkit_ui.models import TopoDoc


class MissionApplicationService:
    """Use cases for saved missions: list, save a queue, toggle, delete, and report status."""

    def __init__(self, store: MissionStore, mission_service: MissionDomainService,
                 get_doc: Callable[[], TopoDoc | None]) -> None:
        """Store the mission store, the executor service and a getter for the live map."""
        self._store = store
        self._mission_service = mission_service
        self._get_doc = get_doc

    @property
    def missions(self) -> tuple[dict, ...]:
        """Return the saved missions snapshot."""
        return self._store.missions

    @property
    def version(self) -> int:
        """Return a counter that increases whenever the saved missions change."""
        return self._store.version

    @property
    def status(self) -> str:
        """Return the last store action's status text; an error starts with 'ERROR'."""
        return self._store.status

    @property
    def running(self) -> bool:
        """Return whether a mission is executing."""
        return self._mission_service.running

    def next_due_in_hours(self, mission_id: str) -> float | None:
        """Return hours until the mission is due, 0.0 if due now, or None if it is done."""
        return self._store.next_due_in_hours(mission_id)

    def set_active(self, mission_id: str, active: bool) -> None:
        """Include or exclude a mission from scheduling. Failures are reported in status."""
        self._store.set_active(mission_id, active)

    def delete(self, mission_id: str) -> None:
        """Delete a mission. Failures are reported in status."""
        self._store.delete(mission_id)

    def save_queue(self, queue: list, name: str, repeat_every_hours: int | None) -> str | None:
        """Save a row queue as a mission and return its id, or None if it was rejected.

        queue holds (row_id, action_key, action_params) tuples. Each row is stored as its
        entry node name in the live map, or ROW_<id>_IN when the map has no such node.
        The store has no per-step actions yet, so the first step's action and parameters
        apply to the whole mission. An empty queue or a store validation error is
        reported in status.
        """
        if not queue:
            self._store.set_status('ERROR: queue is empty')
            return None
        doc = self._get_doc()
        nodes = doc.nodes if doc is not None else []
        rows = [
            next((nd.name for nd in nodes
                  if nd.meta.get('row_id') == rid and nd.meta.get('row_role') == 'entry'),
                 f'ROW_{rid}_IN')
            for rid, _, _ in queue
        ]
        return self._store.add(
            rows=rows, action=queue[0][1], action_params=queue[0][2],
            name=name or '', repeat_every_hours=repeat_every_hours, active=True)
