"""Use cases for the System page tools: Graph Explorer, ros2grapher, RViz and the Medkit gateway.

This owns starting, stopping and polling the tool processes. Handles live in process_registry, so
every browser session sees the same process. A per-tool lock turns an overlapping start or stop
into a 'busy' result instead of a second process or a half-stopped one. No ROS or NiceGUI imports.
"""
from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# pylint: disable-next=import-error
from ament_index_python.packages import PackageNotFoundError, get_package_share_directory

from devkit_ui.constants import TOOL_EXPLORER, TOOL_GRAPHER, TOOL_MEDKIT, TOOL_RVIZ
from devkit_ui.process_registry import (
    alive,
    interrupt_stop,
    kill_group,
    shared,
    spawn_logged,
    spawn_quiet,
    start_vnc_stack,
    stop_all,
)

_EXPLORER_CMD = ['ros2', 'run', 'ros2graph_explorer', 'ros2graph_explorer']
_GRAPHER_DIR = '/tmp/ros2grapher'
_GRAPHER_CMD = ['ros2grapher', '/workspace', '-o', f'{_GRAPHER_DIR}/index.html', '--port', '8888']
_MEDKIT_CMD = ['ros2', 'launch', 'ros2_medkit_gateway', 'bringup.launch.py', 'server_host:=127.0.0.1']
# Seconds to wait after SIGINT, after terminate and after kill; ROS launch needs time to stop its children.
_MEDKIT_WAITS = (15, 5, 5)
_RVIZ_DISPLAY = ':98'
_NO_TOPO_CONFIG = 'no topo config found'
_LOGS = {
    TOOL_EXPLORER: '/tmp/ros2graph_explorer.log',
    TOOL_GRAPHER: '/tmp/ros2grapher.log',
    TOOL_RVIZ: '/tmp/rviz.log',
    TOOL_MEDKIT: '',  # Medkit's output is discarded
}


@dataclass(frozen=True)
class ToolState:
    """What a tool call did or found.

    kind is one of 'started', 'already_running', 'running', 'stopped', 'exited', 'error', 'busy'
    and 'idle'. pid is set for started, already_running, running and exited; log is the tool's log
    path ('' if it has none); detail is the exception text for 'error', the return code for 'exited'
    and the RViz note for 'started'.
    """

    kind: str
    pid: int | None = None
    log: str = ''
    detail: str = ''


class SystemApplicationService:
    """Start, stop and poll the System page tools; the tool keys are the TOOL_* constants."""

    def __init__(self) -> None:
        """Use one shared lock per tool across service instances."""
        self._locks = {tool: shared(f'{tool}_lock', threading.Lock) for tool in _LOGS}
        self._launchers: dict[str, Callable[[], tuple[Any, str]]] = {
            TOOL_EXPLORER: self._launch_explorer,
            TOOL_GRAPHER: self._launch_grapher,
            TOOL_RVIZ: self._launch_rviz,
            TOOL_MEDKIT: self._launch_medkit,
        }

    def start(self, tool: str) -> ToolState:
        """Start tool unless it is running or another start or stop is in flight.

        Return 'busy' while the tool's lock is held, 'already_running' for a live process, 'error'
        with the exception text if launching fails, else 'started'. A failed launch leaves the
        handle as it was. Blocking for RViz, which starts the display stack: call off the UI loop.
        """
        lock = self._locks[tool]
        if not lock.acquire(blocking=False):
            return ToolState('busy')
        try:
            handle: list = shared(tool, lambda: [None])
            if alive(handle[0]):
                return ToolState('already_running', handle[0].pid, _LOGS[tool])
            try:
                proc, detail = self._launchers[tool]()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return ToolState('error', detail=str(exc))
            handle[0] = proc
            return ToolState('started', proc.pid, _LOGS[tool], detail)
        finally:
            lock.release()

    def stop(self, tool: str) -> ToolState:
        """Stop tool's process group, and for RViz its display daemons, then release the handle.

        Return 'busy' while another start or stop is in flight, 'stopped' on success (also when
        nothing was running), or 'error' with the text of an OS error or timeout, in which case the
        handle is kept so the stop can be retried. Blocking: call off the UI loop.
        """
        lock = self._locks[tool]
        if not lock.acquire(blocking=False):
            return ToolState('busy')
        try:
            handle: list = shared(tool, lambda: [None])
            try:
                self._terminate(tool, handle[0])
            except (OSError, subprocess.TimeoutExpired) as exc:
                return ToolState('error', detail=str(exc))
            handle[0] = None
            return ToolState('stopped')
        finally:
            lock.release()

    def status(self, tool: str) -> ToolState:
        """Return 'running' for a live process and 'idle' otherwise; used when a page is built."""
        proc = shared(tool, lambda: [None])[0]
        if alive(proc):
            return ToolState('running', proc.pid, _LOGS[tool])
        return ToolState('idle')

    def exited(self, tool: str) -> ToolState:
        """Return 'exited' with the return code if a started process has since died.

        Return 'busy' while a start or stop is in flight, 'running' for a live process and 'idle'
        if nothing was started. The dead handle is kept, so the next start replaces it.
        """
        lock = self._locks[tool]
        if not lock.acquire(blocking=False):
            return ToolState('busy')
        try:
            proc = shared(tool, lambda: [None])[0]
            if proc is None:
                return ToolState('idle')
            returncode = proc.poll()
            if returncode is None:
                return ToolState('running', proc.pid, _LOGS[tool])
            return ToolState('exited', proc.pid, _LOGS[tool], str(returncode))
        finally:
            lock.release()

    # ── launching ─────────────────────────────────────────────────────────────

    def _launch_explorer(self) -> tuple[Any, str]:
        """Start Graph Explorer and return its process with no note."""
        return spawn_logged(_EXPLORER_CMD, _LOGS[TOOL_EXPLORER]), ''

    def _launch_grapher(self) -> tuple[Any, str]:
        """Start ros2grapher and return its process with no note.

        ros2grapher serves its output directory over plain HTTP on all interfaces. Give it a
        private directory holding only index.html so it cannot expose the workspace (.env etc).
        A failure to create the directory propagates and nothing is spawned.
        """
        os.makedirs(_GRAPHER_DIR, exist_ok=True)
        return spawn_logged(_GRAPHER_CMD, _LOGS[TOOL_GRAPHER], cwd=_GRAPHER_DIR), ''

    def _launch_rviz(self) -> tuple[Any, str]:
        """Start the display stack, then RViz on it; return the process and a note if no config.

        Use the topology RViz configuration when its ROS package is available, which has
        MarkerArray displays pre-wired to /topological_map_visualisation et al. Otherwise launch
        without -d. If the display stack fails to start, the error propagates and RViz is not spawned.
        """
        try:
            config = os.path.join(get_package_share_directory('topological_navigation'),
                                  'rviz', 'topological_navigation.rviz')
        except PackageNotFoundError:
            config = None
        start_vnc_stack(_RVIZ_DISPLAY, 5901, 6081, shared('rviz_daemons', list))
        args = ['ros2', 'run', 'rviz2', 'rviz2']
        if config is not None:
            args += ['-d', config]
        proc = spawn_logged(args, _LOGS[TOOL_RVIZ], env={**os.environ, 'DISPLAY': _RVIZ_DISPLAY})
        return proc, '' if config else _NO_TOPO_CONFIG

    def _launch_medkit(self) -> tuple[Any, str]:
        """Start the Medkit gateway on loopback and return its process with no note.

        It gets its own session: it is registered for shutdown_tools, which signals the whole
        group, and a child in the UI's own group would make that signal the UI itself.
        """
        return spawn_quiet(_MEDKIT_CMD), ''

    # ── stopping ──────────────────────────────────────────────────────────────

    def _terminate(self, tool: str, proc: Any) -> None:
        """Stop proc the way tool needs; a missing handle is a no-op, other than RViz's display."""
        if tool == TOOL_MEDKIT:
            # SIGINT to the launch leader lets it shut down its children; SIGTERM would not.
            if proc is not None:
                interrupt_stop(proc, *_MEDKIT_WAITS)
            return
        kill_group(proc)
        if tool == TOOL_RVIZ:
            stop_all(shared('rviz_daemons', list))
