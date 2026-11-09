"""Test starting and cancelling a mission through MissionApplicationService."""
# pylint: disable=protected-access,attribute-defined-outside-init
import sqlite3
import unittest
from unittest.mock import Mock, call, patch

import test_mission_domain_service as fixtures
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
        steps, _navigate, on_status, estop, on_result, on_step_done = self.executor.start.call_args.args
        self.assertEqual(steps, [(3, 'E', 'X', 'weed', {'depth': 0.4})])
        self.assertIs(on_status, self.on_status)
        self.assertEqual((on_result, on_step_done), (None, None))
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


class TestRunSaved(unittest.TestCase):
    def setUp(self) -> None:
        """Build a service over a store holding one saved mission for row 3."""
        self.executor = Mock(running=False, cancel_requested=False)
        self.topology = Mock()
        self.topology.get_doc.return_value = doc(node('E', 3, 'entry'), node('X', 3, 'exit'))
        self.store = Mock()
        self.store.find.return_value = {
            'id': 'MISSION_1', 'rows': ['E'], 'action': 'weed', 'action_params': {'depth': 0.4},
            'active': False}
        self.service = MissionApplicationService(
            self.store, self.executor, self.topology, Mock(available=True),
            Mock(soft_estop_active=False))
        self.on_status = Mock()

    def test_a_saved_mission_starts_with_its_resolved_steps(self) -> None:
        """Verify entry names become steps, and an inactive mission still runs."""
        self.service.run_saved('MISSION_1', self.on_status)

        self.store.find.assert_called_once_with('MISSION_1')
        self.executor.start.assert_called_once()
        self.assertEqual(self.executor.start.call_args.args[0],
                         [(3, 'E', 'X', 'weed', {'depth': 0.4})])
        self.assertIs(self.executor.start.call_args.args[2], self.on_status)

    def test_an_unknown_mission_reports_an_error_without_starting(self) -> None:
        """Verify a missing mission is an ERROR status and never reaches the executor."""
        self.store.find.return_value = None

        self.service.run_saved('MISSION_9', self.on_status)

        self.on_status.assert_called_once_with(mds.ERROR, "ERROR: 'MISSION_9' not found")
        self.executor.start.assert_not_called()

    def test_saved_fallback_row_starts_at_the_live_entry(self) -> None:
        """Resolve saved fallback rows after the live map gains a named entry."""
        self.store.find.return_value = {**self.store.find.return_value, 'rows': ['ROW_3_IN']}

        self.service.run_saved('MISSION_1', self.on_status)

        self.assertEqual(self.executor.start.call_args.args[0],
                         [(3, 'E', 'X', 'weed', {'depth': 0.4})])

    def test_ambiguous_saved_row_does_not_start(self) -> None:
        """Report duplicate entries before starting a saved mission worker."""
        self.topology.get_doc.return_value.nodes.append(node('another_entry', 3, 'entry'))

        self.service.run_saved('MISSION_1', self.on_status)

        self.executor.start.assert_not_called()
        self.on_status.assert_called_once_with(mds.ERROR, 'ERROR: multiple entry nodes for row 3')

    def test_a_row_missing_from_the_map_is_reported_by_start(self) -> None:
        """Verify an entry name the live map lacks fails like any other start error."""
        self.store.find.return_value = {**self.store.find.return_value, 'rows': ['GONE']}

        self.service.run_saved('MISSION_1', self.on_status)

        self.on_status.assert_called_once_with(mds.ERROR, "ERROR: unknown entry node(s) ['GONE']")
        self.executor.start.assert_not_called()


class TestRunSavedRecordsTheOutcome(unittest.TestCase):
    def setUp(self) -> None:
        """Build a service over a mock store holding one saved mission."""
        self.executor = Mock(running=False, cancel_requested=False)
        self.topology = Mock()
        self.topology.get_doc.return_value = doc(node('E', 3, 'entry'), node('X', 3, 'exit'))
        self.store = Mock()
        self.mission = {'id': 'MISSION_1', 'rows': ['E'], 'action': 'weed', 'action_params': {},
                        'state': 'ended', 'steps_done': 4, 'stage': 'row', 'reason': 'nav',
                        'started_at': 'then', 'ended_at': 'later'}
        self.store.find.return_value = self.mission
        self.service = MissionApplicationService(
            self.store, self.executor, self.topology, Mock(available=True),
            Mock(soft_estop_active=False))
        self.on_status = Mock()
        self.real_executor = mds.MissionDomainService(Mock())
        self.executor.start.side_effect = self.real_executor.start
        thread_patch = patch.object(mds.threading.Thread, 'start')
        self.thread_start = thread_patch.start()
        self.addCleanup(thread_patch.stop)

    def callbacks(self):
        """Return the on_result and on_step_done callbacks the executor was given."""
        args = self.executor.start.call_args.args
        return args[4], args[5]

    def test_it_marks_the_mission_running_and_clears_the_old_result(self) -> None:
        self.assertTrue(self.service.run_saved('MISSION_1', self.on_status))

        kwargs = self.store.record_result.call_args.kwargs
        self.assertEqual(kwargs['state'], 'running')
        self.assertEqual((kwargs['steps_done'], kwargs['stage'], kwargs['reason']), (0, None, None))
        self.assertIsNone(kwargs['ended_at'])
        self.assertTrue(kwargs['started_at'])

    def test_each_finished_step_is_stored(self) -> None:
        self.service.run_saved('MISSION_1', self.on_status)
        _, on_step_done = self.callbacks()

        on_step_done(2)

        self.store.record_result.assert_called_with('MISSION_1', steps_done=2)

    def test_the_result_is_stored_and_the_schedule_rearmed(self) -> None:
        self.service.run_saved('MISSION_1', self.on_status)
        on_result, _ = self.callbacks()

        on_result(mds.MissionResult(False, 1, 'row', 'timeout'))

        kwargs = self.store.record_result.call_args.kwargs
        self.assertEqual(
            (kwargs['state'], kwargs['steps_done'], kwargs['stage'], kwargs['reason']),
            ('ended', 1, 'row', 'timeout'))
        self.assertTrue(kwargs['ended_at'])
        self.store.record_run.assert_called_once_with('MISSION_1', False)
        self.store.set_active.assert_not_called()

    def test_a_successful_run_records_success(self) -> None:
        self.service.run_saved('MISSION_1', self.on_status)
        on_result, _ = self.callbacks()

        on_result(mds.MissionResult(True, 2))

        self.store.record_run.assert_called_once_with('MISSION_1', True)

    def test_a_remote_mission_goes_inactive_when_it_ends_whatever_the_result(self) -> None:
        self.mission['source'] = 'remote'
        self.service.run_saved('MISSION_1', self.on_status)
        on_result, _ = self.callbacks()

        on_result(mds.MissionResult(False, 0, 'transit', 'cancel'))

        self.store.set_active.assert_called_once_with('MISSION_1', False)

    def test_a_mission_that_fails_to_start_keeps_its_previous_result(self) -> None:
        self.topology.get_doc.return_value = doc()

        self.assertFalse(self.service.run_saved('MISSION_1', self.on_status))

        self.executor.start.assert_not_called()
        self.store.record_result.assert_not_called()

    def test_busy_executor_never_changes_results(self) -> None:
        self.real_executor._running = True
        self.assertFalse(self.service.run_saved('MISSION_1', self.on_status))
        self.store.record_result.assert_not_called()

    def test_thread_start_failure_restores_result_and_original_error(self) -> None:
        error = OSError('no thread')
        self.thread_start.side_effect = error
        with self.assertRaises(OSError) as raised:
            self.service.run_saved('MISSION_1', self.on_status)
        self.assertIs(raised.exception, error)
        self.assertEqual(self.store.record_result.call_args.kwargs, {
            key: self.mission[key] for key in
            ('state', 'steps_done', 'stage', 'reason', 'started_at', 'ended_at')})
        self.assertFalse(self.real_executor.running)

    def test_fast_worker_result_is_never_restored(self) -> None:
        def finish():
            on_result, _ = self.callbacks()
            on_result(mds.MissionResult(True, 1))
        self.thread_start.side_effect = finish
        self.assertTrue(self.service.run_saved('MISSION_1', self.on_status))
        self.assertEqual(self.store.record_result.call_args.kwargs['state'], 'ended')
        self.assertEqual(self.store.record_result.call_args.kwargs['steps_done'], 1)

    def test_status_failure_restores_result(self) -> None:
        self.on_status.side_effect = RuntimeError('status failed')
        with self.assertRaisesRegex(RuntimeError, 'status failed'):
            self.service.run_saved('MISSION_1', self.on_status)
        self.assertEqual(self.store.record_result.call_args.kwargs['state'], 'ended')
        self.thread_start.assert_not_called()

    def test_initialization_failure_does_not_start_worker(self) -> None:
        self.store.record_result.side_effect = [sqlite3.OperationalError('locked'), True]
        with self.assertRaises(sqlite3.OperationalError):
            self.service.run_saved('MISSION_1', self.on_status)
        self.thread_start.assert_not_called()
        self.assertEqual(self.store.record_result.call_args.kwargs['steps_done'], 4)

    def test_completion_retries_the_whole_operation(self) -> None:
        self.mission['source'] = 'remote'
        self.service.run_saved('MISSION_1', self.on_status)
        on_result, _ = self.callbacks()
        self.store.set_active.side_effect = [sqlite3.OperationalError('locked'), None]
        with patch('devkit_ui.application_services.mission_application_service.time.sleep'):
            on_result(mds.MissionResult(False, 1, 'row', 'cancel'))
        self.assertEqual(self.store.record_run.call_count, 2)
        self.assertEqual(self.store.set_active.call_count, 2)
        self.assertEqual(self.store.record_result.call_args.kwargs['state'], 'ended')

    def test_persistent_completion_failure_is_reported(self) -> None:
        self.service.run_saved('MISSION_1', self.on_status)
        on_result, _ = self.callbacks()
        self.store.record_result.side_effect = sqlite3.OperationalError('locked')
        with patch('devkit_ui.application_services.mission_application_service.time.sleep'):
            with self.assertRaises(sqlite3.OperationalError):
                on_result(mds.MissionResult(True, 1))
        self.assertIn('completion could not be saved', self.on_status.call_args.args[1])
        self.store.set_status.assert_called_once()

    def test_an_unknown_mission_records_nothing(self) -> None:
        self.store.find.return_value = None

        self.assertFalse(self.service.run_saved('MISSION_9', self.on_status))

        self.store.record_result.assert_not_called()


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
        """Verify the way the wait ended is returned and progress reaches the Run tab."""
        for result in ('arrived', 'nav', 'timeout', 'cancel'):
            with self.subTest(result=result):
                self.navigation.navigate_and_wait_result.return_value = result

                self.assertEqual(self.navigate('X'), result)

                target, timeout, _abort, on_update = (
                    self.navigation.navigate_and_wait_result.call_args.args)
                self.assertEqual((target, timeout), ('X', 300.0))
                self.assertIs(on_update, self.on_nav)

    def test_a_stop_from_the_soft_estop_is_reported_as_estop_not_cancel(self) -> None:
        """Verify an aborted goal is 'estop' only when the estop, not a mission cancel, was set."""
        self.navigation.navigate_and_wait_result.return_value = 'cancel'
        for cancel, estop, expected in [(True, False, 'cancel'), (False, True, 'estop'),
                                        (True, True, 'cancel'), (False, False, 'cancel')]:
            with self.subTest(cancel=cancel, estop=estop):
                self.executor.cancel_requested = cancel
                self.drive.soft_estop_active = estop

                self.assertEqual(self.navigate('X'), expected)

    def test_navigation_aborts_on_a_mission_cancel_or_a_confirmed_estop(self) -> None:
        """Verify the abort check reads both live flags on every call."""
        self.navigate('X')
        abort = self.navigation.navigate_and_wait_result.call_args.args[2]
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
