from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from devkit_ui.plan_types import MIN_CORNERS, PlanFailure, PlanMode, PlanRequest

if TYPE_CHECKING:
    from devkit_ui.application_services.plan_application_service import PlanApplicationService
    from devkit_ui.plan_types import PlannedRows, PlanOutcome
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


@dataclass(frozen=True)
class RepairOffer:
    """What the repair dialog needs: how many rows would be wired and a suggested base node."""
    row_count: int
    default_base: str


@dataclass(frozen=True)
class PlanRun:
    """A planning run that has started: the request to plan and the generation it belongs to."""
    request: PlanRequest
    generation: int


STATUS_INFO = 'info'
STATUS_OK = 'ok'
STATUS_ERROR = 'error'


class PlanPageState:
    """State of one browser session's Plan tab: boundary, rows to save and the status line.

    Each page opens its own, so two browsers never share a boundary or a draft. Cards read
    the attributes and call subscribe to be told when any of them changed. Nothing here
    imports the UI.

    A planning run is stamped with a generation when it starts. Clearing or successfully
    importing a plan starts a new one, so a run that finishes later is discarded.
    """

    def __init__(self) -> None:
        """Start with no boundary, no draft and planning allowed."""
        self.corners: list[tuple[float, float]] = []
        self.draft: PlannedRows | None = None
        self.status = ''
        self.status_kind = STATUS_INFO
        self.can_plan = True
        self.can_save = False
        self._generation = 0
        self._listeners: list[Callable[[], None]] = []

    @property
    def swaths(self) -> list:
        """Return the draft's swaths as (lat, lon) lists in mission order; empty if no draft."""
        return self.draft.swaths if self.draft else []

    def subscribe(self, listener: Callable[[], None]) -> None:
        """Call listener, with no arguments, after every change."""
        self._listeners.append(listener)

    def _notify(self) -> None:
        """Tell every subscriber that something changed."""
        for listener in list(self._listeners):
            listener()

    def _set_status(self, text: str, kind: str) -> None:
        """Replace the status line and how it should be shown."""
        self.status = text
        self.status_kind = kind

    def add_corner(self, lat: float, lon: float) -> None:
        """Append a boundary corner."""
        self.corners.append((lat, lon))
        self._notify()

    def clear(self) -> None:
        """Drop the boundary, the draft and the status, and discard any run still in flight."""
        self._generation += 1
        self.corners.clear()
        self.draft = None
        self._set_status('', STATUS_INFO)
        self.can_save = False
        self.can_plan = True
        self._notify()

    def begin_plan(self, obstacle_rings: list, **form) -> PlanRun | None:
        """Start a planning run for the boundary; return it, or None if it cannot start.

        form holds the raw Plan form values, as PlanRequest.from_form takes them. With too few
        corners, nothing starts and the status says so. Invalid form values raise as
        PlanRequest.from_form does, before anything changes.
        """
        if len(self.corners) < MIN_CORNERS:
            self._set_status(f'Need at least {MIN_CORNERS} corners', STATUS_ERROR)
            self._notify()
            return None
        request = PlanRequest.from_form(self.corners, obstacle_rings, **form)
        self._generation += 1
        self.can_plan = False
        self._set_status('Running F2C…', STATUS_INFO)
        self._notify()
        return PlanRun(request, self._generation)

    def finish_plan(self, run: PlanRun, view: PlanView) -> bool:
        """Apply a run's result; return False if Clear, an import or a newer run made it stale.

        A failed run keeps the earlier draft. A successful one replaces it.
        """
        if run.generation != self._generation:
            return False
        self.can_plan = True
        if view.ok:
            self.draft = view.planned
            self.can_save = bool(view.swaths)
            self._set_status(view.status, STATUS_OK)
        else:
            self._set_status(view.status, STATUS_ERROR)
        self._notify()
        return True

    def apply_import(self, view: PlanView) -> None:
        """Take an imported plan as the draft, or show why it could not be read.

        A successful import discards any planning run still in flight and allows planning.
        A failed import keeps the earlier draft, and Save is enabled only after a good one.
        """
        if view.ok:
            self._generation += 1
            self.can_plan = True
            self.draft = view.planned
        self.can_save = view.ok
        self._set_status(view.status, STATUS_OK if view.ok else STATUS_ERROR)
        self._notify()


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

    def open_page(self) -> PlanPageState:
        """Return fresh state for one browser session's Plan tab."""
        return PlanPageState()

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

    def repair_offer(self) -> RepairOffer | None:
        """Return what the repair dialog shows, or None if no map is loaded.

        The suggested base node is the robot's current node, else the selected one, as long
        as it exists in the map; empty if neither does.
        """
        doc = self._topo_vm.topo_doc
        if doc is None:
            return None
        current = self._topo_vm.current_node
        selected = self._topo_vm.selected_node
        default_base = ''
        if current not in ('—', 'none', 'None', '', None) and doc.has_node(current):
            default_base = current
        elif selected and doc.has_node(selected):
            default_base = selected
        row_count = sum(1 for node in doc.nodes
                        if node.meta.get('row_id') is not None
                        and node.meta.get('row_role') == 'entry')
        return RepairOffer(row_count, default_base)

    def topo_snapshot(self) -> frozenset | None:
        """Return each node's name and row metadata, for telling whether the map changed.

        None if no map is loaded. Equal snapshots mean available_rows would be equal too,
        including after a node's row metadata is edited in place.
        """
        doc = self._topo_vm.topo_doc
        if doc is None:
            return None
        return frozenset((node.name, node.meta.get('row_id'), node.meta.get('row_role'))
                         for node in doc.nodes)

    def available_rows(self) -> dict[int, str]:
        """Return row id -> entry node name for every row in the loaded map; empty if none."""
        doc = self._topo_vm.topo_doc
        rows: dict[int, str] = {}
        for node in (doc.nodes if doc is not None else ()):
            row_id = node.meta.get('row_id')
            if row_id is not None and node.meta.get('row_role', '') == 'entry':
                try:
                    rows[int(row_id)] = node.name
                except (TypeError, ValueError):
                    pass
        return rows

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
