"""Presentation of the System page tools: status wording and its severity."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from devkit_ui.constants import TOOL_GRAPHER, TOOL_RVIZ

if TYPE_CHECKING:
    from devkit_ui.application_services.system_application_service import SystemApplicationService
    from devkit_ui.application_services.tool_state import ToolState

Level = Literal['ok', 'error', 'muted']


@dataclass(frozen=True)
class ToolStatusView:
    """Text for a tool's status label and how to colour it; a level of None keeps the current colour."""

    text: str
    level: Level | None = None


class SystemViewModel:
    """UI-facing intents for the tools on the System page.

    start, stop, status and exited return None when the action was ignored or there is nothing
    to show, and the view then leaves its label alone.
    """

    def __init__(self, service: SystemApplicationService) -> None:
        """Bind to the system service."""
        self._service = service

    def start(self, tool: str, host: str) -> ToolStatusView | None:
        """Start a tool; host is the browser's hostname, used only in the wording of the status.

        Return None if a start or stop is already in flight. Blocking, so call off the UI loop.
        """
        return self._view(self._service.start(tool), tool, host)

    def stop(self, tool: str) -> ToolStatusView | None:
        """Stop a tool; return None if a start or stop is already in flight.

        Blocking, so call off the UI loop.
        """
        return self._view(self._service.stop(tool), tool)

    def status(self, tool: str) -> ToolStatusView | None:
        """Return the status to show when a page is built, or None if the tool is not running."""
        return self._view(self._service.status(tool), tool)

    def exited(self, tool: str) -> ToolStatusView | None:
        """Return the status of a tool that has died since it started, else None."""
        state = self._service.exited(tool)
        return self._view(state, tool) if state.kind == 'exited' else None

    @staticmethod
    def stopping() -> ToolStatusView:
        """Return the interim status to show while a stop is in progress."""
        return ToolStatusView('stopping')

    @staticmethod
    def _view(state: ToolState, tool: str, host: str = '') -> ToolStatusView | None:
        """Word a service result; None for the kinds that change nothing on screen."""
        kind = state.kind
        if kind == 'busy':
            return None
        if kind == 'started':
            return ToolStatusView(_started_text(state, tool, host), 'ok')
        if kind == 'already_running':
            return ToolStatusView('already running')
        if kind == 'running':
            return ToolStatusView(f'running (pid {state.pid})', 'ok')
        if kind == 'stopped':
            return ToolStatusView('stopped', 'muted')
        if kind == 'exited':
            see = f' - see {state.log}' if state.log else ''
            return ToolStatusView(f'exited ({state.detail}){see}', 'error')
        if kind == 'error':
            return ToolStatusView(f'ERROR: {state.detail}', 'error')
        return None  # idle


def _started_text(state: ToolState, tool: str, host: str) -> str:
    """Return 'started (pid N)' plus the tool's hint, RViz note and log path where it has them."""
    text = f'started (pid {state.pid})'
    if tool == TOOL_GRAPHER:
        text += f' - open http://{host}:8888 (scan takes a few seconds)'
    if tool == TOOL_RVIZ and state.detail:
        text += f' ({state.detail})'
    if state.log:
        text += f' - log: {state.log}'
    return text
