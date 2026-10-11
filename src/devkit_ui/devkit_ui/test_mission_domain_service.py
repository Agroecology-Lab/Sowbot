"""Test MissionDomainService: step resolution, execution order, cancellation and tool publishing."""
# pylint: disable=protected-access
import sys
import threading
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

try:
    import std_msgs.msg  # noqa: F401  # pylint: disable=unused-import
except ImportError:  # ROS message packages not installed (e.g. plain unit-test environments)
    class _Msg:
        """Stand in for a std_msgs message with a data field."""
        data = None

    class Bool(_Msg):
        """Stand in for std_msgs/Bool."""

    class Float64(_Msg):
        """Stand in for std_msgs/Float64."""

    _msgs = ModuleType('std_msgs.msg')
    _msgs.Bool, _msgs.Float64 = Bool, Float64
    _pkg = ModuleType('std_msgs')
    _pkg.msg = _msgs
    sys.modules.setdefault('std_msgs', _pkg)
    sys.modules.setdefault('std_msgs.msg', _msgs)

# pylint: disable=wrong-import-position
from devkit_ui.domain_services import mission_domain_service as mds
from devkit_ui.domain_services.mission_domain_service import (
    ERROR,
    OK,
    WARN,
    MissionDomainService,
    MissionError,
    resolve_steps,
)


def node(name, row_id, role):
    """Return a topology node stand-in with row metadata."""
    return SimpleNamespace(name=name, meta={'row_id': row_id, 'row_role': role})


def doc(*nodes):
    """Return a topology document stand-in holding nodes."""
    return SimpleNamespace(nodes=list(nodes))


class TestResolveSteps(unittest.TestCase):
    def test_resolves_entry_and_exit(self) -> None:
        """Verify each queued row maps to its entry and exit node names."""
        topo = doc(node('A_1', 1, 'entry'), node('A_2', 1, 'exit'), node('B_1', '2', 'entry'),
                   node('B_2', '2', 'exit'), node('wp', None, ''))
        steps = resolve_steps(topo, [(2, 'drive', None), (1, 'drive', {'x': 1})])
        self.assertEqual(steps, [(2, 'B_1', 'B_2', 'drive', None),
                                 (1, 'A_1', 'A_2', 'drive', {'x': 1})])

    def test_no_map(self) -> None:
        """Verify a missing map raises MissionError."""
        with self.assertRaisesRegex(MissionError, 'no topology map'):
            resolve_steps(None, [(1, 'drive', None)])

    def test_missing_exit_reports_row(self) -> None:
        """Verify a row without an exit node is reported."""
        topo = doc(node('A_1', 1, 'entry'))
        with self.assertRaisesRegex(MissionError, r'\[1\]'):
            resolve_steps(topo, [(1, 'drive', None)])

    def test_bad_row_id_ignored(self) -> None:
        """Verify a non-numeric row_id is skipped rather than raising."""
        topo = doc(node('X', 'abc', 'entry'))
        with self.assertRaises(MissionError):
            resolve_steps(topo, [(1, 'drive', None)])


class TestMissionExecution(unittest.TestCase):
    def setUp(self) -> None:
        """Create a service whose tool publishing is recorded."""
        self.ros = Mock()
        self.svc = MissionDomainService(self.ros)
        self.svc.publish_tool_msgs = Mock()
        self.events: list = []

    def run_steps(self, steps, navigate, estop=lambda: False) -> None:
        """Run the executor synchronously and capture status events."""
        self.svc._running = True
        self.svc._execute(steps, navigate, lambda lvl, txt: self.events.append((lvl, txt)), estop)

    def test_success_visits_entry_then_exit_with_tool_around_traversal(self) -> None:
        """Verify order: entry transit, tool on, exit traversal, tool off, then completion."""
        order: list = []
        self.svc.publish_tool_msgs.side_effect = lambda a, p, enable: order.append(('tool', enable))
        self.run_steps([(1, 'E', 'X', 'drive', None)], lambda n: order.append(n) or True)
        self.assertEqual(order, ['E', ('tool', True), 'X', ('tool', False)])
        self.assertEqual(self.events[-1][0], OK)
        self.assertFalse(self.svc.running)

    def test_transit_failure_stops_without_engaging_tool(self) -> None:
        """Verify a failed transit stops the mission and never engages the tool."""
        self.run_steps([(1, 'E', 'X', 'drive', None)], lambda n: False)
        self.assertEqual(self.events[-1][0], ERROR)
        self.svc.publish_tool_msgs.assert_not_called()
        self.assertFalse(self.svc.running)

    def test_traversal_failure_still_disengages_tool(self) -> None:
        """Verify the tool is disengaged when the traversal fails or raises."""
        self.run_steps([(1, 'E', 'X', 'drive', None)], lambda n: n == 'E')
        self.assertEqual(self.svc.publish_tool_msgs.call_args_list[-1].kwargs, {'enable': False})
        self.svc.publish_tool_msgs.reset_mock()

        def boom(n):
            if n == 'X':
                raise RuntimeError('nav crashed')
            return True
        self.run_steps([(1, 'E', 'X', 'drive', None)], boom)
        self.assertEqual(self.svc.publish_tool_msgs.call_args_list[-1].kwargs, {'enable': False})
        self.assertEqual(self.events[-1][0], ERROR)
        self.assertFalse(self.svc.running)

    def test_estop_cancels_before_first_goal(self) -> None:
        """Verify an active soft e-stop cancels before any navigation."""
        navigate = Mock(return_value=True)
        self.run_steps([(1, 'E', 'X', 'drive', None)], navigate, estop=lambda: True)
        navigate.assert_not_called()
        self.assertEqual(self.events[-1], (WARN, 'Cancelled'))

    def test_cancel_between_legs(self) -> None:
        """Verify cancel() during transit stops before the traversal leg."""
        def navigate(_n):
            self.svc.cancel()
            return True
        self.run_steps([(1, 'E', 'X', 'drive', None)], navigate)
        self.svc.publish_tool_msgs.assert_not_called()
        self.assertEqual(self.events[-1], (WARN, 'Cancelled'))

    def test_start_status_failure_releases_slot_and_preserves_exception(self) -> None:
        """Verify a failed initial status callback permits a later mission start."""
        failure = RuntimeError('status callback failed')
        on_status = Mock(side_effect=failure)
        with patch.object(mds.threading, 'Thread') as worker:
            with self.assertRaises(RuntimeError) as raised:
                self.svc.start([], Mock(), on_status, lambda: False)
            self.assertIs(raised.exception, failure)
            self.assertFalse(self.svc.running)
            worker.assert_not_called()

            on_status.side_effect = None
            self.svc.start([], Mock(), on_status, lambda: False)
            worker.return_value.start.assert_called_once()
            self.assertTrue(self.svc.running)

    def test_thread_start_failure_releases_slot_and_preserves_exception(self) -> None:
        """Verify a failed worker start propagates unchanged and permits a retry."""
        failure = RuntimeError('thread start failed')
        with patch.object(mds.threading, 'Thread') as worker:
            worker.return_value.start.side_effect = failure
            with self.assertRaises(RuntimeError) as raised:
                self.svc.start([], Mock(), Mock(), lambda: False)
            self.assertIs(raised.exception, failure)
            self.assertFalse(self.svc.running)

            worker.return_value.start.side_effect = None
            self.svc.start([], Mock(), Mock(), lambda: False)
            self.assertEqual(worker.return_value.start.call_count, 2)
            self.assertTrue(self.svc.running)

    def test_second_start_rejected_while_running(self) -> None:
        """Verify start() raises while a mission is running and clears state after it ends."""
        release = threading.Event()
        done = threading.Event()

        def navigate(_n):
            release.wait(2)
            return True

        def on_status(level, _text):
            if level == OK:
                done.set()

        self.svc.start([(1, 'E', 'X', 'drive', None)], navigate, on_status, lambda: False)
        self.assertTrue(self.svc.running)
        with self.assertRaisesRegex(MissionError, 'already running'):
            self.svc.start([(1, 'E', 'X', 'drive', None)], navigate, on_status, lambda: False)
        release.set()
        self.assertTrue(done.wait(2))
        for _ in range(100):
            if not self.svc.running:
                break
            threading.Event().wait(0.01)
        self.assertFalse(self.svc.running)
        self.assertFalse(self.svc.cancel_requested)


class TestToolPublishers(unittest.TestCase):
    def setUp(self) -> None:
        """Create a service on a mock gateway."""
        self.ros = Mock()
        self.svc = MissionDomainService(self.ros)

    def test_bool_and_float_publishers_use_gateway_and_cache(self) -> None:
        """Verify Bool/Float64 publishers come from the gateway at depth 1 and are cached per type."""
        first = self.svc.get_tool_publisher('/t')
        self.assertIs(first, self.svc.get_tool_publisher('/t'))
        self.svc.get_tool_publisher('/t', is_float=True)
        self.assertEqual(self.ros.create_publisher.call_args_list[0].args, (mds.Bool, '/t', 1))
        self.assertEqual(self.ros.create_publisher.call_args_list[1].args, (mds.Float64, '/t', 1))
        self.assertEqual(self.ros.create_publisher.call_count, 2)

    def test_publish_tool_msgs_types_and_error_isolation(self) -> None:
        """Verify bool/float values publish with matching types and one bad value does not stop the rest."""
        pubs = {}

        def make(_t, topic, _q):
            pubs[topic] = Mock()
            return pubs[topic]
        self.ros.create_publisher.side_effect = make
        pairs = [('/a', True), ('/b', object()), ('/c', 2)]
        with patch.object(mds, 'action_ros_msgs', return_value=pairs):
            self.svc.publish_tool_msgs('x', None, True)
        self.assertIs(pubs['/a'].publish.call_args.args[0].data, True)
        self.assertEqual(pubs['/c'].publish.call_args.args[0].data, 2.0)
        self.assertNotIn('/b', pubs)
        self.ros.get_logger.return_value.warn.assert_called_once()


if __name__ == '__main__':
    unittest.main()
