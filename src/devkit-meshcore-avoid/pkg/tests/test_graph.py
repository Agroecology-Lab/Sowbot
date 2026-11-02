import json

import pytest

from sowbot_avoid.graph import Graph, seg_dist


@pytest.mark.parametrize(('first', 'second', 'distance'), [
    (((0, 0), (2, 2)), ((0, 2), (2, 0)), 0),
    (((0, 0), (1, 0)), ((2, 0), (3, 0)), 1),
    (((0, 0), (2, 0)), ((1, 1), (1, 1)), 1),
    (((0, 0), (0, 0)), ((3, 4), (3, 4)), 5),
    (((0, 0), (2, 0)), ((1, 0), (3, 0)), 0),
])
def test_segment_distances(first, second, distance):
    assert seg_dist(*first, *second) == pytest.approx(distance)
    assert seg_dist(*second, *first) == pytest.approx(distance)


def test_conflict_clearance_boundary_and_missing_coordinates():
    graph = Graph([], {1: (0, 0), 2: (2, 0), 3: (0, 1), 4: (2, 1)}, clearance=1)
    assert not graph.conflict((1, 2), (3, 4))
    graph.clearance = 1.01
    assert graph.conflict((1, 2), (3, 4))
    assert graph.conflict((1, 2), (3, 99))


def test_conflict_without_coordinates_only_uses_shared_nodes():
    graph = Graph([])
    assert graph.conflict((1, 2), (2, 3))
    assert not graph.conflict((1, 2), (3, 4))


def test_load_and_shortest_paths_with_filtered_edges(tmp_path):
    map_file = tmp_path / 'map.json'
    map_file.write_text(json.dumps({'nodes': {'1': [0, 0], '2': [1, 0]},
                                    'edges': [[1, 3], [3, 4], [1, 2], [2, 4]], 'park': [4]}))
    graph = Graph.load(map_file, clearance=2)
    assert graph.xy == {1: (0, 0), 2: (1, 0)}
    assert graph.park == {4} and graph.clearance == 2
    assert graph.path(1, 4) == [1, 2, 4]
    assert graph.path(4, 1) == [4, 2, 1]
    assert graph.path(1, 4, lambda u, v: v != 2) == [1, 3, 4]
    assert graph.path(1, 4, lambda u, v: False) is None
    assert graph.path(9, 9) == [9]
    assert graph.path(1, 9) is None


def test_load_requires_edges(tmp_path):
    map_file = tmp_path / 'map.json'
    map_file.write_text('{}')
    with pytest.raises(KeyError):
        Graph.load(map_file)


def test_path_prefers_fewer_hops_and_handles_cycles():
    graph = Graph([(1, 2), (2, 3), (3, 4), (1, 5), (5, 4), (2, 5)])
    assert graph.path(1, 4) == [1, 5, 4]
    assert graph.path(1, 4, lambda u, v: (u, v) != (5, 4)) == [1, 2, 3, 4]


def test_load_propagates_invalid_json(tmp_path):
    map_file = tmp_path / 'invalid.json'
    map_file.write_text('{invalid')
    with pytest.raises(json.JSONDecodeError):
        Graph.load(map_file)
