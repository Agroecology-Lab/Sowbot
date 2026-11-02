"""Map, conflict test and path search."""
import json
import math


def _pt_seg(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def seg_dist(a, b, c, d):
    """Return the minimum distance between 2D segments a-b and c-d, including points."""
    if a != b and c != d:
        if _cross(a, b, c) * _cross(a, b, d) < 0 and _cross(c, d, a) * _cross(c, d, b) < 0:
            return 0.0
    return min(_pt_seg(a, c, d), _pt_seg(b, c, d), _pt_seg(c, a, b), _pt_seg(d, a, b))


class Graph:
    def __init__(self, edges, xy=None, clearance=1.0, park=()):
        """Build an undirected graph with optional node coordinates and parking nodes.

        Clearance uses the same units as xy and is the exclusive conflict
        distance threshold. Park nodes are excluded as retreat waypoints.
        """
        self.xy = {int(k): tuple(v) for k, v in (xy or {}).items()}
        self.clearance, self.park = clearance, set(park)   # park: docks/chargers, never used as side-step points
        self.adj = {}
        for a, b in edges:
            self.adj.setdefault(a, set()).add(b)
            self.adj.setdefault(b, set()).add(a)

    @classmethod
    def load(cls, path, clearance=1.0):
        """Load a graph from JSON with edges and optional nodes and park fields.

        File and JSON errors propagate, as does KeyError if edges is absent.
        """
        with open(path) as fh:
            d = json.load(fh)
        return cls([tuple(e) for e in d["edges"]], d.get("nodes"), clearance, d.get("park", ()))

    def conflict(self, e1, e2):
        """Return whether two node pairs conflict.

        With coordinates, distance strictly below clearance conflicts and a
        missing node counts as a conflict. Without coordinates, pairs conflict
        only when they share a node.
        """
        if not self.xy:
            return bool(set(e1) & set(e2))
        try:
            return seg_dist(self.xy[e1[0]], self.xy[e1[1]], self.xy[e2[0]], self.xy[e2[1]]) < self.clearance
        except KeyError:
            return True

    def path(self, a, b, edge_ok=lambda u, v: True):
        """Shortest hop path a to b over edges allowed by edge_ok, or None."""
        prev, queue = {a: None}, [a]
        for u in queue:
            if u == b:
                break
            for v in sorted(self.adj.get(u, ())):
                if v not in prev and edge_ok(u, v):
                    prev[v] = u
                    queue.append(v)
        if b not in prev:
            return None
        out, n = [], b
        while n is not None:
            out.append(n)
            n = prev[n]
        return out[::-1]

