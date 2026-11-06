"""Run queued row missions: navigate to each row, engage the implement, report progress.

Owns the executor thread, the cancel flag and the tool-topic publishers. Carries no UI
types: progress goes out through a callback as (level, text).
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
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

ARRIVED = 'arrived'


@dataclass(frozen=True)
class MissionResult:
    """How a mission ended.

    steps_done counts whole steps (one entry-to-exit traverse each) that finished. stage is
    'transit' (the entry was not reached or the mission stopped between steps) or 'row' (the
    tool was enabled), and reason is 'nav', 'estop', 'cancel' or 'timeout'. Both are None when
    ok is True.
    """
    ok: bool
    steps_done: int
    stage: str | None = None
    reason: str | None = None


def _failure_reason(outcome: bool | str) -> str | None:
    """Return None for an arrival, otherwise why navigation did not arrive.

    A navigate callable returns True or ARRIVED on arrival, False for a plain failure ('nav'),
    or the reason as a string.
    """
    if outcome is True or outcome == ARRIVED:
        return None
    if outcome is False:
        return 'nav'
    return str(outcome)


class MissionError(Exception):
    """A mission could not be started."""


def resolve_steps(topo_doc, queue: list) -> list[MissionStep]:
    """Resolve queued (row, action, params) tuples to entry and exit nodes.

    row is an integer row id, an entry node name, or a saved ROW_<id>_IN fallback.
    A name resolves through the entry's row_id, so its exit is found the same way.
    Return (row_id, entry_name, exit_name, action, params) tuples in queue order,
    preserving repeated rows; an empty queue returns an empty list when a map is loaded.
    Map row IDs are converted to integers; distinct entries for one row are rejected.
    The last exit node for each row wins. Entry names take precedence over row ID aliases.
    Raises MissionError if there is no map, a name is not an entry node, or any queued row
    lacks an entry or exit node.
    """
    if not topo_doc:
        raise MissionError('no topology map loaded')
    row_entry: dict[int, str] = {}
    row_exit: dict[int, str] = {}
    entry_row: dict[str, int] = {}
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
            if rid_int in row_entry and row_entry[rid_int] != node.name:
                raise MissionError(f'multiple entry nodes for row {rid_int}')
            row_entry[rid_int] = node.name
            entry_row[node.name] = rid_int
        elif role == 'exit':
            row_exit[rid_int] = node.name
    row_aliases = {alias: rid for rid in row_entry for alias in (str(rid), f'ROW_{rid}_IN')}
    row_aliases.update(entry_row)
    unknown = [row for row, _, _ in queue if isinstance(row, str) and row not in row_aliases]
    if unknown:
        raise MissionError(f'unknown entry node(s) {unknown}')
    resolved = [(row_aliases[row] if isinstance(row, str) else row, act, params)
                for row, act, params in queue]
    missing = sorted({rid for rid, _, _ in resolved if rid not in row_entry or rid not in row_exit})
    if missing:
        raise MissionError(f'incomplete row nodes for row(s) {missing}')
    return [(rid, row_entry[rid], row_exit[rid], act, params) for rid, act, params in resolved]


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
        """Whether cancel() was called since the last mission start or worker cleanup."""
        return self._cancel

    def cancel(self) -> bool:
        """Ask a running mission to stop before its next goal; return False if none is running.

        Cancelling the active goal is the caller's job, and only when this returns True.
        """
        with self._lock:
            if not self._running:
                return False
            self._cancel = True
            return True

    def start(self, steps: list[MissionStep], navigate: Callable[[str], bool | str],
              on_status: StatusCallback, estop_active: Callable[[], bool],
              on_result: Callable[[MissionResult], None] | None = None,
              on_step_done: Callable[[int], None] | None = None) -> None:
        """Start executing steps on a daemon thread.

        steps contains (row_id, entry_node, exit_node, action_key, action_params) tuples.
        navigate(node) must block until navigation ends. It returns True or 'arrived' on
        arrival, False for a plain failure, or a reason string ('nav', 'timeout', 'cancel',
        'estop') for a failure.
        estop_active() and the cancel flag are checked before each leg; interrupting
        an active goal is the caller's responsibility.
        on_status(level, text) runs first on the calling thread, then on the worker,
        with levels INFO, PROGRESS, WARN, ERROR, or OK.
        on_step_done(n) runs on the worker as each step finishes, with the count so far.
        on_result runs once on the worker when the mission ends, however it ends, before the
        running flag clears. Errors in on_step_done and on_result are logged and swallowed.
        Empty steps report completion without navigating.

        Raises MissionError if a mission is already running.
        Initial status callback and thread startup errors propagate after clearing running.
        """
        with self._lock:
            if self._running:
                raise MissionError('mission already running')
            self._running = True
            self._cancel = False
        try:
            on_status(INFO, f'Starting {len(steps)} step(s)…')
            threading.Thread(
                target=self._execute,
                args=(steps, navigate, on_status, estop_active, on_result, on_step_done),
                daemon=True).start()
        except BaseException:
            with self._lock:
                self._running = False
            raise

    def _stop_reason(self, estop_active: Callable[[], bool]) -> str | None:
        """Return 'cancel' or 'estop' if the mission must stop before its next leg, else None."""
        if self._cancel:
            return 'cancel'
        if estop_active():
            return 'estop'
        return None

    def _notify(self, callback: Callable | None, *args) -> None:
        """Call an optional callback, logging instead of raising so a store error cannot kill a run."""
        if callback is None:
            return
        try:
            callback(*args)
        except Exception as exc:  # pylint: disable=broad-except
            self._ros.get_logger().error(f'mission callback failed: {exc}')

    def _execute(self, steps, navigate, on_status, estop_active,
                 on_result=None, on_step_done=None) -> None:
        """Navigate to each entry, then request tool activation for the exit traversal.

        Check cancel and soft e-stop before each leg, and stop on navigation failure.
        Attempt tool deactivation after exit navigation, even if navigation raises.
        Execution exceptions are reported through on_status(ERROR, text); errors from
        that error callback propagate out of the worker.
        Always deliver the result, then clear the running and cancel flags on exit.
        """
        ok, done, stage, reason = True, 0, None, None
        try:
            for idx, (rid, entry, exit_, action, params) in enumerate(steps):
                tag = f'[{idx + 1}/{len(steps)}]'
                stage = 'transit'
                reason = self._stop_reason(estop_active)
                if reason:
                    on_status(WARN, 'Cancelled')
                    ok = False
                    break
                adef = ACTIONS.get(action)
                label = f'{adef.icon} {adef.label}' if adef else action

                on_status(PROGRESS, f'{tag} Row {rid} → transit to {entry}')
                reason = _failure_reason(navigate(entry))
                if reason:
                    on_status(ERROR, f'Row {rid}: transit to {entry} failed — stopping mission')
                    ok = False
                    break
                reason = self._stop_reason(estop_active)
                if reason:
                    on_status(WARN, 'Cancelled')
                    ok = False
                    break

                stage = 'row'
                on_status(PROGRESS, f'{tag} Row {rid} {label} → {exit_}')
                self.publish_tool_msgs(action, params, enable=True)
                try:
                    arrived = navigate(exit_)
                finally:
                    self.publish_tool_msgs(action, params, enable=False)
                reason = _failure_reason(arrived)
                if reason:
                    on_status(ERROR, f'Row {rid}: traversal to {exit_} failed — stopping mission')
                    ok = False
                    break
                done += 1
                self._notify(on_step_done, done)
            if ok:
                stage = None
                on_status(OK, f'Mission complete — {len(steps)} step(s) done ✓')
        except Exception as exc:  # pylint: disable=broad-except
            self._ros.get_logger().error(f'mission executor failed: {exc}')
            ok, reason = False, 'nav'
            on_status(ERROR, f'ERROR: mission aborted: {exc}')
        finally:
            self._notify(on_result, MissionResult(ok, done, stage, reason))
            with self._lock:
                self._running = False
                self._cancel = False

    def get_tool_publisher(self, topic: str, is_float: bool = False):
        """Return a cached tool publisher: Float64 if is_float, else Bool.

        Cache separately by topic and message type, creating publishers with depth 1.
        Gateway creation errors propagate and leave the publisher uncached.
        """
        key = (topic, is_float)
        if key not in self._tool_publishers:
            self._tool_publishers[key] = self._ros.create_publisher(
                Float64 if is_float else Bool, topic, 1)
        return self._tool_publishers[key]

    def publish_tool_msgs(self, action_key: str, action_params: dict | None, enable: bool) -> None:
        """Publish the action's tool commands to enable or disable its implement.

        Boolean values use Bool; numeric values and numeric strings use Float64.
        Unknown actions and actions without tool topics publish nothing.
        action_params is forwarded to action_ros_msgs, which currently ignores it.
        Per-topic conversion and publication errors are caught so later topics continue;
        errors from action_ros_msgs itself propagate.
        """
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
