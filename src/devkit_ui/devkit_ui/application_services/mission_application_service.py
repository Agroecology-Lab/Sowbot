from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from devkit_ui.domain_services.mission_domain_service import (
    ERROR,
    NAV_TIMEOUT_SEC,
    MissionError,
    MissionResult,
    resolve_steps,
)
from devkit_ui.time_utils import now_utc_str

if TYPE_CHECKING:
    from devkit_ui.application_services.drive_application_service import (
        DriveApplicationService,
    )
    from devkit_ui.application_services.navigation_application_service import (
        NavigationApplicationService,
    )
    from devkit_ui.application_services.topology_application_service import (
        TopologyApplicationService,
    )
    from devkit_ui.domain_services.mission_domain_service import MissionDomainService
    from devkit_ui.missions import MissionStore
    from devkit_ui.topo_results import NavUpdate

# Called as (level, text) with a level from mission_domain_service: INFO, PROGRESS, WARN, ERROR, OK.
StatusCallback = Callable[[str, str], None]


class MissionApplicationService:
    """Use cases for missions: list, save, toggle, delete, report status, start and cancel a run."""

    def __init__(self, store: MissionStore, mission_service: MissionDomainService,
                 topology: TopologyApplicationService, navigation: NavigationApplicationService,
                 drive: DriveApplicationService) -> None:
        """Store the collaborators a mission run needs.

        Parameters:
            store (MissionStore): Saved missions, scheduling and run recording.
            mission_service (MissionDomainService): Executes a mission on a worker thread.
            topology (TopologyApplicationService): The live map rows are resolved against.
            navigation (NavigationApplicationService): Drives the robot to each node.
            drive (DriveApplicationService): Supplies the confirmed soft estop state.
        """
        self._store = store
        self._mission_service = mission_service
        self._topology = topology
        self._navigation = navigation
        self._drive = drive

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
        """Return hours until due, 0.0 if due now, or -1.0 after a failed run.

        Return None for inactive missions, completed one-shots, or unknown IDs.
        SQLite lookup errors, including ValueError for malformed IDs, propagate.
        """
        return self._store.next_due_in_hours(mission_id)

    def set_active(self, mission_id: str, active: bool) -> None:
        """Include or exclude a mission from scheduling.

        Missing missions and validation failures are reported in status.
        SQLite lookup and write errors propagate, including ValueError for malformed IDs.
        """
        self._store.set_active(mission_id, active)

    def delete(self, mission_id: str) -> None:
        """Delete a mission, reporting an unknown ID in status.

        SQLite lookup and write errors propagate, including ValueError for malformed IDs.
        """
        self._store.delete(mission_id)

    def save_queue(self, queue: list, name: str, repeat_every_hours: int | None) -> str | None:
        """Save a row queue as a mission and return its id, or None if it was rejected.

        queue holds (row_id, action_key, action_params) tuples. Each row is stored as its
        entry node name in the live map, or ROW_<id>_IN when the map has no such node.
        All steps must have the same action and parameters, as the store applies them
        to the whole mission. An empty or mixed queue or a store validation error is
        reported in status.
        repeat_every_hours is a positive interval after success, or None for a one-shot.
        SQLite database and JSON serialization errors propagate.
        """
        if not queue:
            self._store.set_status('ERROR: queue is empty')
            return None
        _, action, action_params = queue[0]
        if any(step_action != action or step_params != action_params
               for _, step_action, step_params in queue):
            self._store.set_status(
                'ERROR: all queued steps must have the same action and parameters')
            return None
        doc = self._topology.get_doc()
        nodes = doc.nodes if doc is not None else []
        rows = [
            next((nd.name for nd in nodes
                  if nd.meta.get('row_id') == rid and nd.meta.get('row_role') == 'entry'),
                 f'ROW_{rid}_IN')
            for rid, _, _ in queue
        ]
        return self._store.add(
            rows=rows, action=action, action_params=action_params,
            name=name or '', repeat_every_hours=repeat_every_hours, active=True)

    def start(self, queue: list, on_status: StatusCallback,
              on_nav_update: Callable[[NavUpdate], None] | None = None,
              on_result: Callable[[MissionResult], None] | None = None,
              on_step_done: Callable[[int], None] | None = None) -> bool:
        """Run the queued (row_id, action_key, action_params) steps on a worker thread.

        Return True if the mission started. Report an empty queue, a running mission,
        unavailable navigation or a MissionError through on_status(ERROR, 'ERROR: ...') and
        return False without starting.
        Otherwise on_status receives progress as (level, text) first on the calling thread,
        then on the worker. on_nav_update receives each navigation update, on the worker.
        on_step_done(n) and on_result(MissionResult) run on the worker; see
        MissionDomainService.start.
        Other startup errors propagate.
        """
        if not queue:
            on_status(ERROR, 'ERROR: queue is empty')
            return False
        if self._mission_service.running:
            on_status(ERROR, 'ERROR: mission already running')
            return False
        if not self._navigation.available:
            on_status(ERROR, 'ERROR: action client unavailable')
            return False
        try:
            steps = resolve_steps(self._topology.get_doc(), queue)
            self._mission_service.start(
                steps, lambda target: self._navigate(target, on_nav_update), on_status,
                lambda: self._drive.soft_estop_active, on_result, on_step_done)
        except MissionError as exc:
            on_status(ERROR, f'ERROR: {exc}')
            return False
        return True

    def run_saved(self, mission_id: str, on_status: StatusCallback,
                  on_nav_update: Callable[[NavUpdate], None] | None = None) -> bool:
        """Run a saved mission: load it, resolve its entry nodes to steps and start().

        The Run button for saved missions, a future scheduler and remote intake share this
        path. Return True if the mission started. An unknown mission id is reported through
        on_status(ERROR, 'ERROR: ... not found'). A mission that is inactive still runs,
        because active only controls scheduling. Every other failure is reported as in start().

        The outcome is recorded in the store (SQLite only): state running with started_at when
        it starts, steps_done as each step finishes, then state ended with stage, reason and
        ended_at, and record_run for the repeat schedule. A remote mission is also made
        inactive when it ends, whatever the result. A mission that fails to start keeps the
        result it had before.
        SQLite lookup errors propagate, including ValueError for malformed IDs.
        """
        mission = self._store.find(mission_id)
        if mission is None:
            on_status(ERROR, f'ERROR: {mission_id!r} not found')
            return False
        action, params = mission['action'], mission.get('action_params') or {}
        queue = [(row, action, params) for row in mission.get('rows', ())]

        fields = ('state', 'steps_done', 'stage', 'reason', 'started_at', 'ended_at')
        before = {name: mission.get(name) for name in fields if name in mission}
        self._store.record_result(
            mission_id, state='running', steps_done=0, stage=None, reason=None,
            started_at=now_utc_str(), ended_at=None)

        def on_step_done(count: int) -> None:
            """Store the steps finished so far, so a restart reports a true count."""
            self._store.record_result(mission_id, steps_done=count)

        def on_result(result: MissionResult) -> None:
            """Store how the mission ended and re-arm its schedule."""
            self._store.record_result(
                mission_id, state='ended', steps_done=result.steps_done, stage=result.stage,
                reason=result.reason, ended_at=now_utc_str())
            self._store.record_run(mission_id, result.ok)
            if mission.get('source') == 'remote':
                self._store.set_active(mission_id, False)

        if self.start(queue, on_status, on_nav_update, on_result, on_step_done):
            return True
        if before:
            self._store.record_result(mission_id, **before)
        return False

    def cancel(self) -> None:
        """Stop the mission before its next goal, then cancel the goal in flight.

        Do nothing when no mission is running, so a manual navigation goal is left alone.
        """
        if self._mission_service.cancel():
            self._navigation.cancel()

    def _navigate(self, target: str, on_nav_update: Callable[[NavUpdate], None] | None) -> str:
        """Navigate to a node and block until it ends; call from the mission worker thread.

        Use a 300-second wait budget after sending the goal, excluding the server wait.
        A mission cancel or a confirmed soft estop requests cancellation while waiting.
        Return 'arrived', or why it did not: 'nav' (failure, rejection or unavailable
        navigation), 'timeout', 'cancel', or 'estop' when the stop came from the soft estop and
        not from a mission cancel. Synchronous navigation errors propagate to the mission worker.
        See NavigationApplicationService.navigate_and_wait_result for polling and cancellation
        timing.
        """
        result = self._navigation.navigate_and_wait_result(
            target, NAV_TIMEOUT_SEC,
            lambda: self._mission_service.cancel_requested or self._drive.soft_estop_active,
            on_nav_update)
        if (result == 'cancel' and not self._mission_service.cancel_requested
                and self._drive.soft_estop_active):
            return 'estop'
        return result
