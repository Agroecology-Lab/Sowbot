"""Build dock and route nodes from a farm map's docks and routes.

Like topo_row_builder, this works on in-memory ``TopoDoc`` objects and plain values, with no ROS,
NiceGUI or persistence dependency. A dock becomes a node ``<prefix>_D_<NAME>`` and each point a route
passes through becomes ``<prefix>_D_<ROUTE>_<k>``. An overwrite removes earlier generated docks
and routes with the same prefix. The nodes are joined to the row end the route names by
navigation edges in both directions.
"""
import math
import re
from dataclasses import dataclass, field
from itertools import pairwise

from devkit_f2c_planner.f2c_planner import f2c_latlon_to_xy
from devkit_ui.constants import NAV_ACTION, YAW_GOAL_TOLERANCE
from devkit_ui.models import (
    NodeID,
    TopoDoc,
    TopoNode,
    TopoPose,
    TopoProperties,
    Vector2,
    yaw_to_orientation,
)
from devkit_ui.topo_row_builder import RowAnchor

# Meta key on every dock and route node: the prefix of the save that made it.
DOCK_PREFIX_KEY = 'dock_prefix'
_NODE_VERTS = (Vector2(x=-1.0, y=-1.0), Vector2(x=1.0, y=-1.0), Vector2(x=1.0, y=1.0), Vector2(x=-1.0, y=1.0))
# A dock or route point with no heading accepts any final yaw.
ANY_YAW_TOLERANCE = math.pi


@dataclass(frozen=True)
class DockBuildSettings:
    """Naming and metadata for one batch of dock nodes."""

    prefix: str
    nav_frame: str
    map_name: str
    timestamp: str
    fix_type: int
    # Direction of the map x axis in degrees counter-clockwise from east, as MAP_YAW_DEG.
    map_yaw_deg: float = 0.0


@dataclass
class DockBuild:
    """The nodes generated for the docks and routes of a farm map.

    ``links`` are (existing node, new node) pairs: the row end of each route and the node next to it, which
    need a reverse edge once the row end is in the map. ``skipped`` names the routes whose row end was not
    among the built rows; their dock is still built.
    """

    nodes: dict[NodeID, TopoNode] = field(default_factory=dict)
    links: list[tuple[NodeID, NodeID]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def build_dock_nodes(docks: tuple | list, routes: tuple | list, anchor: RowAnchor,
                     settings: DockBuildSettings, row_nodes: dict[NodeID, TopoNode]) -> DockBuild:
    """Turn docks and routes into nodes and the edges along each route.

    A route ends at a row node named ``F<field>R<row>N<n>``. It is found through the ``lf_field``,
    ``lf_row`` and ``lf_frag`` metadata of ``row_nodes`` using the exact exported endpoint identity
    resolved by the farm map reader. The first and last points of a route are the dock and the row end, so only
    the points between them become nodes. Routes without a matching row end or dock are listed as
    skipped. Matched row nodes receive the exported endpoint name in their ``lf_node`` metadata;
    nothing is persisted.

    A dock with a ``heading_deg`` faces it, read as a compass bearing: degrees clockwise from north. With
    ``settings.map_yaw_deg`` 0, 90 faces east, map +x; the goal is turned by that yaw so the dock faces the
    same compass bearing in a rotated map. A dock without one, and every route point, accepts any final yaw, so the
    goal checker does not ask for a turn the map never specified.

    Raises:
        ValueError: Generated names collide, a target is not an exported row endpoint, or route
            endpoint coordinates do not match the named nodes. Earlier row-node metadata changes
            are not rolled back.
    """
    build = DockBuild()
    ends: dict[str, NodeID] = {}
    for dock in docks:
        name = _node_name(settings.prefix, dock.name)
        east, north = f2c_latlon_to_xy(dock.lat, dock.lon, anchor.lat, anchor.lon)
        extra = {'role': 'dock', 'farm_name': dock.name, 'heading_deg': dock.heading_deg}
        yaw = None if dock.heading_deg is None else _compass_to_yaw(dock.heading_deg, settings.map_yaw_deg)
        _add(build, _make_node(name, anchor, east, north, dock.lat, dock.lon, settings, extra, yaw))
        ends[dock.name] = name

    for route in routes:
        target = _row_end(route, row_nodes)
        if target is None or route.from_node not in ends:
            build.skipped.append(route.name)
            continue
        dock_node = build.nodes[ends[route.from_node]]
        row_node = row_nodes[target]
        for supplied, node in ((route.points[0], dock_node), (route.points[-1], row_node)):
            if tuple(round(v, 7) for v in supplied) != (node.properties.gps_lat, node.properties.gps_lon):
                raise ValueError(f'route {route.name}: endpoint coordinates do not match named node')
        row_node.meta['lf_node'] = route.to_node
        chain = [ends[route.from_node]]
        for k, (lat, lon) in enumerate(route.points[1:-1], start=1):
            name = _node_name(settings.prefix, f'{route.name}_{k}')
            east, north = f2c_latlon_to_xy(lat, lon, anchor.lat, anchor.lon)
            extra = {'role': 'route', 'farm_name': route.name}
            _add(build, _make_node(name, anchor, east, north, lat, lon, settings, extra))
            chain.append(name)
        for a_name, b_name in pairwise([*chain, target]):
            if b_name == target:
                build.links.append((target, a_name))
                build.nodes[a_name].add_edge(target, action=NAV_ACTION)
            else:
                build.nodes[a_name].add_edge(b_name, action=NAV_ACTION)
                build.nodes[b_name].add_edge(a_name, action=NAV_ACTION)
    return build


def apply_dock_nodes(doc: TopoDoc, build: DockBuild, prefix: str, overwrite: bool) -> None:
    """Insert the generated dock nodes into ``doc`` in place, without persisting it.

    With ``overwrite``, the dock and route nodes an earlier save with this prefix made are removed first.
    Names taken by unrelated nodes are rejected before mutation. Each link adds an edge from the row end to
    the route, when both are in ``doc``. Call it after the rows are applied.

    Raises ValueError before mutation for a name collision or a link that does not refer to the
    intended nodes.
    """
    for name, node in build.nodes.items():
        if doc.has_node(name):
            existing = doc.get_node(name)
            if (existing.meta.get(DOCK_PREFIX_KEY) != prefix
                    or existing.meta.get('role') != node.meta.get('role')
                    or existing.meta.get('farm_name') != node.meta.get('farm_name')):
                raise ValueError(f'dock or route node name collision: {name}')
    for row_end, route_node in build.links:
        if row_end in build.nodes or route_node not in build.nodes or not doc.has_node(row_end):
            raise ValueError('dock route link does not refer to the intended nodes')
    if overwrite:
        old = {n.name for n in doc.nodes if n.meta.get(DOCK_PREFIX_KEY) == prefix}
        if old:
            doc.remove_nodes(old)
    existing = {n.name for n in doc.nodes}
    for node in build.nodes.values():
        if node.name not in existing:
            doc.insert_node(node)
    for row_end, route_node in build.links:
        if doc.has_node(row_end) and doc.has_node(route_node):
            doc.get_node(row_end).add_edge(route_node, action=NAV_ACTION)


def _add(build: DockBuild, node: TopoNode) -> None:
    """Add node to the build, or raise ValueError if its name is already used."""
    if node.name in build.nodes:
        raise ValueError(f'two docks or routes make the node name {node.name}')
    build.nodes[node.name] = node


def _node_name(prefix: str, name: str) -> str:
    """Return the node name for a dock or route point called name."""
    return f'{prefix}_D_{re.sub(r"[^A-Z0-9_]", "", name.upper())}'


def _row_end(route, row_nodes: dict[NodeID, TopoNode]) -> NodeID | None:
    """Resolve the exact exported fragment endpoint, without guessing from its index."""
    if route.target is None:
        match = re.fullmatch(r'F(\d+)R(\d+)N(\d+)', route.to_node)
        if match and not any((n.meta.get('lf_field'), n.meta.get('lf_row')) ==
                             (int(match[1]), int(match[2])) for n in row_nodes.values()):
            return None
        raise ValueError(f'route {route.name}: target is not an exported row endpoint')
    field_number, row, frag, role = route.target
    found = [n for n in row_nodes.values()
             if (n.meta.get('lf_field'), n.meta.get('lf_row'), n.meta.get('lf_frag'),
                 n.meta.get('row_role')) == (field_number, row, frag, role)]
    return found[0].name if len(found) == 1 else None


def _compass_to_yaw(heading_deg: float, map_yaw_deg: float = 0.0) -> float:
    """Return the map-frame yaw in radians, within [-pi, pi], of a compass heading in degrees.

    With the map x axis east (``map_yaw_deg`` 0) and +y north, north (0 degrees) is a yaw of pi / 2.
    A map whose x axis points ``map_yaw_deg`` counter-clockwise from east turns every yaw by that
    angle the other way.
    """
    yaw = math.pi / 2 - math.radians(heading_deg) - math.radians(map_yaw_deg)
    return math.atan2(math.sin(yaw), math.cos(yaw))


def _make_node(name: str, anchor: RowAnchor, east: float, north: float, lat: float, lon: float,
               settings: DockBuildSettings, extra: dict, yaw: float | None = None) -> TopoNode:
    """Create one dock or route node at a lat/lon projected about the anchor.

    yaw is the goal heading in radians from map +x. With None the pose keeps the identity
    orientation and the node accepts any final yaw.
    """
    return TopoNode(
        name=name,
        nav_frame=settings.nav_frame,
        edges=[],
        pose=TopoPose(x=round(anchor.x + east, 3), y=round(anchor.y + north, 3),
                      orientation=yaw_to_orientation(0.0 if yaw is None else yaw)),
        properties=TopoProperties(
            xy_goal_tolerance=0.3,
            yaw_goal_tolerance=ANY_YAW_TOLERANCE if yaw is None else YAW_GOAL_TOLERANCE,
            dropped_by='webui_f2c',
            timestamp=settings.timestamp,
            gps_lat=round(lat, 7),
            gps_lon=round(lon, 7),
            gps_fix_type=settings.fix_type,
            gps_hdop=None,
        ),
        verts=list(_NODE_VERTS),
        meta={'map': settings.map_name, 'node': name, 'pointset': settings.map_name,
              'dropped_by': 'webui_f2c', 'timestamp': settings.timestamp,
              'gps_fix_type': settings.fix_type, 'gps_hdop': None,
              DOCK_PREFIX_KEY: settings.prefix, **extra},
    )
