"""Run-tab map authoring: drop and track nodes, choose the row action, delete nodes and rows."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from devkit_ui.application_services.topology_edit_service import clean_name
from devkit_ui.constants import ROW_ACTION, VISION_ROW_ACTION
from devkit_ui.topo_results import StagedEdit, TopologyEditError

if TYPE_CHECKING:
    from devkit_ui.application_services.telemetry_application_service import (
        TelemetryApplicationService,
    )
    from devkit_ui.application_services.topology_application_service import (
        TopologyApplicationService,
    )
    from devkit_ui.application_services.topology_edit_service import TopologyEditService
    from devkit_ui.application_services.track_application_service import TrackApplicationService
    from devkit_ui.view_models.run_view_model import RunViewModel
    from devkit_ui.view_models.topology_view_model import TopologyViewModel

_LOG = logging.getLogger(__name__)


class MapAuthoringViewModel:
    """Edits the live map from the Run tab and reports progress in RunViewModel's state.

    The row action is RunViewModel.drop_node.row_action, so the Plan tab reads the same
    value. It is per session, never persisted, and returns to geometry-only rows when the
    map is archived.
    """

    def __init__(self, run_vm: RunViewModel, topo_vm: TopologyViewModel,
                 topo_app_service: TopologyApplicationService,
                 edit_service: TopologyEditService,
                 telemetry_app_service: TelemetryApplicationService,
                 track_service: TrackApplicationService, *, is_sim: bool) -> None:
        """Bind to the Run state and services; track_service records nodes in track mode."""
        self._run_vm = run_vm
        self._topo_vm = topo_vm
        self._topo = topo_app_service
        self._edits = edit_service
        self._telemetry = telemetry_app_service
        self._track = track_service
        self._is_sim = is_sim
        topo_vm.register_map_archived_callback(self._on_map_archived)

    # ── row action ────────────────────────────────────────────────────────────

    @property
    def row_action(self) -> str:
        """The action new row edges use, and the one the loaded map's row edges are set to."""
        return self._run_vm.drop_node.row_action

    def set_row_action(self, action: str) -> None:
        """Switch row driving between geometry-only and vision following.

        Applies to rows added from now on and rewrites the row edges already in the map, so
        the same map can be seeded with row_traversal and then run with limbic_row_follow
        once a crop exists.

        Ignore unsupported or unchanged actions. Update the session selection immediately;
        if the loaded map has matching edges to change, persist and reload in the background.
        Worker errors are reported in drop_node.status without reverting the selection.
        """
        if action not in (ROW_ACTION, VISION_ROW_ACTION) or action == self.row_action:
            return
        self._run_vm.drop_node.row_action = action
        doc = self._topo_vm.topo_doc
        if not doc:
            return
        n_edges = sum(1 for n in doc.nodes for e in n.edges
                      if e.action in (ROW_ACTION, VISION_ROW_ACTION) and e.action != action)
        if n_edges == 0:
            self._run_vm.drop_node.status = f'row action: {action} (no edges to change)'
            return
        self._topo.persist_and_reload_async(
            lambda file_doc: file_doc.set_row_action({ROW_ACTION, VISION_ROW_ACTION}, action),
            f'row action: {action}, {n_edges} row edges updated',
            self._set_drop_status)

    def _on_map_archived(self) -> None:
        """Return row driving to geometry mode after the map was archived and cleared."""
        self._run_vm.drop_node.row_action = ROW_ACTION

    # ── node dropping ─────────────────────────────────────────────────────────

    def drop_node(self, name: str, row_id: int | None, row_role: str = 'entry') -> None:
        """Add a topology node at the current robot position and persist it to the active map.

        Add the node in memory immediately, then save and reload in the background. Use
        (0, 0) in simulation if odometry is unavailable. Connect to the current node, or the
        selected node as a fallback; save the outgoing edge only if its target is already on
        disk. Row nodes use the selected row action. Validation failures and worker
        exceptions set drop_node.status; a failed save does not roll back the in-memory
        addition. The rules live in TopologyEditService.

        Parameters:
            name: Name, cleaned to A-Z, 0-9 and underscore.
            row_id: Row identifier to associate with the node, or None for a navigation node.
            row_role: Role of the node within its row, such as "entry" or "exit".
        """
        odom = self._telemetry.latest_odom
        try:
            drop = self._edits.stage_drop(
                name, row_id, row_role,
                pose=(odom.pose.pose.position.x, odom.pose.pose.position.y) if odom else None,
                is_sim=self._is_sim, gps=self._telemetry.latest_gps, row_action=self.row_action,
                current_node=self._topo_vm.current_node,
                selected_node=self._topo_vm.selected_node)
        except TopologyEditError as e:
            self._set_drop_status(str(e))
            return
        self._set_drop_status(drop.pending)
        self._topo.drop_node_async(drop, self._set_drop_status)

    # ── track mode ────────────────────────────────────────────────────────────

    def start_track(self, prefix: str, interval: float,
                    row_id: int | None, row_role: str | None) -> None:
        """Record a topology node immediately, then periodically on the timer.

        Parameters:
            prefix: Prefix used for numbered node names after cleaning.
            interval: Time in seconds between recorded nodes.
            row_id: Optional row identifier; its first node is an entry, and subsequent
                nodes are middle nodes.
            row_role: Role assigned when no row identifier is provided.

        Continue numbering after existing nodes with the same prefix. An empty prefix, a
        running track or a missing map is reported in the track status and nothing starts.
        """
        track = self._run_vm.track
        prefix = clean_name(prefix)
        if not prefix:
            track.running = False
            track.status = 'ERROR: prefix required'
            return
        if self._track.running:
            track.running = True
            track.status = 'ERROR: already running'
            return
        doc = self._topo_vm.topo_doc
        if doc is None:
            track.status = 'ERROR: map not loaded'
            return

        track.prefix = prefix
        track.interval = interval
        track.row_id = row_id
        track.row_role = row_role or 'entry'
        track.running = True
        track.status = ''

        def recorded(name: str, number: int) -> None:
            """Show the node that was just recorded."""
            track.status = f'recording  {name}  (#{number})'

        self._track.start(prefix, interval, row_id, track.row_role,
                          [n.name for n in doc.nodes], self.drop_node, recorded)

    def stop_track(self) -> None:
        """Stop tracking and mark the last tracked node as the row exit when applicable."""
        count = self._track.stop()
        track = self._run_vm.track
        if track.row_id is not None and count > 0:
            last_name = f'{track.prefix}_{count}'
            self._topo.patch_node_role_async(last_name, 'exit')
            track.status = f'stopped — {last_name} marked exit  (#{count} nodes)'
        else:
            track.status = f'stopped at #{count}'
        track.running = False
        track.prefix = ''
        track.row_id = None
        track.row_role = 'entry'

    # ── deleting nodes and rows ───────────────────────────────────────────────

    def delete_node(self, name: str) -> None:
        """Delete a topology node and persist the updated map."""
        self._persist_deletion(self._edits.stage_delete_node, name)

    def delete_row(self, row_id: int) -> None:
        """Delete every topology node of a row and persist the updated map."""
        self._persist_deletion(self._edits.stage_delete_row, row_id)

    def _persist_deletion(self, stage: Callable[[Any], StagedEdit], target: Any) -> None:
        """Stage a deletion, clear a selection it removes, and persist it in the background."""
        topo = self._run_vm.topo
        try:
            edit = stage(target)
        except TopologyEditError as e:
            topo.delete_status = str(e)
            return
        if self._topo_vm.selected_node in edit.affected:
            self._topo_vm.set_selected_node(None)
        topo.delete_status = edit.pending
        self._topo.persist_and_reload_async(edit.modify, edit.success, self._set_delete_status)

    # ── status ────────────────────────────────────────────────────────────────

    def _set_drop_status(self, status: str) -> None:
        """Show status on the Drop Node card."""
        self._run_vm.drop_node.status = status

    def _set_delete_status(self, status: str) -> None:
        """Show status beside the node list."""
        self._run_vm.topo.delete_status = status
