"""Presentation of the Sim page processes: status wording and its severity."""
from __future__ import annotations

from typing import TYPE_CHECKING

# Re-exported so pages can name the launch modes without importing the application service layer.
from devkit_ui.application_services.sim_process_service import GazeboMode
from devkit_ui.view_models.system_view_model import ToolStatusView

if TYPE_CHECKING:
    from devkit_ui.application_services.sim_process_service import SimProcessApplicationService
    from devkit_ui.application_services.tool_state import ToolState

__all__ = ['GazeboMode', 'SimProcessViewModel']


class SimProcessViewModel:
    """UI-facing intents for the Gazebo and row-follow cards.

    Every call returns the status to show. Starts and stops block, so call them off the UI loop.
    """

    def __init__(self, service: SimProcessApplicationService) -> None:
        """Bind to the sim process service."""
        self._service = service

    def row_follow_status(self) -> ToolStatusView | None:
        """Return the status to show when a page is built, or None if it is not running."""
        return self._running(self._service.row_follow_status())

    def start_row_follow(self) -> ToolStatusView | None:
        """Start the row-follow stack in simulation."""
        state = self._service.start_row_follow()
        if state.kind == 'started':
            return ToolStatusView(f'running — pid {state.pid} · log: {state.log}', 'ok')
        return self._view(state)

    def stop_row_follow(self) -> ToolStatusView | None:
        """Stop the row-follow stack."""
        return self._view(self._service.stop_row_follow())

    def gazebo_running(self) -> bool:
        """Return whether a simulation launch is still alive."""
        return self._service.gazebo_running()

    def gazebo_status(self) -> ToolStatusView | None:
        """Return the status to show when a page is built, or None if the sim is not running."""
        return self._running(self._service.gazebo_status())

    def launch_gazebo(self, xacro: str, mode: GazeboMode = 'normal') -> ToolStatusView | None:
        """Launch the sim for the robot model xacro; browser mode also starts the display."""
        state = self._service.launch_gazebo(xacro, mode)
        if state.kind != 'started':
            return self._view(state)
        label = {'headless': 'sim launching (headless)',
                 'browser': 'browser mode'}.get(mode, 'sim launching')
        return ToolStatusView(f'{label} — {xacro} — pid {state.pid}', 'ok')

    def stop_gazebo(self) -> ToolStatusView | None:
        """Stop the sim and its display daemons."""
        return self._view(self._service.stop_gazebo())

    @classmethod
    def _running(cls, state: ToolState) -> ToolStatusView | None:
        """Word a status poll; only a live process has anything to show."""
        return cls._view(state) if state.kind == 'running' else None

    @staticmethod
    def _view(state: ToolState) -> ToolStatusView | None:
        """Word a service result; None when there is nothing to show."""
        kind = state.kind
        if kind == 'busy':
            return None
        if kind == 'already_running':
            return ToolStatusView('already running', 'muted')
        if kind == 'running':
            return ToolStatusView(f'running (pid {state.pid})', 'ok')
        if kind == 'stopped':
            return ToolStatusView('stopped', 'muted')
        if kind == 'error':
            return ToolStatusView(f'ERROR: {state.detail}', 'error')
        if kind == 'idle':
            return ToolStatusView('not running', 'muted')
        return None
