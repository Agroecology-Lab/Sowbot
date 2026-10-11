"""Verify NiceGuiNode creates tool publishers and the track timer through RosGateway."""
# pylint: disable=exec-used,no-member,protected-access,attribute-defined-outside-init
import ast
import re
import unittest
from pathlib import Path
from unittest.mock import Mock

from devkit_ui.models import TopoDoc
from devkit_ui.view_models.run_view_model import RunViewModel


class FakeBool:
    """Stand in for std_msgs/Bool so the test needs no ROS message packages."""


class FakeFloat64:
    """Stand in for std_msgs/Float64 so the test needs no ROS message packages."""


def load_gateway_harness():
    """Load the real start_track and _get_tool_publisher methods from ui_node.py."""
    source = Path(__file__).with_name('ui_node.py')
    tree = ast.parse(source.read_text(encoding='utf-8'))
    node_class = next(node for node in tree.body
                      if isinstance(node, ast.ClassDef) and node.name == 'NiceGuiNode')
    names = {'start_track', '_get_tool_publisher'}
    members = [node for node in node_class.body
               if isinstance(node, ast.FunctionDef) and node.name in names]
    harness = ast.ClassDef(name='Harness', bases=[], keywords=[], body=members, decorator_list=[])
    namespace = {'re': re, 'Bool': FakeBool, 'Float64': FakeFloat64}
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
        self.node._tool_publishers = {}
        self.node._track_timer = None
        self.node._topo_doc = TopoDoc(name='field.yaml')
        self.node._run_vm = RunViewModel(Mock())
        self.node.drop_topo_node = Mock()

    def tearDown(self) -> None:
        """Check that nothing bypassed the gateway to reach the node directly."""
        self.node.create_publisher.assert_not_called()
        self.node.create_timer.assert_not_called()

    def test_bool_tool_publisher_uses_gateway(self) -> None:
        """Verify a Bool tool publisher is created on the gateway with depth 1."""
        publisher = self.node._get_tool_publisher('/tool/enable')

        self.node._ros.create_publisher.assert_called_once_with(FakeBool, '/tool/enable', 1)
        self.assertIs(publisher, self.node._ros.create_publisher.return_value)

    def test_float_tool_publisher_uses_gateway(self) -> None:
        """Verify a Float64 tool publisher is created on the gateway with depth 1."""
        publisher = self.node._get_tool_publisher('/tool/speed', is_float=True)

        self.node._ros.create_publisher.assert_called_once_with(FakeFloat64, '/tool/speed', 1)
        self.assertIs(publisher, self.node._ros.create_publisher.return_value)

    def test_tool_publisher_is_cached_per_topic_and_type(self) -> None:
        """Verify repeat requests reuse a publisher, while the same topic with another type does not."""
        first = self.node._get_tool_publisher('/tool/x')
        second = self.node._get_tool_publisher('/tool/x')
        self.node._get_tool_publisher('/tool/x', is_float=True)

        self.assertIs(first, second)
        self.assertEqual(self.node._ros.create_publisher.call_count, 2)

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
