"""Build and repair topological-map rows from planned Fields2Cover swaths.

Everything here works on in-memory ``TopoDoc`` objects and plain values. There is
no ROS, NiceGUI, GPS or persistence dependency: the caller validates its inputs,
chooses the anchor, and persists the result. That keeps the geometry and graph
rules testable on their own and out of the UI node.
"""

import re
from dataclasses import dataclass, field
from itertools import pairwise

from devkit_f2c_planner.f2c_planner import _f2c_latlon_to_xy, _f2c_xy_to_latlon
from devkit_ui.constants import NAV_ACTION
from devkit_ui.models import (
    NodeID,
    TopoDoc,
    TopoEdge,
    TopoNode,
    TopoPose,
    TopoProperties,
    Vector2,
)
from devkit_ui.row_planning import (
    CONTOUR_WAYPOINT_INTERVAL_M,
    headland_neighbour_pairs,
    resample_row_xy,
)

ROW_ROLES = ('entry', 'exit', 'waypoint')
_WAYPOINT_SUFFIX = re.compile(r'_W(\d+)$')
_NODE_VERTS = (Vector2(x=-0.5, y=-0.5), Vector2(x=0.5, y=-0.5),
               Vector2(x=0.5, y=0.5), Vector2(x=-0.5, y=0.5))


@dataclass(frozen=True)
class RowAnchor:
    """Where the planned rows sit in the topological map.

    ``lat``/``lon`` are the origin of the lat/lon to local-xy projection. ``x``/``y``
    are that origin's position in the map frame, added to every projected point.
    """

    lat: float
    lon: float
    x: float = 0.0
    y: float = 0.0


@dataclass(frozen=True)
class RowBuildSettings:
    """Naming, metadata and edge settings for one batch of generated rows."""

    prefix: str
    row_id_start: int
    row_action: str
    nav_frame: str
    map_name: str
    timestamp: str
    fix_type: int
    overwrite: bool = False
    contour_used: bool = False
    break_after: frozenset[int] = frozenset()
    connect_to: str | None = None
    waypoint_interval_m: float = CONTOUR_WAYPOINT_INTERVAL_M


@dataclass
class RowBuild:
    """The nodes generated for a batch of rows, and which rows were used or skipped."""

    nodes: dict[NodeID, TopoNode] = field(default_factory=dict)
    added: list[int] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    row_names: dict[int, tuple[NodeID, NodeID]] = field(default_factory=dict)


def _make_node(name: str, x: float, y: float, role: str, lat: float, lon: float,
               row_id: int, settings: RowBuildSettings) -> TopoNode:
    """Create one row node carrying its row identity in both properties and metadata."""
    node = TopoNode(
        name=name,
        nav_frame=settings.nav_frame,
        edges=[],
        pose=TopoPose(x=x, y=y),
        properties=TopoProperties(
            xy_goal_tolerance=0.1,
            yaw_goal_tolerance=0.05,
            dropped_by='webui_f2c',
            timestamp=settings.timestamp,
            gps_lat=round(lat, 7),
            gps_lon=round(lon, 7),
            gps_fix_type=settings.fix_type,
            gps_hdop=None,
            row_id=row_id,
            row_role=role,
        ),
        verts=list(_NODE_VERTS),
        # row_id and row_role must also live in .meta: topo_renderer.build_svg()
        # reads nd.meta.get('row_role') for the in-circle label, and without it
        # every row node is drawn as '?'.
        meta={'map': settings.map_name, 'node': name, 'row_id': row_id, 'row_role': role},
    )
    node.add_metadata(dropped_by='webui_f2c', timestamp=settings.timestamp,
                      gps_fix_type=settings.fix_type, gps_hdop=None, row_id=row_id)
    return node


def _add_headland_edge(nodes: dict[NodeID, TopoNode], p: NodeID, q: NodeID) -> None:
    """Link p and q with a bidirectional nav_to_pose edge.

    Replace any existing edges between them. Raise KeyError if either name is
    absent from ``nodes``; an edge may already have been added before the error.
    """
    edge_id = f'{p}_{q}'
    for src, dst in ((p, q), (q, p)):
        nodes[src].add_edge(TopoEdge(action=NAV_ACTION, edge_id=edge_id, node=dst))
    for name, other in ((p, q), (q, p)):
        nodes[name].add_edge(other, action=NAV_ACTION)


def build_row_nodes(swaths: list, anchor: RowAnchor, settings: RowBuildSettings) -> RowBuild:
    """Turn planned swaths into entry, waypoint and exit nodes with their edges.

    Each usable swath becomes an IN and an OUT node joined by directed edges using
    ``settings.row_action``. With contour mode and more than two points, rows may
    also get intermediate waypoints; otherwise they keep a single entry-to-exit edge.

    Consecutive built rows are joined OUT to the next row's IN in both directions. The
    swaths arrive in snake order, so that pairing is known by construction and no
    coordinate guessing is needed. Guessing from coordinates alone
    (``headland_neighbour_pairs``) splits the field on the wrong axis once the
    cross-row width approaches the row length, leaving two disconnected halves.
    Swaths whose zero-based indices are in ``settings.break_after`` are not linked
    to the next built row.

    If ``settings.connect_to`` is set, the first IN and last OUT get outgoing
    navigation edges to it, without checking that the target exists. Swaths with
    fewer than two points are omitted without renumbering later rows or appearing
    in ``RowBuild.skipped``. Name collisions are checked only within this batch;
    existing map nodes are handled by ``apply_row_nodes``.

    Args:
        swaths: Sequences of (latitude, longitude) points in degrees, in snake order.
        anchor: Projection origin in degrees and its map position in meters.
            Generated map coordinates are rounded to three decimal places.
        settings: Naming, metadata and edge options. Row IDs are ``row_id_start``
            plus the swath index; ``prefix`` is used verbatim. Contour waypoint
            spacing is ``waypoint_interval_m`` meters along the swath.

    Returns:
        Generated nodes, added row IDs, endpoint names and row IDs skipped for
        endpoint-name collisions when overwrite is disabled. Empty if no swath
        has at least two points. No map is mutated or persisted.

    Raises:
        TypeError: A consumed coordinate is nonnumeric or a point is not iterable.
        ValueError: A consumed point does not unpack into two coordinates, or
            projection or resampling encounters invalid numeric input.
        ZeroDivisionError: Contour resampling uses a zero waypoint interval.
        OverflowError: A resampling count cannot be converted to an integer.
    """
    build = RowBuild()
    prefix, row_action = settings.prefix, settings.row_action

    for i, swath in enumerate(swaths):
        if len(swath) < 2:
            continue
        rid = settings.row_id_start + i
        in_lat, in_lon = swath[0]
        out_lat, out_lon = swath[-1]

        in_east, in_north = _f2c_latlon_to_xy(in_lat, in_lon, anchor.lat, anchor.lon)
        out_east, out_north = _f2c_latlon_to_xy(out_lat, out_lon, anchor.lat, anchor.lon)
        in_x, in_y = round(anchor.x + in_east, 3), round(anchor.y + in_north, 3)
        out_x, out_y = round(anchor.x + out_east, 3), round(anchor.y + out_north, 3)

        in_name = f'{prefix}_R{rid}_IN'
        out_name = f'{prefix}_R{rid}_OUT'
        if not settings.overwrite and (in_name in build.nodes or out_name in build.nodes):
            build.skipped.append(rid)
            continue

        waypoint_names: list[NodeID] = []
        if settings.contour_used and len(swath) > 2:
            waypoints = resample_row_xy(
                swath, anchor.lat, anchor.lon, settings.waypoint_interval_m)
            for k, (wx, wy) in enumerate(waypoints, start=1):
                name = f'{prefix}_R{rid}_W{k}'
                if not settings.overwrite and name in build.nodes:
                    continue
                wlat, wlon = _f2c_xy_to_latlon(wx, wy, anchor.lat, anchor.lon)
                build.nodes[name] = _make_node(
                    name, round(anchor.x + wx, 3), round(anchor.y + wy, 3),
                    'waypoint', wlat, wlon, rid, settings)
                waypoint_names.append(name)

        build.nodes[in_name] = _make_node(
            in_name, in_x, in_y, 'entry', in_lat, in_lon, rid, settings)
        build.nodes[out_name] = _make_node(
            out_name, out_x, out_y, 'exit', out_lat, out_lon, rid, settings)

        for a_name, b_name in pairwise([in_name, *waypoint_names, out_name]):
            build.nodes[a_name].add_edge(b_name, action=row_action)

        build.added.append(rid)
        build.row_names[rid] = (in_name, out_name)

    if not build.added:
        return build

    for rid_a, rid_b in pairwise(build.added):
        # Fragments of one row must not be linked across obstacles or field gaps.
        if rid_a - settings.row_id_start in settings.break_after:
            continue
        _add_headland_edge(build.nodes, build.row_names[rid_a][1], build.row_names[rid_b][0])

    if settings.connect_to:
        for target in (build.row_names[build.added[0]][0], build.row_names[build.added[-1]][1]):
            build.nodes[target].add_edge(settings.connect_to, action=NAV_ACTION)
    return build


def apply_row_nodes(doc: TopoDoc, build: RowBuild, prefix: str, overwrite: bool) -> None:
    """Insert the generated nodes into ``doc`` in place, without persisting it.

    With ``overwrite``, existing ``<prefix>_R*`` nodes are removed first, together
    with any edges pointing at them, even when the build is empty. Any remaining
    nodes whose names match generated nodes are kept unchanged. Inserted nodes
    are shared with ``build``; no reverse edges are added to existing nodes.
    """
    if overwrite:
        old_names = {node.name for node in doc.nodes if node.name.startswith(f'{prefix}_R')}
        if old_names:
            doc.remove_nodes(old_names)
    existing = {node.name for node in doc.nodes}
    for node in build.nodes.values():
        if node.name in existing:
            continue
        # insert_node(), not add_node(): edges inside this batch are already
        # wired both ways, so add_node()'s reverse-edge backfill would raise
        # KeyError on a sibling not yet inserted, and would add an unwanted
        # reverse edge onto a pre-existing connect_to node.
        doc.insert_node(node)


@dataclass(frozen=True)
class RepairPlan:
    """The edges that should exist for the rows already in a map."""

    row_count: int
    edges: tuple[tuple[NodeID, NodeID, str], ...]
    headlands_skipped: bool = False


def plan_row_repair(doc: TopoDoc, row_action: str) -> RepairPlan | None:
    """Work out which in-row and headland edges the rows in ``doc`` should have.

    Rows are found from the ``row_id`` and ``row_role`` metadata. In-row edges chain
    entry, waypoints (in ``_W<n>`` order) and exit, so a repair restores the curve
    instead of collapsing it to one hop. Headland edges join same-end neighbours,
    classified by geometry rather than the IN/OUT label, because snake ordering
    flips the label against the physical end. Waypoints are left out of that
    classification, since they are not row ends.

    This works from coordinates alone because the map on disk may contain nodes
    dropped by hand with no known generation order. Nodes with missing IDs,
    unrecognized roles, or IDs whose integer conversion raises TypeError or
    ValueError are ignored. In-row chains require distinct entry and exit nodes.

    Returns:
        A plan of (source, destination, action) edges without modifying ``doc`` or
        filtering existing edges, or None if no usable row metadata is found.
        The row count includes incomplete rows. Headlands are marked skipped
        when fewer than two endpoint coordinates are available.

    Raises:
        OverflowError: A row ID is infinite and cannot be converted to an integer.
    """
    rows: dict[int, dict] = {}
    coords: dict[NodeID, tuple[float, float]] = {}
    for node in doc.nodes:
        row_id = node.meta.get('row_id')
        role = node.meta.get('row_role')
        if row_id is None or role not in ROW_ROLES:
            continue
        try:
            rid = int(row_id)
        except (TypeError, ValueError):
            continue
        if role == 'waypoint':
            rows.setdefault(rid, {}).setdefault('waypoints', []).append(node.name)
        else:
            rows.setdefault(rid, {})[role] = node.name
            coords[node.name] = (node.x, node.y)

    if not rows:
        return None

    def waypoint_order(name: str) -> int:
        """Return the numeric ``_W<n>`` suffix, or zero when the suffix is absent."""
        match = _WAYPOINT_SUFFIX.search(name)
        return int(match.group(1)) if match else 0

    edges: list[tuple[NodeID, NodeID, str]] = []
    for rid in sorted(rows):
        entry, exit_ = rows[rid].get('entry'), rows[rid].get('exit')
        if entry and exit_ and entry != exit_:
            waypoints = sorted(rows[rid].get('waypoints', []), key=waypoint_order)
            edges.extend((a, b, row_action) for a, b in pairwise([entry, *waypoints, exit_]))

    headlands_skipped = len(coords) < 2
    if not headlands_skipped:
        for a_name, b_name in headland_neighbour_pairs(coords):
            edges.append((a_name, b_name, NAV_ACTION))
            edges.append((b_name, a_name, NAV_ACTION))
    return RepairPlan(len(rows), tuple(edges), headlands_skipped)


def add_missing_edges(doc: TopoDoc, edges: tuple[tuple[NodeID, NodeID, str], ...]) -> int:
    """Add each wanted edge that ``doc`` lacks, and return how many were added.

    Each edge is a (source, destination, action) tuple. Mutate ``doc`` in place
    without persisting it. Edges from unknown nodes, self-links and edges to an
    already connected destination are ignored, preserving existing actions.
    Destination nodes need not exist; reverse edges are not added.
    """
    nodes = {node.name: node for node in doc.nodes}
    added = 0
    for src, dst, action in edges:
        node = nodes.get(src)
        if node is None or src == dst or node.is_connected_to(dst):
            continue
        node.add_edge(dst, action=action)
        added += 1
    return added
