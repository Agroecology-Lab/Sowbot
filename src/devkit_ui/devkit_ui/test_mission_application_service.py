"""Test starting and cancelling a mission through MissionApplicationService."""
# pylint: disable=protected-access,attribute-defined-outside-init
import unittest
from unittest.mock import Mock, call

from devkit_ui import test_mission_domain_service as fixtures
from devkit_ui.application_services.mission_application_service import MissionApplicationService

doc, mds, node = fixtures.doc, fixtures.mds, fixtures.node


class TestMissionRun(unittest.TestCase):
    def setUp(self) -> None:
        """Build an idle service over a complete row, available navigation and no estop."""
        self.executor = Mock(running=False, cancel_requested=False)
        self.topology = Mock()
        self.topology.get_doc.return_value = doc(node('E', 3, 'entry'), node('X', 3, 'exit'))
        self.navigation = Mock(available=True)
        self.drive = Mock(soft_estop_active=False)
        self.service = MissionApplicationService(
            Mock(), self.executor, self.topology, self.navigation, self.drive)
        self.on_status, self.on_nav = Mock(), Mock()
        self.queue = [(3, 'weed', {'depth': 0.4})]

    def test_invalid_missions_report_an_error_without_starting_the_worker(self) -> None:
        """Verify each rejected start reports one error and never reaches the executor."""
        cases = [
            ([], False, True, doc(node('E', 3, 'entry'), node('X', 3, 'exit')), 'queue is empty'),
            (self.queue, True, True, doc(node('E', 3, 'entry'), node('X', 3, 'exit')),
             'mission already running'),
            (self.queue, False, False, doc(node('E', 3, 'entry'), node('X', 3, 'exit')),
             'action client unavailable'),
            (self.queue, False, True, None, 'no topology map loaded'),
            (self.queue, False, True, doc(node('E', 3, 'entry')),
             'incomplete row nodes for row(s) [3]'),
        ]
        for queue, running, available, topo, error in cases:
            with self.subTest(error=error):
                self.on_status.reset_mock()
                self.executor.running = running
                self.navigation.available = available
                self.topology.get_doc.return_value = topo

                self.service.start(queue, self.on_status, self.on_nav)

                self.on_status.assert_called_once_with(mds.ERROR, f'ERROR: {error}')
                self.executor.start.assert_not_called()

    def test_valid_mission_passes_resolved_steps_and_the_live_confirmed_estop(self) -> None:
        """Verify the executor gets resolved steps and an estop reader that follows the driver."""
        self.service.start(self.queue, self.on_status, self.on_nav)

        self.executor.start.assert_called_once()
        steps, _navigate, on_status, estop = self.executor.start.call_args.args
        self.assertEqual(steps, [(3, 'E', 'X', 'weed', {'depth': 0.4})])
        self.assertIs(on_status, self.on_status)
        self.assertFalse(estop())
        self.drive.soft_estop_active = True
        self.assertTrue(estop())

    def test_a_start_race_is_reported_as_a_mission_error(self) -> None:
        """Verify the executor refusing a second mission is shown as an error, not raised."""
        self.executor.start.side_effect = mds.MissionError('mission already running')

        self.service.start(self.queue, self.on_status, self.on_nav)

        self.on_status.assert_called_once_with(mds.ERROR, 'ERROR: mission already running')

    def test_unexpected_start_errors_propagate(self) -> None:
        """Verify a programming error is not turned into a status line."""
        self.executor.start.side_effect = OSError('no thread')

        with self.assertRaises(OSError):
            self.service.start(self.queue, self.on_status, self.on_nav)

        self.on_status.assert_not_called()


class TestMissionNavigation(unittest.TestCase):
    def setUp(self) -> None:
        """Start a mission and capture the navigate callback the executor receives."""
        self.executor = Mock(running=False, cancel_requested=False)
        self.topology = Mock()
        self.topology.get_doc.return_value = doc(node('E', 3, 'entry'), node('X', 3, 'exit'))
        self.navigation = Mock(available=True)
        self.drive = Mock(soft_estop_active=False)
        self.service = MissionApplicationService(
            Mock(), self.executor, self.topology, self.navigation, self.drive)
        self.on_nav = Mock()
        self.service.start([(3, 'weed', None)], Mock(), self.on_nav)
        self.navigate = self.executor.start.call_args.args[1]

    def test_navigation_forwards_the_timeout_and_the_progress_callback(self) -> None:
        """Verify the result of the wait is returned and progress reaches the Run tab."""
        for result in (True, False):
            with self.subTest(result=result):
                self.navigation.navigate_and_wait.return_value = result

                self.assertIs(self.navigate('X'), result)

                target, timeout, _abort, on_update = (
                    self.navigation.navigate_and_wait.call_args.args)
                self.assertEqual((target, timeout), ('X', 300.0))
                self.assertIs(on_update, self.on_nav)

    def test_navigation_aborts_on_a_mission_cancel_or_a_confirmed_estop(self) -> None:
        """Verify the abort check reads both live flags on every call."""
        self.navigate('X')
        abort = self.navigation.navigate_and_wait.call_args.args[2]
        for cancel, estop in [(False, False), (True, False), (False, True), (True, True)]:
            with self.subTest(cancel=cancel, estop=estop):
                self.executor.cancel_requested = cancel
                self.drive.soft_estop_active = estop

                self.assertIs(abort(), cancel or estop)

    def test_cancel_signals_the_worker_before_cancelling_the_active_goal(self) -> None:
        """Verify no new goal can start between the goal cancel and the worker's cancel flag."""
        order = Mock()
        order.attach_mock(self.executor.cancel, 'worker_cancel')
        order.attach_mock(self.navigation.cancel, 'navigation_cancel')

        self.service.cancel()

        self.assertEqual(order.mock_calls, [call.worker_cancel(), call.navigation_cancel()])

    def test_cancel_with_no_mission_running_leaves_a_manual_goal_alone(self) -> None:
        """Verify an idle cancel does not reach navigation, which may be running a manual goal."""
        self.executor.cancel.return_value = False

        self.service.cancel()

        self.navigation.cancel.assert_not_called()


if __name__ == '__main__':
    unittest.main()
