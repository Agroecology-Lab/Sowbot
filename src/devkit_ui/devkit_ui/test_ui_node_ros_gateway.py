"""Verify NiceGuiNode creates the track timer through RosGateway."""
# pylint: disable=exec-used,no-member,protected-access,attribute-defined-outside-init
import ast
import re
import unittest
from pathlib import Path
from unittest.mock import Mock

from devkit_ui.models import TopoDoc
from devkit_ui.view_models.run_view_model import RunViewModel


def load_gateway_harness():
    """Load the real start_track method from ui_node.py."""
    source = Path(__file__).with_name('ui_node.py')
    tree = ast.parse(source.read_text(encoding='utf-8'))
    node_class = next(node for node in tree.body
                      if isinstance(node, ast.ClassDef) and node.name == 'NiceGuiNode')
    names = {'start_track'}
    members = [node for node in node_class.body
               if isinstance(node, ast.FunctionDef) and node.name in names]
    harness = ast.ClassDef(name='Harness', bases=[], keywords=[], body=members, decorator_list=[])
    namespace = {'re': re}
    module = ast.fix_missing_locations(ast.Module(body=[harness], type_ignores=[]))
    exec(compile(module, source, 'exec'), namespace)
    return namespace['Harness']()


class TestUiNodeRosGateway(unittest.TestCase):
    def setUp(self) -> None:
        """Build a harness whose node-level create_* methods fail the test if they are used."""
        self.node = load_gateway_harness()
        self.node._ros = Mock()
        self.node.create_publisher = Mock()
        self.node.create_timer = Mock()
        self.node._track_timer = None
        self.node._topo_doc = TopoDoc(name='field.yaml')
        self.node._run_vm = RunViewModel(Mock())
        self.node.drop_topo_node = Mock()

    def tearDown(self) -> None:
        """Check that nothing bypassed the gateway to reach the node directly."""
        self.node.create_publisher.assert_not_called()
        self.node.create_timer.assert_not_called()

    def test_track_timer_uses_gateway(self) -> None:
        """Verify start_track records one node at once, then creates the repeating timer on the gateway."""
        self.node.start_track('row a', 2.5, None, 'entry')

        self.node._ros.create_timer.assert_called_once()
        period, callback = self.node._ros.create_timer.call_args.args
        self.assertEqual(period, 2.5)
        self.assertIs(self.node._track_timer, self.node._ros.create_timer.return_value)
        self.assertEqual(self.node.drop_topo_node.call_args_list[0].args, ('ROW_A_1', None, 'entry'))

        callback()

        self.assertEqual(self.node.drop_topo_node.call_args_list[1].args, ('ROW_A_2', None, 'entry'))

    def test_track_timer_not_created_when_prefix_missing(self) -> None:
        """Verify an empty prefix reports an error and creates no timer."""
        self.node.start_track('  ', 2.5, None, None)

        self.node._ros.create_timer.assert_not_called()
        self.assertIsNone(self.node._track_timer)
        self.assertEqual(self.node._run_vm.track.status, 'ERROR: prefix required')

    def test_existing_timer_prevents_duplicate_gateway_timer_and_immediate_drop(self) -> None:
        """Starting twice must keep the original timer and its recording callback."""
        self.node.start_track('row', 1.0, 2, 'entry')
        original_timer = self.node._track_timer
        self.node.drop_topo_node.reset_mock()

        self.node.start_track('other', 5.0, 3, 'entry')

        self.node._ros.create_timer.assert_called_once()
        self.node.drop_topo_node.assert_not_called()
        self.assertIs(self.node._track_timer, original_timer)
        self.assertEqual(self.node._run_vm.track.status, 'ERROR: already running')
        self.assertTrue(self.node._run_vm.track.running)

    def test_gateway_callback_preserves_row_id_and_advances_from_entry_to_middle(self) -> None:
        """A timer callback must retain row zero and mark only the first node as entry."""
        self.node.start_track('row', 0.5, 0, 'exit')
        callback = self.node._ros.create_timer.call_args.args[1]
        callback()
        callback()

        self.assertEqual([c.args for c in self.node.drop_topo_node.call_args_list], [
            ('ROW_1', 0, 'entry'), ('ROW_2', 0, 'middle'), ('ROW_3', 0, 'middle'),
        ])
        self.assertEqual(self.node._run_vm.track.status, 'recording  ROW_3  (#3)')
