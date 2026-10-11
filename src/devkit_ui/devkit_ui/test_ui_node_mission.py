"""Test mission UI delegation and gateway subscription with isolated ROS/UI boundaries."""
# pylint: disable=exec-used,no-member,protected-access,attribute-defined-outside-init
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

from devkit_ui.test_mission_domain_service import doc, mds, node


def load_mission_harness():
    """Compile the real constructor and mission methods, replacing external collaborators."""
    source = Path(__file__).with_name('ui_node.py')
    tree = ast.parse(source.read_text(encoding='utf-8'))
    node_class = next(n for n in tree.body
                      if isinstance(n, ast.ClassDef) and n.name == 'NiceGuiNode')
    names = {'__init__', '_run_mission', '_send_goal_sync', 'cancel_mission'}
    members = [n for n in node_class.body if isinstance(n, ast.FunctionDef) and n.name in names]
    harness = ast.ClassDef(name='Harness', bases=[ast.Name(id='Node', ctx=ast.Load())],
                          keywords=[], body=members, decorator_list=[])
    colours = next(n for n in tree.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'MISSION_STATUS_COLOURS'
                           for t in n.targets))

    class FakeNode:
        """Provide only the node boundary used during initialization."""
        def __init__(self, _name):
            self.declare_parameter = Mock()
            self.get_parameter = Mock(return_value=SimpleNamespace(value=False))
            self.create_subscription = Mock(side_effect=AssertionError('gateway bypassed'))

    namespace = {name: Mock(name=name) for name in (
        'RosGateway', 'DriveDomainService', 'TopologyDomainService', 'RobotBrainDomainService',
        'RowDiscoveryDomainService', 'NavigationDomainService', 'TelemetryDomainService',
        'DriveApplicationService', 'TopologyApplicationService', 'RobotBrainApplicationService',
        'RowDiscoveryApplicationService', 'NavigationApplicationService',
        'TelemetryApplicationService',
        'GlobalViewModel', 'RunViewModel', 'TopologyViewModel', 'TelemetryViewModel',
        'ObstacleManager', 'MissionStore', 'MissionDomainService', 'ui', 'String',
    )}
    namespace.update({name: getattr(mds, name) for name in (
        'INFO', 'PROGRESS', 'WARN', 'ERROR', 'OK', 'NAV_TIMEOUT_SEC',
        'MissionError', 'resolve_steps',
    )})
    namespace.update(Node=FakeNode, NODE_NAME='test_ui', FIELD27_CENTER=(0.0, 0.0),
                     ROW_ACTION='row', QoSProfile=SimpleNamespace,
                     ReliabilityPolicy=SimpleNamespace(BEST_EFFORT='best_effort'))
    module = ast.fix_missing_locations(ast.Module(body=[colours, harness], type_ignores=[]))
    exec(compile(module, source, 'exec'), namespace)
    return namespace['Harness'](), namespace


class TestUiNodeMission(unittest.TestCase):
    def setUp(self):
        self.node, self.namespace = load_mission_harness()
        self.service = self.node._mission_service
        self.service.running = False
        self.service.cancel_requested = False
        self.node._global_vm.soft_estop_active = False
        self.node._run_vm.navigation_available = True
        self.node._topo_doc = doc(node('E', 3, 'entry'), node('X', 3, 'exit'))
        self.node.cancel_nav_goal = Mock()
        self.label = Mock()
        self.queue = [(3, 'weed', {'depth': 0.4})]

    def test_constructor_routes_current_node_subscription_and_mission_service_through_gateway(self):
        gateway = self.node._ros
        gateway.create_subscription.assert_called_once()
        message_type, topic, callback, qos = gateway.create_subscription.call_args.args
        self.assertIs(message_type, self.namespace['String'])
        self.assertEqual(topic, '/current_node')
        self.assertEqual((qos.depth, qos.reliability), (1, 'best_effort'))
        self.node.create_subscription.assert_not_called()
        self.namespace['MissionDomainService'].assert_called_once_with(gateway)
        callback(SimpleNamespace(data='E'))
        callback(SimpleNamespace(data='X'))
        self.assertEqual(self.node._topo_vm.set_current_node.call_args_list, [call('E'), call('X')])

    def test_invalid_missions_report_error_without_starting_worker(self):
        cases = [
            ([], False, True, self.node._topo_doc, 'queue is empty'),
            (self.queue, True, True, self.node._topo_doc, 'mission already running'),
            (self.queue, False, False, self.node._topo_doc, 'action client unavailable'),
            (self.queue, False, True, None, 'no topology map loaded'),
            (self.queue, False, True, doc(node('E', 3, 'entry')),
             'incomplete row nodes for row(s) [3]'),
        ]
        for queue, running, available, topo, error in cases:
            with self.subTest(error=error):
                self.label.reset_mock()
                self.service.running = running
                self.node._run_vm.navigation_available = available
                self.node._topo_doc = topo
                self.node._run_mission(queue, self.label)
                self.label.set_text.assert_called_once_with(f'ERROR: {error}')
                self.label.style.assert_called_once_with('color:#cf222e')
                self.service.start.assert_not_called()

    def test_valid_mission_passes_resolved_steps_and_live_estop_callback(self):
        self.node._run_mission(self.queue, self.label)
        self.service.start.assert_called_once()
        steps, navigate, _show, estop = self.service.start.call_args.args
        self.assertEqual(steps, [(3, 'E', 'X', 'weed', {'depth': 0.4})])
        self.assertEqual(navigate, self.node._send_goal_sync)
        self.assertFalse(estop())
        self.node._global_vm.soft_estop_active = True
        self.assertTrue(estop())

    def test_service_status_levels_update_label_text_and_colour(self):
        self.node._run_mission(self.queue, self.label)
        show = self.service.start.call_args.args[2]
        for level, colour in [('info', '#57606a'), ('progress', '#0969da'),
                              ('warn', '#9a6700'), ('error', '#cf222e'), ('ok', '#1a7f37')]:
            with self.subTest(level=level):
                self.label.reset_mock()
                show(level, 'mission update')
                self.label.set_text.assert_called_once_with('mission update')
                self.label.style.assert_called_once_with(f'color:{colour}')

    def test_start_race_is_reported_as_mission_error(self):
        self.service.start.side_effect = mds.MissionError('mission already running')
        self.node._run_mission(self.queue, self.label)
        self.label.set_text.assert_called_once_with('ERROR: mission already running')
        self.label.style.assert_called_once_with('color:#cf222e')

    def test_sync_navigation_uses_timeout_and_observes_current_cancel_and_estop(self):
        for result in (True, False):
            with self.subTest(result=result):
                self.node._run_vm.navigate_and_wait.return_value = result
                self.assertIs(self.node._send_goal_sync('X'), result)
                target, timeout, cancelled = self.node._run_vm.navigate_and_wait.call_args.args
                self.assertEqual((target, timeout), ('X', 300.0))
                for cancel, estop in [(False, False), (True, False), (False, True), (True, True)]:
                    self.service.cancel_requested = cancel
                    self.node._global_vm.soft_estop_active = estop
                    self.assertIs(cancelled(), cancel or estop)

    def test_cancel_signals_worker_before_cancelling_active_navigation(self):
        order = Mock()
        order.attach_mock(self.service.cancel, 'worker_cancel')
        order.attach_mock(self.node.cancel_nav_goal, 'navigation_cancel')
        self.node.cancel_mission()
        self.assertEqual(order.mock_calls, [call.worker_cancel(), call.navigation_cancel()])
