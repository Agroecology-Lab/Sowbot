import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sweeps"))
import make_map  # noqa: E402
import pytest  # noqa: E402

from sowbot_avoid.topo import load_topo  # noqa: E402


def _topo(tmp_path, **kw):
    p = tmp_path / "m.json"
    make_map.main(["-o", str(p)] + [x for k, v in kw.items() for x in (f"--{k}", str(v))])
    return load_topo(str(p))


def test_nodes_every_step_along_rows_and_docks_recognised(tmp_path):
    t = _topo(tmp_path, rows=4, length=12, docks=2)
    assert t.ids_stored and t.rows == [1, 2, 3, 4] and len(t.docks) == 2
    for r in t.rows:
        row = t.row_nodes(r)
        assert len(row) == 5 and t.role[row[0]] == "entry" and t.role[row[-1]] == "exit"
        assert all(math.isclose(math.dist(t.graph.xy[a], t.graph.xy[b]), 3.0) for a, b in zip(row, row[1:]))


def test_whole_map_is_connected(tmp_path):
    t = _topo(tmp_path, rows=5, length=9, docks=3)
    seen, todo = set(), [next(iter(t.graph.adj))]
    while todo:
        n = todo.pop()
        if n not in seen:
            seen.add(n)
            todo += t.graph.adj[n]
    assert seen == set(t.graph.adj)


def test_more_docks_than_rows_is_refused():
    with pytest.raises(ValueError):
        make_map.build(rows=2, docks=3)


def test_drop_docks_removes_them_and_their_edges(tmp_path):
    p = tmp_path / "m.json"
    make_map.main(["-o", str(p), "--rows", "4", "--length", "12", "--docks", "2"])
    from sowbot_avoid.topo import load_topo
    kept, dropped = load_topo(str(p)), load_topo(str(p), drop_docks=True)
    assert len(kept.docks) == 2 and dropped.docks == []
    assert len(dropped.ids) == len(kept.ids) - 2
    assert not any(n.lower().startswith("dock") for n in dropped.ids)


def test_legs_make_routes_longer_and_stay_on_edges(tmp_path):
    import random
    import maize_sweep as ms
    from sowbot_avoid.topo import load_topo
    p = tmp_path / "m.json"
    make_map.main(["-o", str(p), "--rows", "4", "--length", "12"])
    t = load_topo(str(p))
    one = ms.scenario(t, "random", 2, random.Random("a"), 8.0, 1)
    six = ms.scenario(t, "random", 2, random.Random("a"), 8.0, 6)
    assert sum(map(len, six)) > 3 * sum(map(len, one))
    assert all(b in t.graph.adj[a] for r in six for a, b in zip(r, r[1:]))
