from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from devkit_ui.application_services.plan_application_service import PlanApplicationService
    from devkit_ui.plan_types import PlanOutcome, PlanRequest
    from devkit_ui.view_models.run_view_model import RunViewModel
    from devkit_ui.view_models.topology_view_model import TopologyViewModel


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
    def swaths(self) -> list:
        """Return the planned swaths as (lat, lon) lists in mission order; empty if none."""
        return self._app_service.swaths

    @property
    def save_status(self) -> str:
        """Return the last save or repair status; an error starts with 'ERROR'."""
        return self._app_service.save_status

    def plan(self, request: PlanRequest) -> PlanOutcome:
        """Plan rows for the boundary and keep them for saving.

        Blocks on terrain and planner work: call it from a worker thread.
        See PlanApplicationService.plan for the fallback and failure behavior.
        """
        return self._app_service.plan(request)

    def import_plan(self, text: str) -> str:
        """Load a web-planner plan as the planned rows and return a status string."""
        return self._app_service.import_plan(text)

    def save_rows(self, prefix: str, row_id_start: int, overwrite: bool) -> None:
        """Save the planned rows into the loaded map, connected to the current or selected node.

        Progress and errors appear in save_status.
        See PlanApplicationService.save_rows for validation and background-write behavior.
        """
        self._app_service.save_rows(
            prefix, row_id_start, overwrite, row_action=self._row_action,
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
