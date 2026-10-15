"""Use cases for the Plan tab: plan field rows, hold the planned swaths, save them to the map.

This sits between the Plan view model and the topology services. It runs the field planner,
remembers the latest swaths until they are saved, and turns them into topology rows through
TopologyEditService. No ROS or NiceGUI imports.
"""
from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from devkit_f2c_planner.f2c_planner import run_f2c
from devkit_ui import plan_import
from devkit_ui.plan_types import MIN_CORNERS, PlannedRows, PlanOutcome, PlanRequest
from devkit_ui.row_planning import plan_contour_rows
from devkit_ui.topo_results import StagedEdit, TopologyEditError

if TYPE_CHECKING:
    from devkit_ui.application_services.telemetry_application_service import (
        TelemetryApplicationService,
    )
    from devkit_ui.application_services.topology_application_service import (
        TopologyApplicationService,
    )
    from devkit_ui.application_services.topology_edit_service import TopologyEditService

_LOG = logging.getLogger(__name__)

DEFAULT_RECON_PATH = '/workspace/maps/recon_logs/recon.csv'


def _start_thread(work: Callable[[], None]) -> None:
    """Run work on a daemon thread."""
    threading.Thread(target=work, daemon=True).start()


class PlanApplicationService:
    """Plans field rows and saves them into the loaded topology map."""

    def __init__(self, topology: TopologyApplicationService, edits: TopologyEditService,
                 telemetry: TelemetryApplicationService, *, is_sim: bool,
                 background: Callable[[Callable[[], None]], None] = _start_thread) -> None:
        """Keep the services used to save rows and the pose and fix that anchor them.

        background starts the map write that follows a save or repair; tests pass a function
        that runs it inline.
        """
        self._topology = topology
        self._edits = edits
        self._telemetry = telemetry
        self._is_sim = is_sim
        self._background = background
        self._planned: PlannedRows | None = None
        self._save_status = ''

    @property
    def swaths(self) -> list:
        """Return the planned swaths as (lat, lon) lists in mission order; empty if none."""
        return self._planned.swaths if self._planned else []

    @property
    def save_status(self) -> str:
        """Return the last save or repair status, including validation and write results."""
        return self._save_status

    def set_status(self, text: str) -> None:
        """Replace the save status, for problems the caller found before any save or repair."""
        self._save_status = text

    def plan(self, request: PlanRequest) -> PlanOutcome:
        """Plan straight or contour rows for the boundary and keep them for saving.

        Blocks on terrain and planner work, so call it from a worker thread. Contour mode
        falls back to straight rows when no usable reference contour exists, using the
        requested angle. With fewer than three corners or a caught planner exception, return
        a failed outcome and keep the previous plan. A successful run replaces it,
        even when no rows survive.
        """
        if len(request.corners_ll) < MIN_CORNERS:
            return PlanOutcome(False, f'Need at least {MIN_CORNERS} corners')

        corners = list(request.corners_ll)
        rings = request.obstacle_rings
        break_after: set[int] = set()
        contour_used = False
        mode_note = ''
        try:
            if request.contour:
                swaths = plan_contour_rows(
                    corners, rings, request.tool_width, request.obstacle_pad_m,
                    request.headland_m, request.snake,
                    request.recon_path.strip() or DEFAULT_RECON_PATH, request.dem_res,
                    break_after=break_after)
                if swaths is None:
                    mode_note = ' · flat field, straight swaths used'
                    swaths = self._plan_straight(request)
                else:
                    mode_note = ' · contour rows'
                    contour_used = True
            else:
                swaths = self._plan_straight(request)
        except (FileNotFoundError, ValueError) as exc:
            stage = 'Contour planning' if request.contour else 'Planning'
            return PlanOutcome(False, f'{stage} failed: {exc}')
        except Exception as exc:
            _LOG.exception('plan failed')
            return PlanOutcome(False, f'ERROR: {exc}')

        # The origin is the corner run_f2c projected from. Saving re-anchors to it so the lat/lon
        # round trip cancels and rows land at the local odom origin, instead of being offset by
        # the distance between the field and whatever GPS fix is current (in sim, the datum fix).
        self._planned = PlannedRows(
            swaths, tuple(corners[0]), contour_used, frozenset(break_after))

        headland = f' · {request.headland_m}m headland' if request.headland_m > 0 else ''
        snake = ' · snake' if request.snake else ''
        avoided = f' · {len(rings)} obs avoided' if rings else ''
        angle = '' if contour_used else f' · {request.angle_deg:.0f}°'
        status = (f'{len(swaths)} rows · {request.tool_width}m wide'
                  f'{angle}{headland}{snake}{avoided}{mode_note}')
        return PlanOutcome(True, status, swaths)

    def import_plan(self, text: str) -> str:
        """Load a web-planner plan as the planned rows so they can be saved.

        Returns a status string; does not touch the topology map. Hardware requires finite
        GPS coordinates with at least one absolute value above 1e-9 degrees, and rejects
        a plan whose row-endpoint center is more than 2,000 meters from the robot.
        Simulation skips these GPS checks. PlanError becomes an 'ERROR:' status and leaves
        the previous plan intact; other parser errors propagate.
        """
        robot_ll = None
        gps = self._telemetry.latest_gps
        if gps is not None:
            lat, lon = gps.latitude, gps.longitude
            if math.isfinite(lat) and math.isfinite(lon) and (abs(lat) > 1e-9 or abs(lon) > 1e-9):
                robot_ll = (lat, lon)
        if robot_ll is None and not self._is_sim:
            return 'ERROR: no GPS fix; cannot check the plan is near the robot'
        try:
            plan = plan_import.parse_plan(text, robot_ll=None if self._is_sim else robot_ll)
        except plan_import.PlanError as exc:
            return f'ERROR: {exc}'
        self._planned = PlannedRows(
            plan.swaths, plan.origin_ll, bool(plan.params.get('contour')),
            frozenset(plan.break_after))
        split = (
            f' · {len(plan.break_after)} obstacle splits (not linked)' if plan.break_after else '')
        return f'imported {len(plan.swaths)} rows{split}; review, then Save as Topo Rows'

    def save_rows(self, prefix: str, row_id_start: int, overwrite: bool, *,
                  row_action: str, current_node: str | None, selected_node: str | None) -> None:
        """Save the planned swaths as rows in the loaded topology map.

        In-row edges use row_action and headland edges use point-to-point navigation. The
        new rows connect to current_node, or selected_node when that is not usable.
        Validation failures are reported in save_status and write nothing. Otherwise the
        map is written in the background and save_status is updated when that ends. See
        TopologyEditService.stage_f2c_save for the rules. Errors from the row builder
        propagate before anything is scheduled.

        Args:
            prefix: Prefix for the generated node names, normalized by the edit service.
            row_id_start: Row id given to the first planned swath.
            overwrite: Replace existing ``<prefix>_R*`` nodes instead of skipping taken names.
            row_action: Action for edges along a row.
            current_node: Node the robot is localised on, if any.
            selected_node: Node picked in the map view, if any.
        """
        planned = self._planned or PlannedRows([], None)
        odom = self._telemetry.latest_odom
        try:
            edit = self._edits.stage_f2c_save(
                prefix, row_id_start, overwrite,
                swaths=planned.swaths, origin_ll=planned.origin_ll,
                contour_used=planned.contour_used, break_after=planned.break_after,
                is_sim=self._is_sim,
                odom_xy=(odom.pose.pose.position.x, odom.pose.pose.position.y) if odom else None,
                gps=self._telemetry.latest_gps, row_action=row_action,
                current_node=current_node, selected_node=selected_node)
        except TopologyEditError as exc:
            self._log_edit_lines(exc.warnings, ())
            self._save_status = str(exc)
            return
        self._start_write(edit)

    def repair_connectivity(self, row_action: str, connect_to: str | None = None) -> None:
        """Rebuild missing in-row and headland edges for the rows already in the loaded map.

        New in-row edges use row_action and existing edge actions are kept. The live map
        changes immediately and the write runs in the background; nothing is scheduled when
        no edges are added. Validation failures are reported in save_status. connect_to only
        changes the success text; an unknown node is ignored with a warning.

        Raises:
            OverflowError: Row metadata contains an infinite row id; raised before any edge is added.
        """
        try:
            edit = self._edits.stage_repair(row_action, connect_to)
        except TopologyEditError as exc:
            self._log_edit_lines(exc.warnings, ())
            self._save_status = str(exc)
            return
        self._start_write(edit)

    def _plan_straight(self, request: PlanRequest) -> list:
        """Return straight swaths for the boundary at the requested angle."""
        return run_f2c(
            list(request.corners_ll), request.obstacle_rings, request.tool_width,
            request.angle_deg, request.obstacle_pad_m, request.headland_m, request.snake)

    def _start_write(self, edit: StagedEdit) -> None:
        """Log the staged edit, show its pending status and persist it in the background."""
        self._log_edit_lines(edit.warnings, edit.infos)
        self._save_status = edit.pending
        self._background(lambda: self._write(edit))

    def _write(self, edit: StagedEdit) -> None:
        """Persist the edit and report how it ended in save_status."""
        try:
            result = self._topology.persist_and_reload(edit.modify)
            self._save_status = result.describe(edit.success)
            _LOG.info('plan write: %s', edit.success)
        except Exception as exc:
            self._save_status = f'ERROR: {exc}'
            _LOG.exception('plan write failed')

    @staticmethod
    def _log_edit_lines(warnings: tuple[str, ...], infos: tuple[str, ...]) -> None:
        """Log the lines produced while staging an edit."""
        for line in infos:
            _LOG.info(line)
        for line in warnings:
            _LOG.warning(line)
