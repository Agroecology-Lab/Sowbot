"""Presentation of the System page tools and the LiteFarm row plan export: wording and severity."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from devkit_ui.constants import TOOL_GRAPHER, TOOL_RVIZ
from devkit_ui.row_plan_export import export_row_plan
from devkit_ui.row_plan_format import RowPlanError, row_length_m

if TYPE_CHECKING:
    from devkit_ui.application_services.system_application_service import SystemApplicationService
    from devkit_ui.application_services.tool_state import ToolState
    from devkit_ui.view_models.map_context import MapContext

Level = Literal['ok', 'error', 'muted']


@dataclass(frozen=True)
class ToolStatusView:
    """Text for a tool's status label and how to colour it; a level of None keeps the current colour."""

    text: str
    level: Level | None = None


@dataclass(frozen=True)
class RowPlanDownload:
    """The result of an export: what to say, and the file to hand to the browser if there is one."""

    status: ToolStatusView
    filename: str | None = None
    content: bytes | None = None


class SystemViewModel:
    """UI-facing intents for the tools on the System page.

    start, stop, status and exited return None when the action was ignored or there is nothing
    to show, and the view then leaves its label alone.
    """

    def __init__(self, service: SystemApplicationService, map_context: MapContext | None = None) -> None:
        """Bind to the system service and, for the row plan export, to the loaded map."""
        self._service = service
        self._map_context = map_context

    def export_row_plan(self) -> RowPlanDownload:
        """Return the loaded map's rows as a LiteFarm row plan file, or the reason there is none.

        Pure computation on the loaded map, so it is quick enough to call on the UI loop.
        """
        doc = self._map_context.topo_doc if self._map_context else None
        if doc is None:
            return RowPlanDownload(ToolStatusView('no map loaded', 'error'))
        try:
            plan = export_row_plan(doc)
            content = plan.json_text().encode('utf-8')
        except RowPlanError as error:
            return RowPlanDownload(ToolStatusView(f'ERROR: {error}', 'error'))
        metres = sum(row_length_m(row) for row in plan.rows)
        text = f'{len(plan.rows)} rows, {metres:.1f} m, {plan.map_id}'
        if plan.warnings:
            text += f' - warning: {"; ".join(plan.warnings[:3])}'
        filename = f'{re.sub(r"[^A-Za-z0-9._-]", "_", plan.map_id)}.geojson'
        return RowPlanDownload(ToolStatusView(text, 'ok'), filename, content)

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
