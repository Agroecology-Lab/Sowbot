"""Build dock and route nodes from a farm map's docks and routes.

Like topo_row_builder, this works on in-memory ``TopoDoc`` objects and plain values, with no ROS,
NiceGUI or persistence dependency. A dock becomes a node ``<prefix>_D_<NAME>`` and each point a route
passes through becomes ``<prefix>_D_<ROUTE>_<k>``. The names never start with ``<prefix>_R``, so saving
rows with overwrite does not remove them. The nodes are joined to the row end the route names by
navigation edges in both directions.
"""
import re
from dataclasses import dataclass, field
from itertools import pairwise

from devkit_f2c_planner.f2c_planner import f2c_latlon_to_xy
from devkit_ui.constants import NAV_ACTION
from devkit_ui.models import NodeID, TopoDoc, TopoNode, TopoPose, TopoProperties, Vector2
from devkit_ui.topo_row_builder import RowAnchor

# Meta key on every dock and route node: the prefix of the save that made it.
DOCK_PREFIX_KEY = 'dock_prefix'
_ROW_END = re.compile(r'^F(\d+)R(\d+)N(\d+)$')
_NODE_VERTS = (Vector2(x=-1.0, y=-1.0), Vector2(x=1.0, y=-1.0), Vector2(x=1.0, y=1.0), Vector2(x=-1.0, y=1.0))


@dataclass(frozen=True)
class DockBuildSettings:
    """Naming and metadata for one batch of dock nodes."""

    prefix: str
    nav_frame: str
    map_name: str
    timestamp: str
    fix_type: int


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
    ``lf_row`` and ``lf_frag`` metadata of ``row_nodes``: N0 is the entry of the row's first fragment and
    any other index is its exit. The first and last points of a route are the dock and the row end, so only
    the points between them become nodes. Nothing is mutated or persisted.

    Raises:
        ValueError: Two docks or route points get the same node name.
    """
    build = DockBuild()
    ends: dict[str, NodeID] = {}
    for dock in docks:
        name = _node_name(settings.prefix, dock.name)
        east, north = f2c_latlon_to_xy(dock.lat, dock.lon, anchor.lat, anchor.lon)
        extra = {'role': 'dock', 'farm_name': dock.name, 'heading_deg': dock.heading_deg}
        _add(build, _make_node(name, anchor, east, north, dock.lat, dock.lon, settings, extra))
        ends[dock.name] = name

    for route in routes:
        target = _row_end(route.to_node, row_nodes)
        if target is None or route.from_node not in ends:
            build.skipped.append(route.name)
            continue
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
    Nodes whose names are taken by other nodes are kept unchanged. Each link adds an edge from the row end to
    the route, when both are in ``doc``. Call it after the rows are applied.
    """
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


def _row_end(to_node: str, row_nodes: dict[NodeID, TopoNode]) -> NodeID | None:
    """Return the name of the row node ``F<field>R<row>N<n>`` stands for, or None if it is not built."""
    match = _ROW_END.match(to_node)
    if not match:
        return None
    field_number, row, index = (int(g) for g in match.groups())
    wanted = 'entry' if index == 0 else 'exit'
    found = [n for n in row_nodes.values()
             if n.meta.get('lf_field') == field_number and n.meta.get('lf_row') == row
             and n.meta.get('row_role') == wanted]
    return min(found, key=lambda n: n.meta.get('lf_frag', 0)).name if found else None


def _make_node(name: str, anchor: RowAnchor, east: float, north: float, lat: float, lon: float,
               settings: DockBuildSettings, extra: dict) -> TopoNode:
    """Create one dock or route node at a lat/lon projected about the anchor."""
    return TopoNode(
        name=name,
        nav_frame=settings.nav_frame,
        edges=[],
        pose=TopoPose(x=round(anchor.x + east, 3), y=round(anchor.y + north, 3)),
        properties=TopoProperties(
            xy_goal_tolerance=0.3,
            yaw_goal_tolerance=0.1,
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
