"""Validate and stage topology edits (drop node, F2C row save, repair, delete).

This sits between the NiceGUI node and TopologyApplicationService. It decides what an
edit means for the loaded map and returns plain results, so the node only has to show
statuses, log lines and start the background write. No ROS or NiceGUI imports.
"""
from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any

from devkit_ui.application_services.topology_application_service import (
    TopologyApplicationService,
)
from devkit_ui.constants import NAV_ACTION
from devkit_ui.models import TopoDoc, TopoEdge, TopoNode, TopoProperties, Vector2
from devkit_ui.topo_results import StagedDrop, StagedEdit, TopologyEditError
from devkit_ui.topo_row_builder import (
    RowAnchor,
    RowBuildSettings,
    add_missing_edges,
    apply_row_nodes,
    build_row_nodes,
    plan_row_repair,
)

_NAME_RE = re.compile(r'^[A-Z0-9_]+$')
_NO_NODE = ('—', 'none', 'None', '', None)


def clean_name(raw: str | None) -> str:
    """Return raw stripped, uppercased, with spaces as underscores and other junk removed."""
    return re.sub(r'[^A-Z0-9_]', '', (raw or '').strip().upper().replace(' ', '_'))


def _timestamp() -> str:
    """Return the current UTC time in the format used by node metadata."""
    return datetime.now(UTC).strftime('%d-%m-%Y_%H-%M-%S')


class TopologyEditService:
    """Stages edits to the loaded topology map for the application service to persist."""

    def __init__(self, topology: TopologyApplicationService) -> None:
        """Keep the application service whose live document edits are checked against."""
        self._topology = topology

    def stage_drop(self, name: str, row_id: int | None, row_role: str, *,
                   pose: tuple[float, float] | None, is_sim: bool, gps: Any,
                   row_action: str, current_node: str | None,
                   selected_node: str | None) -> StagedDrop:
        """Add a node at pose to the live map and describe how to save it.

        pose is the robot's odometry position, or None when there is none; simulation
        then uses (0, 0). The node connects to current_node, or selected_node as a
        fallback, when that node exists. gps is the latest fix or None. Row nodes use
        row_action for their edge.

        Raises:
            TopologyEditError: The name is empty or invalid, no map is loaded, the name
                is taken, or hardware has no odometry. The map is left unchanged.
        """
        name = clean_name(name)
        if not name:
            raise TopologyEditError('ERROR: node name required')
        if not _NAME_RE.match(name):
            raise TopologyEditError(f'ERROR: invalid name "{name}"')
        doc = self._topology.get_doc()
        if not doc:
            raise TopologyEditError('ERROR: map not loaded')
        if doc.has_node(name):
            raise TopologyEditError(f'ERROR: {name} already exists')
        if pose is None and not is_sim:
            raise TopologyEditError('ERROR: no odometry')

        x = round(pose[0], 3) if pose else 0.0
        y = round(pose[1], 3) if pose else 0.0
        connect_to = self._connect_target(doc, current_node, selected_node)
        is_row = row_id is not None
        if is_row:
            edge_action, xy_tol, yaw_tol, vert_r = row_action, 0.1, 0.05, 0.5
        else:
            edge_action, xy_tol, yaw_tol, vert_r = NAV_ACTION, 0.3, 0.1, 1.0

        gps_meta: dict = {}
        if gps is not None and gps.status.status >= 0:
            gps_meta = {
                'gps_lat': round(gps.latitude, 7),
                'gps_lon': round(gps.longitude, 7),
                'gps_fix_type': int(gps.status.status),
                'gps_hdop': None,
            }
        row_meta = {'row_id': row_id, 'row_role': row_role} if is_row else {}

        new_node = TopoNode(
            name=name,
            nav_frame=doc.transformation.get('topo_frame_id') or 'map',
            x=x,
            y=y,
            meta={
                'map': doc.name,
                'node': name,
                'pointset': doc.name,
                'dropped_by': 'webui',
                'timestamp': _timestamp(),
                **gps_meta,
                **row_meta,
            },
            properties=TopoProperties(xy_goal_tolerance=xy_tol, yaw_goal_tolerance=yaw_tol),
            verts=[
                Vector2(x=-vert_r, y=-vert_r),
                Vector2(x=vert_r, y=-vert_r),
                Vector2(x=vert_r, y=vert_r),
                Vector2(x=-vert_r, y=vert_r),
            ],
            edges=[
                TopoEdge(action=edge_action, edge_id=f'{name}_{connect_to}', node=connect_to),
            ] if connect_to else [],
        )
        doc.add_node(new_node)

        conn_str = f' → {connect_to}' if connect_to else ''
        gps_str = (f' [{gps_meta["gps_lat"]:.5f},{gps_meta["gps_lon"]:.5f}]'
                   if gps_meta else '')
        row_str = f' row={row_id}/{row_role}' if is_row else ''
        return StagedDrop(
            node=new_node,
            pending=f'{name}{conn_str} at ({x}, {y}){row_str}{gps_str} — writing…',
            base=f'{name}{conn_str} at ({x},{y}){row_str}{gps_str}',
            short=f'{name}{conn_str}',
            log=f'Node dropped: {name} at ({x:.3f},{y:.3f}){conn_str}{row_str}{gps_str}',
        )

    def stage_f2c_save(self, prefix: str, row_id_start: int, overwrite: bool, *,
                       swaths: list, origin_ll: tuple[float, float] | None,
                       contour_used: bool, break_after: set | frozenset,
                       is_sim: bool, odom_xy: tuple[float, float] | None, gps: Any,
                       row_action: str, current_node: str | None,
                       selected_node: str | None) -> StagedEdit:
        """Turn planned F2C swaths into row nodes and describe how to save them.

        Nothing is added to the live map; the edit is applied when it is persisted.
        Hardware anchors rows to the GPS fix and odometry position (odom_xy). Simulation
        anchors them to origin_ll at (0, 0), because the sim GPS fix is a datum that is
        not where the field was drawn. A GPS status below zero only logs a warning.

        Raises:
            TopologyEditError: The prefix is empty, nothing is planned, odometry, GPS or
                the map is missing, the GPS coordinates are not usable, the simulation
                has no field origin, or every row name is already taken.
        """
        prefix = clean_name(prefix)
        if not prefix:
            raise TopologyEditError('ERROR: prefix required')
        if not swaths:
            raise TopologyEditError('ERROR: no rows planned — click Plan Rows first')
        if odom_xy is None and not is_sim:
            raise TopologyEditError('ERROR: no odometry')
        if gps is None:
            raise TopologyEditError('ERROR: no GPS fix yet (/gnss/fix or sim shim)')

        lat, lon, status = gps.latitude, gps.longitude, gps.status.status
        if not (math.isfinite(lat) and math.isfinite(lon)):
            raise TopologyEditError(
                f'ERROR: GPS lat/lon not finite ({lat}, {lon}) status={status}')
        if abs(lat) < 1e-9 and abs(lon) < 1e-9:
            raise TopologyEditError(
                f'ERROR: GPS lat/lon are 0,0 — no fix yet (status={status})')
        warnings = []
        if status < 0:
            warnings.append(
                f'save_f2c_rows: proceeding with status={status} '
                f'(lat={lat:.7f}, lon={lon:.7f})')

        doc = self._topology.get_doc()
        if not doc:
            raise TopologyEditError('ERROR: map not loaded', tuple(warnings))

        # On hardware the GPS fix is the survey origin. In sim it is the static datum,
        # which is generally not where the F2C field was drawn; anchoring to it would
        # offset every row by the field-to-datum distance. Anchoring to the field's own
        # reference corner makes the round trip cancel, so rows land at the odom origin.
        if is_sim and origin_ll is not None:
            anchor_lat, anchor_lon = origin_ll
        elif is_sim:
            raise TopologyEditError(
                'ERROR: no F2C field origin in memory (re-run "Plan Rows" first) — '
                'saving now would anchor rows to the sim GPS datum instead of the '
                'field, offsetting every node.', tuple(warnings))
        else:
            anchor_lat, anchor_lon = lat, lon
        if is_sim or odom_xy is None:
            # Hardware without odometry was rejected above, so None only occurs in sim.
            anchor_x, anchor_y = 0.0, 0.0
        else:
            anchor_x, anchor_y = odom_xy
        anchor = RowAnchor(lat=anchor_lat, lon=anchor_lon, x=anchor_x, y=anchor_y)

        connect_to = self._connect_target(doc, current_node, selected_node)
        nav_frame = doc.transformation.get('topo_frame_id') or 'map'
        infos = (f'F2C save: nav_frame={nav_frame!r}, topo_doc type={type(doc).__name__}',)
        build = build_row_nodes(swaths, anchor, RowBuildSettings(
            prefix=prefix,
            row_id_start=row_id_start,
            row_action=row_action,
            nav_frame=nav_frame,
            map_name=doc.name or 'mixed_test_map',
            timestamp=_timestamp(),
            fix_type=int(status),
            overwrite=overwrite,
            contour_used=contour_used,
            break_after=frozenset(break_after),
            connect_to=connect_to,
        ))
        if not build.added:
            raise TopologyEditError(
                'ERROR: nothing added (all names already taken)', tuple(warnings))

        skip_str = f' (skipped {len(build.skipped)} dup ids)' if build.skipped else ''
        splice_str = f' · spliced @ {connect_to}' if connect_to else ' · standalone'
        return StagedEdit(
            pending=f'writing {len(build.added)} rows · {prefix}{splice_str}{skip_str}…',
            success=f'saved {len(build.added)} rows · {prefix}{splice_str}{skip_str}',
            modify=lambda file_doc: apply_row_nodes(file_doc, build, prefix, overwrite),
            warnings=tuple(warnings),
            infos=infos,
        )

    def stage_repair(self, row_action: str, connect_to: str | None = None) -> StagedEdit:
        """Add the missing in-row and headland edges to the live map and describe the save.

        New in-row edges use row_action and existing edge actions are kept. connect_to
        only changes the success text; an unknown node is ignored with a warning.

        Raises:
            TopologyEditError: No map is loaded, there are no row nodes, or every edge
                is already present. The map is left unchanged.
            OverflowError: Row metadata contains an infinite row id.
        """
        doc = self._topology.get_doc()
        if not doc:
            raise TopologyEditError('ERROR: map not loaded')
        plan = plan_row_repair(doc, row_action)
        if plan is None:
            raise TopologyEditError('ERROR: no row nodes found')

        warnings = []
        if plan.headlands_skipped:
            warnings.append(
                'repair: row nodes lack x/y coords — cannot classify headland '
                'ends; skipping headland edges (in-row edges still restored)')
        if connect_to and not doc.has_node(connect_to):
            warnings.append(f'repair: connect_to={connect_to!r} not in map, ignoring')
            connect_to = None

        added_count = add_missing_edges(doc, plan.edges)
        if added_count == 0:
            raise TopologyEditError(
                f'repair: already wired ({plan.row_count} rows)', tuple(warnings))

        target_str = f' @ {connect_to}' if connect_to else ' — NO SPLICE, chain still isolated'
        return StagedEdit(
            pending=f'repair: adding {added_count} edges…',
            success=f'repair: wired {added_count} edges{target_str}',
            modify=lambda file_doc: add_missing_edges(file_doc, plan.edges),
            warnings=tuple(warnings),
        )

    def stage_delete_node(self, name: str) -> StagedEdit:
        """Describe deleting one node from the saved map.

        Raises:
            TopologyEditError: No map is loaded or name is not in it.
        """
        doc = self._require_loaded()
        if not name or not doc.has_node(name):
            raise TopologyEditError(f'ERROR: {name!r} not in map')
        return StagedEdit(
            pending=f'deleting {name}…',
            success=f'deleted {name}',
            modify=lambda file_doc: file_doc.remove_node(name),
            affected=frozenset({name}),
        )

    def stage_delete_row(self, row_id: int) -> StagedEdit:
        """Describe deleting every node of a row from the saved map.

        Raises:
            TopologyEditError: No map is loaded or the row has no nodes.
        """
        doc = self._require_loaded()
        targets = frozenset(node.name for node in doc.nodes
                            if node.meta.get('row_id') == row_id)
        if not targets:
            raise TopologyEditError(f'ERROR: no nodes for row {row_id}')
        label = f'row {row_id} ({len(targets)} nodes)'
        return StagedEdit(
            pending=f'deleting {label}…',
            success=f'deleted {label}',
            modify=lambda file_doc: file_doc.remove_nodes(set(targets)),
            affected=targets,
        )

    def _require_loaded(self) -> TopoDoc:
        """Return the live map, or raise TopologyEditError if none is loaded."""
        doc = self._topology.get_doc()
        if not doc:
            raise TopologyEditError('ERROR: map not loaded')
        return doc

    @staticmethod
    def _connect_target(doc: TopoDoc, current_node: str | None,
                        selected_node: str | None) -> str | None:
        """Return the existing node a new edge should point at, or None.

        The current node wins; the selected node is the fallback.
        """
        if current_node not in _NO_NODE and doc.has_node(current_node):
            return current_node
        if selected_node and doc.has_node(selected_node):
            return selected_node
        return None
