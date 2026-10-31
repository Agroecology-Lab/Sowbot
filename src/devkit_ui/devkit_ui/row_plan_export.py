"""Turn the row nodes of a topological map into a row plan for LiteFarm. No ROS imports.

This is the reverse of ``topo_row_builder``: that module makes entry, waypoint and exit nodes from planned
swaths, this one reads them back. The row number in LiteFarm is the ``row_id`` of the nodes, so a result
reported for LiteFarm row 12 is the robot's row 12. The format is described in docs/row-plan-geojson.md.
"""
import hashlib
import json
import re
from dataclasses import dataclass, field
from itertools import pairwise

from devkit_ui.constants import NAV_ACTION
from devkit_ui.models import NodeID, TopoDoc, TopoNode
from devkit_ui.row_plan_format import (
    PlanProperties,
    PlanRow,
    RowPlanError,
    build_geojson,
    map_hash,
    row_length_m,
)

# The roles topo_row_builder writes. Not imported from there: that module needs the Fields2Cover planner.
ROW_ROLES = ('entry', 'exit', 'waypoint')

# A row's map-frame length and its GPS length may differ by this much before a warning is given.
LENGTH_TOLERANCE_M = 0.5
LENGTH_TOLERANCE_RATIO = 0.05


@dataclass
class RowPlanExport:
    """A row plan as GeoJSON, with the facts a caller will want to report."""

    geojson: dict
    rows: list[PlanRow]
    ignored_nodes: int
    warnings: list[str] = field(default_factory=list)

    @property
    def map_id(self) -> str:
        return self.geojson['properties']['map_id']

    @property
    def map_hash(self) -> str:
        return self.geojson['properties']['map_hash']


def export_row_plan(doc: TopoDoc, *, map_id: str | None = None, supersedes: str | None = None,
                    field_location_id: str | None = None) -> RowPlanExport:
    """Return the row plan in ``doc``, in mission order (ascending ``row_id``).

    Without ``map_id`` the id is ``<map name>-<first 12 characters of the hash>``. The same rows always
    give the same id, so importing twice is harmless, and changed rows get a new id that can supersede
    the old one. Raise RowPlanError if a row is incomplete, has no GPS position, or LiteFarm would refuse it.
    """
    groups, ignored = _group_row_nodes(doc)
    if not groups:
        raise RowPlanError(f'map {doc.name!r} has no row nodes (nodes with a row_id and an entry or exit role)')
    ordered = {row_id: _ordered_row(row_id, nodes) for row_id, nodes in sorted(groups.items())}
    rows = [PlanRow(row_id, [(n.properties.gps_lat, n.properties.gps_lon) for n in nodes])
            for row_id, nodes in ordered.items()]
    plan_hash = map_hash(rows)
    props = PlanProperties(
        map_id=map_id or f'{_safe_name(doc.name)}-{plan_hash[:12]}',
        origin=rows[0].points[0],
        supersedes=supersedes,
        field_location_id=field_location_id,
        inputs_hash=_inputs_hash(groups),
    )
    geojson = build_geojson(rows, props)
    return RowPlanExport(geojson, rows, ignored, _length_warnings(ordered, rows))


def _group_row_nodes(doc: TopoDoc) -> tuple[dict[int, list[TopoNode]], int]:
    """Return the row nodes by row id, and how many nodes are not part of a row."""
    groups: dict[int, list[TopoNode]] = {}
    ignored = 0
    for node in doc.nodes:
        props = node.properties
        row_id = _row_id(props.row_id)
        if row_id is None or props.row_role not in ROW_ROLES:
            ignored += 1
            continue
        groups.setdefault(row_id, []).append(node)
    return groups, ignored


def _row_id(value: object) -> int | None:
    """Return a row id as a non-negative int, or None if the node has none."""
    if isinstance(value, bool):
        return None
    try:
        row_id = int(str(value).strip())
    except ValueError:
        return None
    return row_id if row_id >= 0 else None


def _ordered_row(row_id: int, nodes: list[TopoNode]) -> list[TopoNode]:
    """Return a row's nodes in driving order, from entry to exit. Raise RowPlanError if it is incomplete."""
    entries = [n for n in nodes if n.properties.row_role == 'entry']
    exits = [n for n in nodes if n.properties.row_role == 'exit']
    if len(entries) != 1 or len(exits) != 1:
        raise RowPlanError(
            f'row {row_id} needs exactly one entry and one exit node, found {len(entries)} and {len(exits)}')
    ordered = _driving_order(entries[0], exits[0], [n for n in nodes if n.properties.row_role == 'waypoint'])
    missing = [n.name for n in ordered if not _has_position(n)]
    if missing:
        raise RowPlanError(f'row {row_id}: no GPS position on {", ".join(missing)}')
    if len({(n.properties.gps_lat, n.properties.gps_lon) for n in ordered}) < 2:
        raise RowPlanError(f'row {row_id}: the entry and the exit are in the same place, so it has no direction')
    return ordered


def _has_position(node: TopoNode) -> bool:
    """Return whether the node has a GPS position. 0, 0 is what an unset position looks like."""
    return (node.properties.gps_lat, node.properties.gps_lon) != (0.0, 0.0)


def _driving_order(entry: TopoNode, exit_node: TopoNode, waypoints: list[TopoNode]) -> list[TopoNode]:
    """Return entry, waypoints and exit in the order the robot drives them.

    The order comes from the row edges (the ones that are not headland navigation edges). If the edges do
    not reach every waypoint, fall back to the number in the waypoint name, then to the name.
    """
    by_name = {n.name: n for n in waypoints}
    chain: list[TopoNode] = []
    current = entry
    while True:
        following = [e.node for e in current.edges if e.action != NAV_ACTION and e.node in by_name
                     and by_name[e.node] not in chain]
        if not following:
            break
        current = by_name[following[0]]
        chain.append(current)
    if len(chain) != len(waypoints):
        chain = sorted(waypoints, key=lambda n: (_waypoint_number(n.name), n.name))
    return [entry, *chain, exit_node]


def _waypoint_number(name: NodeID) -> int:
    match = re.search(r'_W(\d+)$', name)
    return int(match.group(1)) if match else 0


def _safe_name(name: str) -> str:
    """Return ``name`` with anything LiteFarm does not allow in a map_id replaced by an underscore."""
    return re.sub(r'[^A-Za-z0-9._:-]', '_', name)[:80] or 'map'


def _inputs_hash(groups: dict[int, list[TopoNode]]) -> str:
    """Hash what the plan was made from: each row node's name, row, role, map position and GPS position."""
    rows = [
        [n.name, row_id, n.properties.row_role, round(n.x, 3), round(n.y, 3), n.properties.gps_lat,
         n.properties.gps_lon]
        for row_id, nodes in sorted(groups.items())
        for n in sorted(nodes, key=lambda n: n.name)
    ]
    return hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode('utf-8')).hexdigest()


def _length_warnings(ordered: dict[int, list[TopoNode]], rows: list[PlanRow]) -> list[str]:
    """Warn about rows whose length on the map and from GPS disagree: the GPS positions or the map are off."""
    warnings = []
    for plan_row in rows:
        nodes = ordered[plan_row.row]
        map_length = sum(((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5 for a, b in pairwise(nodes))
        gps_length = row_length_m(plan_row)
        if abs(map_length - gps_length) > max(LENGTH_TOLERANCE_M, LENGTH_TOLERANCE_RATIO * map_length):
            warnings.append(f'row {plan_row.row}: {map_length:.1f} m on the map but {gps_length:.1f} m from GPS')
    return warnings
