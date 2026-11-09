import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from deepdiff import DeepDiff

from devkit_ui.constants import NAV_ACTION, ROW_ACTION
from devkit_ui.models import TopoDoc, TopoNode
from devkit_ui.parse import _topo_from_dict, dump_topo_yaml, parse_topo_yaml


class TestParseTopo(unittest.TestCase):
    def test_dump_and_restore_yaml_round_trip(self) -> None:
        original = TopoDoc(
            name='test-topo',
            nodes=[
                TopoNode(name='A', x=0.0, y=0.0, edges=['B'], meta={'kind': 'start'}),
                TopoNode(name='B', x=1.0, y=1.0, edges=['A', 'C']),
                TopoNode(name='C', x=2.0, y=2.0, edges=['B']),
            ],
            meta={'map': 'field-1'},
            actions={'move_base': {}},
            definitions={'robot': {'type': 'tractor'}},
            transformation={'frame': 'map'},
        )

        with TemporaryDirectory() as tmp_dir:
            yaml_path = Path(tmp_dir) / 'topo.yaml'
            dump_topo_yaml(original, str(yaml_path))
            restored = parse_topo_yaml(str(yaml_path))

        original_dict = original.to_dict()
        restored_dict = restored.to_dict()
        # `dump_topo_yaml` injects a timestamp on write, so ignore it for equality.
        original_dict.get('meta', {}).pop('last_updated', None)
        restored_dict.get('meta', {}).pop('last_updated', None)

        diff = DeepDiff(restored_dict, original_dict)
        self.assertDictEqual(diff, {})

    def test_edge_action_survives_yaml_round_trip(self) -> None:
        """A non-empty edge action (e.g. limbic_row_follow) must not be
        silently dropped to '' on load. The previous test above only ever
        round-tripped edges built via edges=['B'] (plain strings), which
        already default to action='' in TopoNode.__init__ — so it never
        exercised a non-empty action and couldn't catch this regression."""
        a = TopoNode(name='A', x=0.0, y=0.0, edges=[])
        a.add_edge('B', action=ROW_ACTION)
        a.add_edge('C', action=NAV_ACTION)
        original = TopoDoc(
            name='test-topo-actions',
            nodes=[a, TopoNode(name='B', x=1.0, y=1.0, edges=[]),
                   TopoNode(name='C', x=2.0, y=2.0, edges=[])],
        )

        with TemporaryDirectory() as tmp_dir:
            yaml_path = Path(tmp_dir) / 'topo.yaml'
            dump_topo_yaml(original, str(yaml_path))
            restored = parse_topo_yaml(str(yaml_path))

        actions = {e.node: e.action for e in restored.get_node('A').edges}
        self.assertEqual(actions, {'B': ROW_ACTION, 'C': NAV_ACTION})

        # A second round trip (mirrors _persist_and_reload's load -> modify
        # -> save cycle on every subsequent map edit) must not corrupt it
        # either.
        with TemporaryDirectory() as tmp_dir:
            yaml_path = Path(tmp_dir) / 'topo2.yaml'
            dump_topo_yaml(restored, str(yaml_path))
            restored_again = parse_topo_yaml(str(yaml_path))

        actions_again = {e.node: e.action for e in restored_again.get_node('A').edges}
        self.assertEqual(actions_again, {'B': ROW_ACTION, 'C': NAV_ACTION})


if __name__ == '__main__':
    unittest.main()


class TestOrientationValidation(unittest.TestCase):
    @staticmethod
    def doc_with(orientation: dict | None) -> dict:
        pose = {'position': {'x': 0.0, 'y': 0.0}}
        if orientation is not None:
            pose['orientation'] = orientation
        return {'nodes': [{'node': {'name': 'A', 'pose': pose, 'edges': []}, 'meta': {}}]}

    def orientation(self, orientation: dict | None) -> dict:
        node = _topo_from_dict(self.doc_with(orientation)).get_node('A')
        return node.to_dict()['node']['pose']['orientation']

    def test_an_absent_or_empty_orientation_is_the_identity(self) -> None:
        identity = {'x': 0.0, 'y': 0.0, 'z': 0.0, 'w': 1.0}

        self.assertEqual(self.orientation(None), identity)
        self.assertEqual(self.orientation({}), identity)

    def test_a_partial_orientation_with_a_nonzero_quaternion_is_kept(self) -> None:
        self.assertEqual(self.orientation({'z': 1.0, 'w': 0.0}), {'x': 0.0, 'y': 0.0, 'z': 1.0, 'w': 0.0})

    def test_a_partial_orientation_that_makes_the_zero_quaternion_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, 'node A.*not a valid quaternion'):
            _topo_from_dict(self.doc_with({'w': 0.0}))

    def test_a_nonfinite_orientation_is_rejected(self) -> None:
        for value in (float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _topo_from_dict(self.doc_with({'z': value, 'w': 1.0}))
