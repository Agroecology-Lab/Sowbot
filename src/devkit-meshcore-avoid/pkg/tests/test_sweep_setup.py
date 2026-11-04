"""Route generation for the maize sweep. The sweep itself is not run here: it uses wall-clock timing."""
import json
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


def _with_docks(tmp_path, names):
    """The fixture plus dock nodes, each joined to one row exit."""
    d = json.load(open(FIXTURE))
    outs = sorted(n for n in d["nodes"] if n.endswith("_OUT"))
    for k, name in enumerate(names):
        x, y = d["nodes"][outs[k]]["xy"]
        d["nodes"][name] = dict(xy=[x + 1.5, y], role="waypoint", row=0, id=100 + k)
        d["edges"].append([outs[k], name])
    p = tmp_path / "docks.json"
    p.write_text(json.dumps(d))
    return load_topo(p)


def test_map_without_docks_gets_none_and_routes_are_unchanged():
    t = load_topo(FIXTURE)
    routes = ms.scenario(t, "follow", 3, random.Random(2), 8.0)
    assert t.docks == [] and t.graph.park == set()
    assert ms.assign_docks(t, routes) == routes


def test_only_nodes_named_dock_are_docks(tmp_path):
    t = _with_docks(tmp_path, ["Dock", "dock2", "docking"])
    assert {t.names[i] for i in t.docks} == {"Dock", "dock2"}


def test_docks_are_park_nodes_outside_the_rows_and_used_once(tmp_path):
    t = _with_docks(tmp_path, ["dock1", "dock2"])
    assert {t.names[i] for i in t.docks} == {"dock1", "dock2"}
    assert set(t.docks) == t.graph.park
    assert all(d not in t.row and d not in t.graph.rows for d in t.docks)
    routes = ms.scenario(t, "random", 3, random.Random(2), 8.0)
    assert all(not set(r) & set(t.docks) for r in routes)
    out = ms.assign_docks(t, routes)
    ends = [r[-1] for r in out]
    assert sum(e in t.docks for e in ends) == 2 and len({e for e in ends if e in t.docks}) == 2
    assert all(b in t.graph.adj[a] for r in out for a, b in zip(r, r[1:]))
