"""Route generation for the maize sweep. The sweep itself is not run here: it uses wall-clock timing."""
import math
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sweeps"))
import maize_sweep as ms  # noqa: E402

from sowbot_avoid.topo import load_topo  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "data", "maize_map_fixture.json")


@pytest.mark.parametrize("name", ["follow", "opposed", "random", "local"])
def test_starts_distinct_and_clear_and_routes_follow_edges(name):
    t = load_topo(FIXTURE)
    routes = ms.scenario(t, name, 3, random.Random(1), 8.0)
    starts = [r[0] for r in routes]
    assert len(set(starts)) == 3
    for i, a in enumerate(starts):
        for b in starts[i + 1:]:
            assert math.dist(t.graph.xy[a], t.graph.xy[b]) >= 1.0
    for r in routes:
        assert len(r) >= 3 and all(b in t.graph.adj[a] for a, b in zip(r, r[1:]))


def test_too_many_robots_is_infeasible():
    with pytest.raises(ms.Infeasible):
        ms.scenario(load_topo(FIXTURE), "random", 12, random.Random(0), 8.0)


def test_docks_are_park_nodes_and_distinct():
    t = load_topo(FIXTURE)
    routes = ms.scenario(t, "follow", 3, random.Random(2), 8.0)
    g, out = ms.add_docks(t, routes)
    ends = [r[-1] for r in out]
    assert set(ends) <= g.park and len(set(ends)) == 3
    assert all(b in g.adj[a] for r in out for a, b in zip(r, r[1:]))
    for i, a in enumerate(ends):
        for b in ends[i + 1:]:
            assert math.dist(g.xy[a], g.xy[b]) >= 1.0
