"""System tool service: process control with process creation and package lookup isolated."""

# pylint: disable=protected-access
import os
import subprocess
import sys
import threading
import unittest
from types import ModuleType
from unittest.mock import Mock, call, patch

from devkit_ui import process_registry
from devkit_ui.constants import TOOL_EXPLORER, TOOL_GRAPHER, TOOL_MEDKIT, TOOL_RVIZ

packages = ModuleType('ament_index_python.packages')
packages.PackageNotFoundError = type('PackageNotFoundError', (LookupError,), {})
packages.get_package_share_directory = Mock()
with patch.dict(sys.modules, {'ament_index_python.packages': packages}):
    from devkit_ui.application_services import system_application_service as sas
    from devkit_ui.application_services.system_application_service import (
        SystemApplicationService,
        ToolState,
    )


REAL_REAP = SystemApplicationService._reap_rviz_display


def make_process(pid: int = 101):
    """Return a running process double."""
    return Mock(spec=['pid', 'poll', 'terminate', 'wait', 'kill', 'send_signal'],
                pid=pid, poll=Mock(return_value=None))


def handle(tool: str) -> list:
    """Return the shared handle for tool."""
    return process_registry.shared(tool, lambda: [None])


class ServiceTestCase(unittest.TestCase):
    """Isolate the registry and every external effect of the service."""

    def setUp(self):
        self.enterContext(patch.dict(process_registry._TOOLS, clear=True))
        self.logged = self.enterContext(patch.object(sas, 'spawn_logged', return_value=make_process()))
        self.quiet = self.enterContext(patch.object(sas, 'spawn_quiet', return_value=make_process(137)))
        self.vnc = self.enterContext(patch.object(sas, 'start_vnc_stack'))
        self.makedirs = self.enterContext(patch.object(sas.os, 'makedirs'))
        self.package = self.enterContext(
            patch.object(sas, 'get_package_share_directory', return_value='/ros/topo'))
        self.kill_group = self.enterContext(patch.object(sas, 'kill_group'))
        self.stop_all = self.enterContext(patch.object(sas, 'stop_all'))
        self.interrupt = self.enterContext(patch.object(sas, 'interrupt_stop'))
        self.reaper = self.enterContext(
            patch.object(SystemApplicationService, '_reap_rviz_display'))
        self.service = SystemApplicationService()


class TestStartTools(ServiceTestCase):
    def test_explorer_starts_once_and_is_shared_with_other_sessions(self):
        self.assertEqual(self.service.start(TOOL_EXPLORER),
                         ToolState('started', 101, '/tmp/ros2graph_explorer.log'))
        self.logged.assert_called_once_with(
            ['ros2', 'run', 'ros2graph_explorer', 'ros2graph_explorer'], '/tmp/ros2graph_explorer.log')
        # A second browser session has its own service object but the same registry.
        other = SystemApplicationService()
        self.assertEqual(other.start(TOOL_EXPLORER).kind, 'already_running')
        self.assertEqual(other.status(TOOL_EXPLORER), ToolState('running', 101, '/tmp/ros2graph_explorer.log'))
        self.logged.assert_called_once()

    def test_grapher_serves_only_a_private_output_directory(self):
        state = self.service.start(TOOL_GRAPHER)
        self.assertEqual(state, ToolState('started', 101, '/tmp/ros2grapher.log'))
        self.makedirs.assert_called_once_with('/tmp/ros2grapher', exist_ok=True)
        self.logged.assert_called_once_with(
            ['ros2grapher', '/workspace', '-o', '/tmp/ros2grapher/index.html', '--port', '8888'],
            '/tmp/ros2grapher.log', cwd='/tmp/ros2grapher')

    def test_failed_directory_creation_never_spawns_grapher(self):
        self.makedirs.side_effect = PermissionError('private directory denied')
        self.assertEqual(self.service.start(TOOL_GRAPHER),
                         ToolState('error', detail='private directory denied'))
        self.logged.assert_not_called()
        self.assertEqual(handle(TOOL_GRAPHER), [None])

    def test_rviz_starts_display_stack_before_rviz_with_topology_configuration(self):
        order = Mock()
        order.attach_mock(self.vnc, 'vnc')
        order.attach_mock(self.logged, 'spawn')
        state = self.service.start(TOOL_RVIZ)
        self.assertEqual(state, ToolState('started', 101, '/tmp/rviz.log'))
        self.package.assert_called_once_with('topological_navigation')
        daemons = process_registry.shared('rviz_daemons', list)
        self.assertEqual([c[0] for c in order.mock_calls], ['vnc', 'spawn'])
        self.vnc.assert_called_once_with(':98', 5901, 6081, daemons)
        self.assertEqual(
            self.logged.call_args.args,
            (['ros2', 'run', 'rviz2', 'rviz2', '-d', '/ros/topo/rviz/topological_navigation.rviz'],
             '/tmp/rviz.log'))
        self.assertEqual(self.logged.call_args.kwargs['env']['DISPLAY'], ':98')

    def test_rviz_without_topology_package_launches_without_configuration(self):
        self.package.side_effect = sas.PackageNotFoundError('not installed')
        state = self.service.start(TOOL_RVIZ)
        self.assertEqual(self.logged.call_args.args[0], ['ros2', 'run', 'rviz2', 'rviz2'])
        self.assertEqual(state.detail, 'no topo config found')

    def test_rviz_display_failure_spawns_nothing_and_leaves_handle_empty(self):
        self.vnc.side_effect = OSError('no display')
        self.assertEqual(self.service.start(TOOL_RVIZ), ToolState('error', detail='no display'))
        self.logged.assert_not_called()
        self.assertEqual(handle(TOOL_RVIZ), [None])

    def test_spawn_failures_are_returned_as_errors_without_registering_a_process(self):
        self.logged.side_effect = OSError('missing executable')
        for tool in (TOOL_EXPLORER, TOOL_GRAPHER, TOOL_RVIZ):
            with self.subTest(tool=tool):
                self.assertEqual(self.service.start(tool),
                                 ToolState('error', detail='missing executable'))
                self.assertEqual(handle(tool), [None])


class TestMedkit(ServiceTestCase):
    def test_start_launches_gateway_on_loopback_and_reports_pid(self):
        self.assertEqual(self.service.start(TOOL_MEDKIT), ToolState('started', 137))
        self.quiet.assert_called_once_with(
            ['ros2', 'launch', 'ros2_medkit_gateway', 'bringup.launch.py', 'server_host:=127.0.0.1'])
        self.logged.assert_not_called()

    def test_start_while_running_does_not_launch_again(self):
        self.service.start(TOOL_MEDKIT)
        self.assertEqual(self.service.start(TOOL_MEDKIT), ToolState('already_running', 137))
        self.quiet.assert_called_once()
        self.kill_group.assert_not_called()
        self.interrupt.assert_not_called()

    def test_start_replaces_exited_process_regardless_of_exit_status(self):
        for returncode in (0, 1, -15):
            with self.subTest(returncode=returncode), patch.dict(process_registry._TOOLS, clear=True):
                self.quiet.reset_mock()
                old, new = make_process(100), make_process(200)
                self.quiet.side_effect = [old, new]
                self.service.start(TOOL_MEDKIT)
                old.poll.return_value = returncode
                self.assertEqual(self.service.start(TOOL_MEDKIT), ToolState('started', 200))
                self.assertEqual(self.quiet.call_count, 2)
                self.assertIs(handle(TOOL_MEDKIT)[0], new)

    def test_launch_errors_are_returned_and_allow_retry(self):
        for error in (FileNotFoundError('ros2 missing'), PermissionError('access denied'),
                      OSError('process limit reached')):
            with self.subTest(error=error):
                self.quiet.side_effect = [error, make_process(201)]
                self.assertEqual(self.service.start(TOOL_MEDKIT), ToolState('error', detail=str(error)))
                self.assertEqual(self.service.start(TOOL_MEDKIT).kind, 'started')
                self.service.stop(TOOL_MEDKIT)

    def test_failed_restart_keeps_previous_process_tracked(self):
        first = make_process(100)
        self.quiet.return_value = first
        self.service.start(TOOL_MEDKIT)
        first.poll.return_value = 1
        self.quiet.side_effect = OSError('cannot launch')
        self.assertEqual(self.service.start(TOOL_MEDKIT).kind, 'error')
        self.assertIs(handle(TOOL_MEDKIT)[0], first)

    def test_stop_interrupts_the_launch_leader_and_never_uses_group_termination(self):
        self.service.start(TOOL_MEDKIT)
        self.assertEqual(self.service.stop(TOOL_MEDKIT), ToolState('stopped'))
        self.interrupt.assert_called_once_with(self.quiet.return_value, 15, 5, 5)
        self.kill_group.assert_not_called()
        self.assertEqual(handle(TOOL_MEDKIT), [None])

    def test_stop_without_start_is_safe_and_repeatable(self):
        self.assertEqual(self.service.stop(TOOL_MEDKIT), ToolState('stopped'))
        self.assertEqual(self.service.stop(TOOL_MEDKIT), ToolState('stopped'))
        self.interrupt.assert_not_called()

    def test_stop_after_launch_failure_resets_error(self):
        self.quiet.side_effect = FileNotFoundError('ros2 missing')
        self.service.start(TOOL_MEDKIT)
        self.assertEqual(self.service.stop(TOOL_MEDKIT), ToolState('stopped'))
        self.interrupt.assert_not_called()

    def test_stop_reaps_an_already_exited_process(self):
        self.service.start(TOOL_MEDKIT)
        self.quiet.return_value.poll.return_value = 0
        self.assertEqual(self.service.stop(TOOL_MEDKIT).kind, 'stopped')
        self.interrupt.assert_called_once()

    def test_start_after_completed_stop_launches_a_new_process(self):
        self.service.start(TOOL_MEDKIT)
        self.service.stop(TOOL_MEDKIT)
        self.quiet.return_value = make_process(202)
        self.assertEqual(self.service.start(TOOL_MEDKIT), ToolState('started', 202))
        self.assertEqual(self.quiet.call_count, 2)

    def test_stop_failure_keeps_handle_for_retry(self):
        for error in (PermissionError('cannot signal'), subprocess.TimeoutExpired('ros2', 5)):
            with self.subTest(error=error):
                self.interrupt.side_effect = error
                proc = make_process(300)
                self.quiet.return_value = proc
                self.service.start(TOOL_MEDKIT)
                self.assertEqual(self.service.stop(TOOL_MEDKIT), ToolState('error', detail=str(error)))
                self.assertIs(handle(TOOL_MEDKIT)[0], proc)
                self.assertEqual(self.service.start(TOOL_MEDKIT).kind, 'already_running')
                self.interrupt.side_effect = None
                self.assertEqual(self.service.stop(TOOL_MEDKIT).kind, 'stopped')

    def test_spawns_in_a_new_session_and_shutdown_tools_stops_it(self):
        """Use a real process: a child in the UI's own group would make kill_group signal the UI."""
        command = [sys.executable, '-c', 'import time; time.sleep(60)']
        with (patch.object(sas, 'spawn_quiet', process_registry.spawn_quiet),
              patch.object(sas, '_MEDKIT_CMD', command)):
            state = self.service.start(TOOL_MEDKIT)
        proc = handle(TOOL_MEDKIT)[0]
        try:
            self.assertEqual(state.kind, 'started')
            self.assertEqual(os.getsid(proc.pid), proc.pid)
            self.assertNotEqual(os.getpgid(proc.pid), os.getpgrp())
            process_registry.shutdown_tools()
            self.assertIsNotNone(proc.poll())
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


class TestStopOtherTools(ServiceTestCase):
    def test_explorer_and_grapher_stop_the_process_group_and_release_the_handle(self):
        for tool in (TOOL_EXPLORER, TOOL_GRAPHER):
            with self.subTest(tool=tool):
                self.kill_group.reset_mock()
                self.service.start(tool)
                self.assertEqual(self.service.stop(tool), ToolState('stopped'))
                self.kill_group.assert_called_once_with(self.logged.return_value)
                self.assertEqual(handle(tool), [None])
                self.assertEqual(self.service.start(tool).kind, 'started')

    def test_rviz_stops_the_process_then_its_display_daemons(self):
        order = Mock()
        order.attach_mock(self.kill_group, 'kill')
        order.attach_mock(self.stop_all, 'stop_all')
        self.service.start(TOOL_RVIZ)
        self.assertEqual(self.service.stop(TOOL_RVIZ), ToolState('stopped'))
        daemons = process_registry.shared('rviz_daemons', list)
        self.assertEqual(order.mock_calls, [call.kill(self.logged.return_value), call.stop_all(daemons)])
        self.assertEqual(handle(TOOL_RVIZ), [None])

    def test_rviz_stop_without_a_process_still_stops_the_display_daemons(self):
        self.assertEqual(self.service.stop(TOOL_RVIZ).kind, 'stopped')
        self.stop_all.assert_called_once()

    def test_failed_stop_is_reported_and_keeps_the_handle(self):
        self.service.start(TOOL_EXPLORER)
        self.kill_group.side_effect = PermissionError('cannot signal')
        self.assertEqual(self.service.stop(TOOL_EXPLORER), ToolState('error', detail='cannot signal'))
        self.assertIs(handle(TOOL_EXPLORER)[0], self.logged.return_value)

    def test_failed_display_cleanup_keeps_the_rviz_handle(self):
        self.service.start(TOOL_RVIZ)
        self.stop_all.side_effect = OSError('daemon stuck')
        self.assertEqual(self.service.stop(TOOL_RVIZ).kind, 'error')
        self.assertIs(handle(TOOL_RVIZ)[0], self.logged.return_value)


class TestRvizDisplayReaper(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(sas.threading, 'Thread'))  # keep the reaper off a real thread
        self.service.start(TOOL_RVIZ)
        self.rviz = self.logged.return_value
        self.daemons = process_registry.shared('rviz_daemons', list)
        self.daemons.extend([make_process(1), make_process(2)])
        self.reap = lambda: REAL_REAP(self.service, self.rviz)

    def test_starting_rviz_watches_the_process(self):
        thread = sas.threading.Thread
        thread.assert_called_once_with(
            target=self.service._reap_rviz_display, args=(self.rviz,), daemon=True)
        thread.return_value.start.assert_called_once_with()

    def test_rviz_exiting_on_its_own_stops_the_display_daemons(self):
        stopped = list(self.daemons)
        self.reap()
        self.rviz.wait.assert_called_once_with()
        self.stop_all.assert_called_once_with(stopped)
        self.assertEqual(self.daemons, [])

    def test_a_stopped_rviz_leaves_its_daemons_to_the_stop(self):
        handle(TOOL_RVIZ)[0] = None
        self.reap()
        self.stop_all.assert_not_called()
        self.assertEqual(len(self.daemons), 2)

    def test_a_restarted_rviz_keeps_the_new_daemons(self):
        handle(TOOL_RVIZ)[0] = make_process(202)
        self.reap()
        self.stop_all.assert_not_called()
        self.assertEqual(len(self.daemons), 2)

    def test_an_unsignalable_daemon_does_not_raise(self):
        self.stop_all.side_effect = PermissionError('cannot signal')
        self.reap()

    def test_the_exit_is_still_reported_after_the_display_is_reaped(self):
        self.rviz.poll.return_value = 1
        self.reap()
        self.assertEqual(self.service.exited(TOOL_RVIZ),
                         ToolState('exited', 101, '/tmp/rviz.log', '1'))


class TestPolling(ServiceTestCase):
    def test_status_is_idle_until_started_then_running(self):
        self.assertEqual(self.service.status(TOOL_RVIZ), ToolState('idle'))
        self.service.start(TOOL_RVIZ)
        self.assertEqual(self.service.status(TOOL_RVIZ), ToolState('running', 101, '/tmp/rviz.log'))

    def test_status_does_not_report_a_dead_process_as_running(self):
        self.service.start(TOOL_EXPLORER)
        self.logged.return_value.poll.return_value = 1
        self.assertEqual(self.service.status(TOOL_EXPLORER), ToolState('idle'))

    def test_exited_reports_return_code_and_log_and_keeps_the_dead_handle(self):
        self.assertEqual(self.service.exited(TOOL_EXPLORER), ToolState('idle'))
        self.service.start(TOOL_EXPLORER)
        self.assertEqual(self.service.exited(TOOL_EXPLORER).kind, 'running')
        self.logged.return_value.poll.return_value = 3
        self.assertEqual(self.service.exited(TOOL_EXPLORER),
                         ToolState('exited', 101, '/tmp/ros2graph_explorer.log', '3'))
        self.assertIs(handle(TOOL_EXPLORER)[0], self.logged.return_value)
        self.assertEqual(self.service.start(TOOL_EXPLORER).kind, 'started')


class TestOverlappingActions(ServiceTestCase):
    """While a start or stop is in flight, every other action on that tool is busy."""

    def run_blocked(self, mock, action, tool):
        """Run action(tool) on a thread whose call to mock blocks; return after it has blocked.

        Return the thread, the release event and a one-item list that receives the result.
        """
        entered, release, result = threading.Event(), threading.Event(), []

        def block(*_args, **_kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return make_process(150)

        mock.side_effect = block
        thread = threading.Thread(target=lambda: result.append(action(tool)))
        thread.start()
        self.assertTrue(entered.wait(5))
        return thread, release, result

    def test_actions_during_a_start_are_busy_and_do_not_spawn_twice(self):
        thread, release, result = self.run_blocked(self.vnc, self.service.start, TOOL_RVIZ)
        try:
            for service in (self.service, SystemApplicationService()):
                for action in (service.start, service.stop, service.exited):
                    self.assertEqual(action(TOOL_RVIZ), ToolState('busy'))
            # Another tool is unaffected, and reading the status never waits.
            self.assertEqual(self.service.start(TOOL_EXPLORER).kind, 'started')
            self.assertEqual(self.service.status(TOOL_RVIZ), ToolState('idle'))
        finally:
            release.set()
            thread.join(5)
        self.assertEqual(result[0].kind, 'started')
        self.assertEqual(self.vnc.call_count, 1)

    def test_actions_during_a_stop_are_busy_and_the_stop_completes(self):
        self.service.start(TOOL_MEDKIT)
        thread, release, result = self.run_blocked(self.interrupt, self.service.stop, TOOL_MEDKIT)
        try:
            self.quiet.reset_mock()
            self.quiet.side_effect = None
            self.quiet.return_value.poll.return_value = 0  # even an exited process cannot be restarted yet
            for service in (self.service, SystemApplicationService()):
                for action in (service.start, service.stop, service.exited):
                    self.assertEqual(action(TOOL_MEDKIT), ToolState('busy'))
            self.quiet.assert_not_called()
        finally:
            release.set()
            thread.join(5)
        self.assertEqual(result[0], ToolState('stopped'))
        self.assertEqual(self.interrupt.call_count, 1)
        self.assertEqual(self.service.start(TOOL_MEDKIT).kind, 'started')

    def test_the_lock_is_released_after_every_outcome(self):
        self.logged.side_effect = [OSError('boom'), make_process()]
        self.assertEqual(self.service.start(TOOL_EXPLORER).kind, 'error')
        self.kill_group.side_effect = [OSError('stuck'), None]
        self.assertEqual(self.service.start(TOOL_EXPLORER).kind, 'started')
        self.assertEqual(self.service.stop(TOOL_EXPLORER).kind, 'error')
        self.assertEqual(self.service.stop(TOOL_EXPLORER).kind, 'stopped')


if __name__ == '__main__':
    unittest.main()
