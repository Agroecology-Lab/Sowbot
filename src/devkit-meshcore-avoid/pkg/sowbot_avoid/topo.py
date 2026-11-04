"""Load a Sowbot topological map (YAML, as written by the F2C planner) into a Graph.

Frames carry 16-bit ints, the map names nodes with strings, so every robot must use the
same name-to-int table. The table is taken from `properties.avoid_id` on each node when
every node has one. Otherwise it is assigned by sorted name, which shifts when nodes are
added or renamed; `ids_stored` is False in that case and `digest` lets two robots check
they hold the same table.

A node the farmer has renamed `dock`, `dock1`, `dock2`, ... (any case) is a dock: it goes into
`Graph.park`, so retreats never stop on it, and it is left out of the rows. A map with no such
node has no docks and nothing in the package assumes one.

PyYAML is needed for the YAML map only; the JSON fixture format needs nothing.
"""
import hashlib
import json
import math
import re
from dataclasses import dataclass, field

from .graph import Graph


_DOCK = re.compile(r"dock\d*", re.IGNORECASE)


def is_dock(name):
    """True for the names dock, dock1, dock2, ... in any case."""
    return _DOCK.fullmatch(str(name)) is not None


class TopoError(ValueError):
    """The map cannot be turned into a graph with stable ids."""


@dataclass
class TopoMap:
    graph: Graph
    ids: dict                       # node name -> int id
    names: dict                     # int id -> node name
    role: dict                      # int id -> entry / waypoint / exit
    row: dict                       # int id -> row number
    n_directed: int                 # directed edges as written in the file
    n_two_way: int                  # directed edges whose reverse is also present
    zero_length: list = field(default_factory=list)   # undirected edges between coincident nodes
    docks: list = field(default_factory=list)         # int ids of nodes named dock, dock1, ...
    ids_stored: bool = False
    digest: str = ""

    def row_nodes(self, r):
        """Node ids of row r in driving order: entry, waypoints in name order, exit."""
        rank = {"entry": 0, "waypoint": 1, "exit": 2}
        return sorted((n for n in self.row if self.row[n] == r),
                      key=lambda n: (rank.get(self.role[n], 1), _natural(self.names[n])))

    @property
    def rows(self):
        return sorted(set(self.row.values()))


def _natural(name):
    """Sort key that orders W2 before W10."""
    out, num = [], ""
    for ch in name:
        if ch.isdigit():
            num += ch
        else:
            if num:
                out.append((1, int(num), ""))
                num = ""
            out.append((0, 0, ch))
    if num:
        out.append((1, int(num), ""))
    return out


def digest_of(ids):
    """Short hash of the id table, for robots to compare."""
    text = ";".join(f"{i}:{n}" for n, i in sorted(ids.items(), key=lambda kv: kv[1]))
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def _assign_ids(names, stored):
    if stored and len(stored) != len(names):
        raise TopoError(f"avoid_id is set on {len(stored)} of {len(names)} nodes: set it on all or none")
    if stored:
        vals = list(stored.values())
        if len(set(vals)) != len(vals) or any(not 0 <= v <= 65535 for v in vals):
            raise TopoError("avoid_id values must be unique integers in 0-65535")
        return dict(stored), True
    return {n: i for i, n in enumerate(sorted(names, key=_natural), 1)}, False


def _build(nodes, edges, clearance):
    """nodes: name -> dict(xy, role, row, avoid_id|None); edges: iterable of (name, name)."""
    stored = {n: v["avoid_id"] for n, v in nodes.items() if v.get("avoid_id") is not None}
    ids, ids_stored = _assign_ids(list(nodes), stored)
    directed = {(a, b) for a, b in edges if a in nodes and b in nodes and a != b}
    und = {tuple(sorted((a, b))) for a, b in directed}
    zero = [(a, b) for a, b in und if math.dist(nodes[a]["xy"], nodes[b]["xy"]) == 0.0]
    docks = sorted(ids[n] for n in nodes if is_dock(n))
    # Side-step stops are row waypoints only: never an _IN or _OUT node, a headland node (no row
    # number) or a dock. Robots pass through those on the way.
    stops = {ids[n] for n, v in nodes.items() if not is_dock(n) and v["role"] == "waypoint" and v["row"] > 0}
    row_of = {ids[n]: v["row"] for n, v in nodes.items() if not is_dock(n) and v["row"] > 0}
    g = Graph([(ids[a], ids[b]) for a, b in sorted(und)], {ids[n]: v["xy"] for n, v in nodes.items()}, clearance,
              park=docks, rows=stops, row_of=row_of)
    for n in ids.values():
        g.adj.setdefault(n, set())              # isolated nodes stay in the graph
    names = {i: n for n, i in ids.items()}
    rowed = {n: v for n, v in nodes.items() if not is_dock(n)}      # a dock belongs to no row
    return TopoMap(g, ids, names, {ids[n]: v["role"] for n, v in rowed.items()},
                   {ids[n]: v["row"] for n, v in rowed.items()}, len(directed),
                   sum(1 for a, b in directed if (b, a) in directed),
                   [(ids[a], ids[b]) for a, b in zero], docks, ids_stored, digest_of(ids))


def load_topo(path, clearance=1.0):
    """Load the YAML topological map, or a JSON fixture written by `to_fixture`."""
    if str(path).endswith(".json"):
        with open(path) as fh:
            d = json.load(fh)
        nodes = {n: dict(xy=tuple(v["xy"]), role=v["role"], row=v["row"], avoid_id=v.get("id"))
                 for n, v in d["nodes"].items()}
        t = _build(nodes, [tuple(e) for e in d["edges"]], clearance)
        t.n_directed, t.n_two_way = d.get("n_directed", t.n_directed), d.get("n_two_way", t.n_two_way)
        return t
    try:
        import yaml
    except ImportError as e:
        raise TopoError("pyyaml is needed to read a YAML map: pip install pyyaml") from e
    with open(path) as fh:
        d = yaml.safe_load(fh)
    nodes, edges = {}, []
    for entry in d["nodes"]:
        n = entry["node"]
        p, props = n["pose"]["position"], n.get("properties") or {}
        nodes[n["name"]] = dict(xy=(float(p["x"]), float(p["y"])), role=props.get("row_role", "waypoint"),
                                row=int(props.get("row_id", 0)), avoid_id=props.get("avoid_id"))
        edges += [(n["name"], e["node"]) for e in n.get("edges") or []]
    return _build(nodes, edges, clearance)


def to_fixture(topo, path):
    """Write the small JSON fixture (names, positions, roles, rows, edges), so tests need no YAML or network."""
    g, names = topo.graph, topo.names
    d = dict(nodes={names[i]: dict(xy=list(g.xy[i]), role=topo.role.get(i, "waypoint"), row=topo.row.get(i, 0), id=i)
                    for i in sorted(names)},
             n_directed=topo.n_directed, n_two_way=topo.n_two_way,
             edges=sorted([names[a], names[b]] for a in g.adj for b in g.adj[a] if a < b))
    with open(path, "w") as fh:
        json.dump(d, fh, indent=1)
        fh.write("\n")
