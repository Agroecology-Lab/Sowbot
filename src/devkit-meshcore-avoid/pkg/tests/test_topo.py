import json
import os

import pytest

from sowbot_avoid.topo import TopoError, digest_of, load_topo, to_fixture

FIXTURE = os.path.join(os.path.dirname(__file__), "data", "maize_map_fixture.json")


def test_fixture_loads_with_stable_ids():
    t = load_topo(FIXTURE)
    assert len(t.ids) == len(t.graph.xy) == 9 and t.ids_stored
    assert t.names[t.ids["F2C_R2_W1"]] == "F2C_R2_W1"
    assert t.n_directed == 17 and t.n_two_way == 14 and t.zero_length == []


def test_row_nodes_in_driving_order():
    t = load_topo(FIXTURE)
    assert [t.names[n] for n in t.row_nodes(2)] == ["F2C_R2_IN", "F2C_R2_W1", "F2C_R2_OUT"]


def test_edges_are_undirected_in_graph():
    t = load_topo(FIXTURE)
    for a, nbrs in t.graph.adj.items():
        assert all(a in t.graph.adj[b] for b in nbrs)


def test_round_trip_keeps_ids_and_digest(tmp_path):
    t = load_topo(FIXTURE)
    p = tmp_path / "again.json"
    to_fixture(t, p)
    u = load_topo(p)
    assert u.ids == t.ids and u.digest == t.digest and u.n_directed == t.n_directed


def test_digest_changes_when_ids_shift():
    assert digest_of({"a": 1, "b": 2}) != digest_of({"a": 2, "b": 1})


def test_duplicate_stored_ids_rejected(tmp_path):
    d = json.load(open(FIXTURE))
    for v in d["nodes"].values():
        v["id"] = 7
    p = tmp_path / "dup.json"
    p.write_text(json.dumps(d))
    with pytest.raises(TopoError):
        load_topo(p)


def test_zero_length_edge_reported(tmp_path):
    d = json.load(open(FIXTURE))
    names = sorted(d["nodes"])
    d["nodes"][names[1]]["xy"] = d["nodes"][names[0]]["xy"]
    d["edges"].append([names[0], names[1]])
    p = tmp_path / "zero.json"
    p.write_text(json.dumps(d))
    t = load_topo(p)
    assert (t.ids[names[0]], t.ids[names[1]]) in t.zero_length


def test_partial_stored_ids_rejected(tmp_path):
    d = json.load(open(FIXTURE))
    del d["nodes"][sorted(d["nodes"])[0]]["id"]
    p = tmp_path / "partial.json"
    p.write_text(json.dumps(d))
    with pytest.raises(TopoError):
        load_topo(p)


def test_side_step_stops_are_row_waypoints_only():
    t = load_topo(FIXTURE)
    stops = {t.names[n] for n in t.graph.rows}
    assert stops and all("_W" in n for n in stops)
    assert not any(n.endswith(("_IN", "_OUT")) for n in stops)
    assert t.graph.row_key(t.ids["F2C_R2_W1"]) == t.graph.row_key(t.ids["F2C_R2_OUT"]) != t.graph.row_key(t.ids["F2C_R1_IN"])


def test_yaml_map_with_blank_row_id_loads(tmp_path):
    """Headland and dock nodes in a real map carry row_id '' (or null): no row, not a crash."""
    pytest.importorskip("yaml")

    def node(name, x, row_id, edges=()):
        return {"node": {"name": name, "pose": {"position": {"x": x, "y": 0.0}},
                         "properties": {"row_id": row_id, "row_role": "waypoint"},
                         "edges": [{"node": e} for e in edges]}}

    p = tmp_path / "map.yaml"
    p.write_text(json.dumps({"nodes": [node("a", 0.0, 1, ["b"]), node("b", 3.0, "", ["c"]),
                                       node("c", 6.0, None)]}))     # JSON is valid YAML
    t = load_topo(str(p))
    assert [t.row[t.ids[n]] for n in "abc"] == [1, 0, 0]
