"""Use cases for the Sim page processes: the Gazebo simulation and the row-follow stack.

Handles live in process_registry, so every browser session sees the same process and a page
reload cannot orphan a running sim or start a second one. This owns the launch commands and
environment; pages only ask for a start, a stop or a status. No ROS or NiceGUI imports.
"""
from __future__ import annotations

import os
from typing import Literal

from devkit_ui.application_services.tool_state import ToolState
from devkit_ui.constants import SIM_MAP_FILE
from devkit_ui.process_registry import (
    alive,
    kill_group,
    shared,
    spawn_logged,
    start_vnc_stack,
    stop_all,
)

GazeboMode = Literal['normal', 'headless', 'browser']

GAZEBO_LOG = '/tmp/gazebo_sim.log'
ROW_FOLLOW_LOG = '/tmp/neo_sim.log'
_ROW_FOLLOW_CMD = ['ros2', 'launch', 'devkit_bringup', 'neo.launch.py',
                   'use_camera:=false', 'detector:=tsm', 'image_topic:=/camera/image_raw']
_VNC_DISPLAY = ':99'
_VNC_PORT = 5900
_NOVNC_PORT = 6080
# Forest3D-generated models (model://ground, model://crop/plant) live in /workspace/models; it must
# come first or gz sim aborts world load with "Unable to find uri".
_GZ_RESOURCES = ('/workspace/models'
                 ':/workspace/install/virtual_maize_field/share/virtual_maize_field/models')


class SimProcessApplicationService:
    """Start, stop and poll the Gazebo simulation and the row-follow stack."""

    # ── row follow ────────────────────────────────────────────────────────────

    def row_follow_status(self) -> ToolState:
        """Return 'running' for a live row-follow process and 'idle' otherwise."""
        return self._status('neo', ROW_FOLLOW_LOG)

    def start_row_follow(self) -> ToolState:
        """Launch neo.launch.py with the Gazebo-bridged camera unless it is already running.

        It uses /camera/image_raw instead of a V4L2 device, runs the TSM detector and publishes
        /cmd_vel. Return 'already_running', 'error' with the exception text (the handle is left
        as it was), or 'started'.
        """
        handle: list = shared('neo', lambda: [None])
        if alive(handle[0]):
            return ToolState('already_running', handle[0].pid, ROW_FOLLOW_LOG)
        try:
            proc = spawn_logged(_ROW_FOLLOW_CMD, ROW_FOLLOW_LOG, env=os.environ.copy())
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return ToolState('error', detail=str(exc))
        handle[0] = proc
        return ToolState('started', proc.pid, ROW_FOLLOW_LOG)

    def stop_row_follow(self) -> ToolState:
        """Stop the row-follow process group; 'idle' if nothing was started.

        Blocking: call off the UI loop.
        """
        handle: list = shared('neo', lambda: [None])
        if handle[0] is None:
            return ToolState('idle')
        kill_group(handle[0])
        handle[0] = None
        return ToolState('stopped')

    # ── gazebo ────────────────────────────────────────────────────────────────

    def gazebo_running(self) -> bool:
        """Return whether a simulation launch is still alive."""
        return alive(shared('gazebo', lambda: [None])[0])

    def gazebo_status(self) -> ToolState:
        """Return 'running' for a live simulation and 'idle' otherwise."""
        return self._status('gazebo', GAZEBO_LOG)

    def launch_gazebo(self, xacro: str, mode: GazeboMode = 'normal') -> ToolState:
        """Launch sowbot_sim.launch.py for the robot model xacro unless a sim is running.

        This is the whole stack: sowbot_sim.launch.py runs preflight_pkill, fusioncore and
        kill_bootstrap_tfs, which the old Launch World and Spawn Robot split skipped, leaving stale
        wall-time TFs fighting the sim-time ones. Do not reintroduce that split.

        mode 'browser' first starts a virtual display with VNC and noVNC, and blocks while it does;
        call off the UI loop. Return 'already_running', 'error' with the exception text (a failed
        display stack means Gazebo is not started and the handle stays empty), or 'started' with
        the mode in detail.
        """
        handle: list = shared('gazebo', lambda: [None])
        if alive(handle[0]):
            return ToolState('already_running', handle[0].pid, GAZEBO_LOG)
        try:
            if mode == 'browser':
                start_vnc_stack(_VNC_DISPLAY, _VNC_PORT, _NOVNC_PORT,
                                shared('gazebo_daemons', list))
                proc = spawn_logged(self._browser_cmd(xacro), GAZEBO_LOG,
                                    env=self._browser_env())
            else:
                proc = spawn_logged(self._sim_cmd(xacro, headless=mode == 'headless'),
                                    GAZEBO_LOG, env=self._env())
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return ToolState('error', detail=str(exc))
        handle[0] = proc
        return ToolState('started', proc.pid, GAZEBO_LOG, mode)

    def stop_gazebo(self) -> ToolState:
        """Stop the sim, the robot spawn and the display daemons, and release their handles.

        Blocking: call off the UI loop.
        """
        for name in ('gazebo', 'gazebo_spawn'):
            handle: list = shared(name, lambda: [None])
            kill_group(handle[0])
            handle[0] = None
        stop_all(shared('gazebo_daemons', list))
        return ToolState('stopped')

    # ── commands ──────────────────────────────────────────────────────────────

    @staticmethod
    def _status(name: str, log: str) -> ToolState:
        """Return 'running' with the pid for a live process and 'idle' otherwise."""
        proc = shared(name, lambda: [None])[0]
        if alive(proc):
            return ToolState('running', proc.pid, log)
        return ToolState('idle')

    @staticmethod
    def _sim_cmd(xacro: str, headless: bool = False) -> list[str]:
        """Build the sowbot_sim launch command for the robot model xacro."""
        return ['ros2', 'launch', 'devkit_bringup', 'sowbot_sim.launch.py',
                'world:=maize.world', f'urdf:={xacro}',
                f'headless:={"true" if headless else "false"}']

    @staticmethod
    def _env() -> dict[str, str]:
        """Return the environment for the sim: the map file and the model search path."""
        extra = os.environ.get('GZ_SIM_RESOURCE_PATH')
        return {**os.environ, 'TMAP2_FILE': SIM_MAP_FILE,
                'GZ_SIM_RESOURCE_PATH': _GZ_RESOURCES + (f':{extra}' if extra else '')}

    def _browser_env(self) -> dict[str, str]:
        """Return the sim environment for the virtual display, with software GL forced.

        Xvfb has no DRI/GLX driver, so it cannot honour the hardware-render settings the container
        was started with. See docs/research/Sim.md.
        """
        return {**self._env(), 'DISPLAY': _VNC_DISPLAY, 'LIBGL_ALWAYS_SOFTWARE': '1',
                'GALLIUM_DRIVER': 'llvmpipe', 'MESA_LOADER_DRIVER_OVERRIDE': 'llvmpipe'}

    def _browser_cmd(self, xacro: str) -> list[str]:
        """Wrap the sim command so it cannot see /dev/dri.

        Environment variables alone are not enough for gz-sim: its Ogre2 renderer initialises via
        EGL_EXT_platform_device, which enumerates /dev/dri and picks a real GPU node, and Mesa then
        refuses to force software rendering on an explicitly selected hardware device. The
        container runs privileged with /dev mounted, so a render node is always visible. Hiding
        /dev/dri from this subprocess alone with a private mount namespace is what gz-sim's own CI
        does on GPU-less runners.
        """
        quoted = ' '.join("'" + a.replace("'", "'\\''") + "'" for a in self._sim_cmd(xacro))
        return ['unshare', '--mount', '--propagation', 'private', '--', 'bash', '-c',
                f'mount -t tmpfs tmpfs /dev/dri 2>/dev/null; exec {quoted}']
