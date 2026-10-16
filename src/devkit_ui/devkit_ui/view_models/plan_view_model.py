from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from devkit_ui.plan_types import MIN_CORNERS, PlanFailure, PlanMode

if TYPE_CHECKING:
    from devkit_ui.application_services.plan_application_service import PlanApplicationService
    from devkit_ui.plan_types import PlannedRows, PlanOutcome, PlanRequest
    from devkit_ui.view_models.run_view_model import RunViewModel
    from devkit_ui.view_models.topology_view_model import TopologyViewModel


@dataclass(frozen=True)
class PlanView:
    """What the Plan tab shows after planning or importing, and the rows to keep for saving.

    status is the line to display. planned is None unless ok; the page holds on to it and
    hands it back to save_rows.
    """
    ok: bool
    status: str
    planned: PlannedRows | None = None

    @property
    def swaths(self) -> list:
        """Return the planned swaths as (lat, lon) lists in mission order; empty if none."""
        return self.planned.swaths if self.planned else []


class PlanViewModel:
    """UI-facing state and user intents for the Plan tab: row planning, saving and repair."""

    def __init__(self, app_service: PlanApplicationService, topo_vm: TopologyViewModel,
                 run_vm: RunViewModel) -> None:
        """Bind to the plan service and the view models that hold the map and row action.

        The row action is the Run tab's drop_node.row_action; this view model only reads it.
        """
        self._app_service = app_service
        self._topo_vm = topo_vm
        self._run_vm = run_vm

    @property
    def _row_action(self) -> str:
        """Return the row action selected on the Run tab."""
        return self._run_vm.drop_node.row_action

    @property
    def save_status(self) -> str:
        """Return the last save or repair status, including validation and write results."""
        return self._app_service.save_status

    def plan(self, request: PlanRequest) -> PlanView:
        """Plan rows for the boundary and describe the result.

        Blocks on terrain and planner work: call it from a worker thread. The page keeps
        the returned rows and passes them to save_rows. See PlanApplicationService.plan for
        the fallback and failure behavior.
        """
        outcome = self._app_service.plan(request)
        return PlanView(outcome.ok, self._describe_plan(request, outcome), outcome.planned)

    def import_plan(self, text: str) -> PlanView:
        """Read a web-planner plan and describe the result; an error starts with 'ERROR'."""
        outcome = self._app_service.import_plan(text)
        if not outcome.ok:
            return PlanView(False, f'ERROR: {outcome.error}')
        planned = outcome.planned
        splits = (f' · {len(planned.break_after)} obstacle splits (not linked)'
                  if planned.break_after else '')
        return PlanView(
            True, f'imported {len(planned.swaths)} rows{splits}; review, then Save as Topo Rows',
            planned)

    def save_rows(self, planned: PlannedRows | None, prefix: str, row_id_start: int,
                  overwrite: bool) -> None:
        """Save the given planned rows into the loaded map, joined to the current or selected node.

        Progress and errors appear in save_status.
        See PlanApplicationService.save_rows for validation and background-write behavior.
        """
        self._app_service.save_rows(
            planned, prefix, row_id_start, overwrite, row_action=self._row_action,
            current_node=self._topo_vm.current_node, selected_node=self._topo_vm.selected_node)

    def repair(self, connect_to: str | None = None) -> None:
        """Rebuild missing row edges, optionally naming the node the chain is meant to join.

        An unknown connect_to node is rejected in save_status and nothing is repaired.
        See PlanApplicationService.repair_connectivity for the rest.
        """
        doc = self._topo_vm.topo_doc
        if connect_to and doc is not None and not doc.has_node(connect_to):
            self._app_service.set_status(f'ERROR: base node {connect_to!r} not in map')
            return
        self._app_service.repair_connectivity(self._row_action, connect_to)

    @staticmethod
    def _describe_plan(request: PlanRequest, outcome: PlanOutcome) -> str:
        """Return the status line for a planning run."""
        if outcome.failure is PlanFailure.TOO_FEW_CORNERS:
            return f'Need at least {MIN_CORNERS} corners'
        if outcome.failure is PlanFailure.PLANNING:
            stage = 'Contour planning' if request.contour else 'Planning'
            return f'{stage} failed: {outcome.detail}'
        if outcome.failure is not None:
            return f'ERROR: {outcome.detail}'
        rows = len(outcome.planned.swaths)
        angle = '' if outcome.mode is PlanMode.CONTOUR else f' · {request.angle_deg:.0f}°'
        headland = f' · {request.headland_m}m headland' if request.headland_m > 0 else ''
        snake = ' · snake' if request.snake else ''
        rings = len(request.obstacle_rings)
        avoided = f' · {rings} obs avoided' if rings else ''
        note = {PlanMode.CONTOUR: ' · contour rows',
                PlanMode.FLAT_FALLBACK: ' · flat field, straight swaths used'}.get(outcome.mode, '')
        return f'{rows} rows · {request.tool_width}m wide{angle}{headland}{snake}{avoided}{note}'
