"""Run queued row missions: navigate to each row, engage the implement, report progress.

Owns the executor thread, the cancel flag and the tool-topic publishers. Carries no UI
types: progress goes out through a callback as (level, text).
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from std_msgs.msg import Bool, Float64

from devkit_ui.actions import ACTIONS, action_ros_msgs

if TYPE_CHECKING:
    from devkit_ui.ros_gateway import RosGateway

INFO = 'info'
PROGRESS = 'progress'
WARN = 'warn'
ERROR = 'error'
OK = 'ok'

NAV_TIMEOUT_SEC = 300.0

# (row_id, entry_node, exit_node, action_key, action_params)
MissionStep = tuple[int, str, str, str, dict | None]
StatusCallback = Callable[[str, str], None]


class MissionError(Exception):
    """A mission could not be started."""


def resolve_steps(topo_doc, queue: list) -> list[MissionStep]:
    """Resolve queued (row_id, action, params) tuples to entry and exit nodes.

    Raises MissionError if there is no map or any queued row lacks an entry or exit node.
    """
    if not topo_doc:
        raise MissionError('no topology map loaded')
    row_entry: dict[int, str] = {}
    row_exit: dict[int, str] = {}
    for node in topo_doc.nodes:
        rid = node.meta.get('row_id')
        role = node.meta.get('row_role', '')
        if rid is None:
            continue
        try:
            rid_int = int(rid)
        except (TypeError, ValueError):
            continue
        if role == 'entry':
            row_entry[rid_int] = node.name
        elif role == 'exit':
            row_exit[rid_int] = node.name
    missing = sorted({rid for rid, _, _ in queue if rid not in row_entry or rid not in row_exit})
    if missing:
        raise MissionError(f'incomplete row nodes for row(s) {missing}')
    return [(rid, row_entry[rid], row_exit[rid], act, params) for rid, act, params in queue]


class MissionDomainService:
    """Execute a mission on a worker thread, one at a time."""

    def __init__(self, ros: RosGateway) -> None:
        """Store the gateway and initialise executor state and the publisher cache."""
        self._ros = ros
        self._tool_publishers: dict = {}
        self._lock = threading.Lock()
        self._running = False
        self._cancel = False

    @property
    def running(self) -> bool:
        """Whether a mission is executing."""
        return self._running

    @property
    def cancel_requested(self) -> bool:
        """Whether cancel() was called for the current mission."""
        return self._cancel

    def cancel(self) -> None:
        """Ask the executor to stop before its next goal. Cancelling the active goal is the caller's job."""
        self._cancel = True

    def start(self, steps: list[MissionStep], navigate: Callable[[str], bool],
              on_status: StatusCallback, estop_active: Callable[[], bool]) -> None:
        """Start executing steps on a daemon thread.

        navigate(node) must block until arrival and return True only on arrival.
        estop_active() is polled between legs. Raises MissionError if one is already running.
        """
        with self._lock:
            if self._running:
                raise MissionError('mission already running')
            self._running = True
            self._cancel = False
        on_status(INFO, f'Starting {len(steps)} step(s)…')
        threading.Thread(
            target=self._execute, args=(steps, navigate, on_status, estop_active),
            daemon=True).start()

    def _execute(self, steps, navigate, on_status, estop_active) -> None:
        """Run each step: transit to entry with the implement off, then traverse to exit with it on.

        Stop on cancel, soft e-stop or navigation failure. Always disengage the implement
        after a traversal and always clear the running state.
        """
        try:
            ok = True
            for idx, (rid, entry, exit_, action, params) in enumerate(steps):
                tag = f'[{idx + 1}/{len(steps)}]'
                if self._cancel or estop_active():
                    on_status(WARN, 'Cancelled')
                    ok = False
                    break
                adef = ACTIONS.get(action)
                label = f'{adef.icon} {adef.label}' if adef else action

                on_status(PROGRESS, f'{tag} Row {rid} → transit to {entry}')
                if not navigate(entry):
                    on_status(ERROR, f'Row {rid}: transit to {entry} failed — stopping mission')
                    ok = False
                    break
                if self._cancel or estop_active():
                    on_status(WARN, 'Cancelled')
                    ok = False
                    break

                on_status(PROGRESS, f'{tag} Row {rid} {label} → {exit_}')
                self.publish_tool_msgs(action, params, enable=True)
                try:
                    arrived = navigate(exit_)
                finally:
                    self.publish_tool_msgs(action, params, enable=False)
                if not arrived:
                    on_status(ERROR, f'Row {rid}: traversal to {exit_} failed — stopping mission')
                    ok = False
                    break
            if ok:
                on_status(OK, f'Mission complete — {len(steps)} step(s) done ✓')
        except Exception as exc:  # pylint: disable=broad-except
            self._ros.get_logger().error(f'mission executor failed: {exc}')
            on_status(ERROR, f'ERROR: mission aborted: {exc}')
        finally:
            with self._lock:
                self._running = False
                self._cancel = False

    def get_tool_publisher(self, topic: str, is_float: bool = False):
        """Return (creating if needed) the publisher for a tool topic: Float64 if is_float, else Bool."""
        key = (topic, is_float)
        if key not in self._tool_publishers:
            self._tool_publishers[key] = self._ros.create_publisher(
                Float64 if is_float else Bool, topic, 1)
        return self._tool_publishers[key]

    def publish_tool_msgs(self, action_key: str, action_params: dict | None, enable: bool) -> None:
        """Publish every (topic, value) pair for the action. Per-topic errors are logged, not raised."""
        for topic, value in action_ros_msgs(action_key, action_params, enable):
            try:
                if isinstance(value, bool):
                    pub = self.get_tool_publisher(topic, is_float=False)
                    msg = Bool()
                    msg.data = value
                else:
                    if not isinstance(value, (int, float, str)):
                        raise TypeError(f'unsupported tool value type: {type(value)!r}')
                    pub = self.get_tool_publisher(topic, is_float=True)
                    msg = Float64()
                    msg.data = float(value)
                pub.publish(msg)
            except Exception as exc:  # pylint: disable=broad-except
                self._ros.get_logger().warn(
                    f'publish_tool_msgs({topic}, enable={enable}): {exc}')
