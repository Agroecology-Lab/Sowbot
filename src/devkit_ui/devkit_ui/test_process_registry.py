"""Process ownership and display setup without starting external programs."""

# pylint: disable=protected-access
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from devkit_ui import process_registry as registry


class TestProcessRegistry(unittest.TestCase):
    def setUp(self):
        """Isolate shared process entries and restore the registry after each test."""
        self.enterContext(patch.dict(registry._TOOLS, clear=True))

    def test_shared_handles_survive_second_session_and_keep_names_separate(self):
        """Verify sessions share handles by tool name without mixing different tools."""
        gazebo = registry.shared('gazebo', lambda: [None])
        process = Mock()
        gazebo[0] = process
        self.assertIs(registry.shared('gazebo', lambda: [None]), gazebo)
        self.assertIs(registry.shared('gazebo', list)[0], process)
        self.assertEqual(registry.shared('rviz', list), [])

    def test_alive_distinguishes_missing_running_and_exited_handles(self):
        """Verify only a handle whose process has not exited is considered alive."""
        self.assertFalse(registry.alive(None))
        for code in (None, 0, 1, -15):
            with self.subTest(returncode=code):
                self.assertEqual(registry.alive(Mock(poll=Mock(return_value=code))), code is None)

    def test_spawn_truncates_log_redirects_streams_and_closes_parent_handle(self):
        """Verify spawning replaces the log and redirects both streams in a new session."""
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'tool.log'
            log.write_text('stale output', encoding='utf-8')
            with patch.object(registry.subprocess, 'Popen') as popen:
                result = registry.spawn_logged(
                    ['tool', 'argument'], str(log), cwd='/work', env={'A': 'B'}
                )
            self.assertIs(result, popen.return_value)
            fh = popen.call_args.kwargs['stdout']
            popen.assert_called_once_with(
                ['tool', 'argument'],
                stdout=fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                cwd='/work',
                env={'A': 'B'},
            )
            self.assertTrue(fh.closed)
            self.assertEqual(log.read_text(encoding='utf-8'), '')

    def test_spawn_failure_propagates_and_closes_log(self):
        """Verify a spawn error propagates while the parent's log handle is closed."""
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                registry.subprocess, 'Popen', side_effect=OSError('cannot start')
            ) as popen:
                with self.assertRaisesRegex(OSError, 'cannot start'):
                    registry.spawn_logged(['missing'], str(Path(directory) / 'tool.log'))
            self.assertTrue(popen.call_args.kwargs['stdout'].closed)

    def test_unwritable_log_prevents_process_creation(self):
        """Verify failure to open the log prevents starting the subprocess."""
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(registry.subprocess, 'Popen') as popen:
                with self.assertRaises(IsADirectoryError):
                    registry.spawn_logged(['tool'], directory)
            popen.assert_not_called()

    def test_stop_all_stops_every_handle_and_clears_list_in_place(self):
        """Verify every handle is stopped and existing aliases see the emptied list."""
        first, second = Mock(), Mock()
        handles = [first, None, second]
        alias = handles
        with patch.object(registry, 'kill_group') as kill:
            registry.stop_all(handles)
        self.assertEqual(
            kill.call_args_list,
            [call(first, grace=3.0), call(None, grace=3.0), call(second, grace=3.0)],
        )
        self.assertEqual(alias, [])

    def test_shutdown_handles_scalar_and_list_entries_and_ignores_nonprocesses(self):
        """Verify shutdown stops process entries, including those stored in lists."""
        first, second = Mock(spec=subprocess.Popen), Mock(spec=subprocess.Popen)
        registry._TOOLS.update(
            single=first, grouped=[None, second, 'metadata'], empty=[], other=None
        )
        with patch.object(registry, 'kill_group') as kill:
            registry.shutdown_tools()
        self.assertEqual(kill.call_args_list, [call(first, grace=3.0), call(second, grace=3.0)])

    def test_spawn_quiet_discards_output_and_starts_a_new_session(self):
        """Verify output goes to DEVNULL and the child leads its own session, away from the UI's group."""
        with patch.object(registry.subprocess, 'Popen') as popen:
            result = registry.spawn_quiet(['tool', 'argument'], cwd='/work')
        self.assertIs(result, popen.return_value)
        popen.assert_called_once_with(
            ['tool', 'argument'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, cwd='/work')

    def test_spawn_quiet_failure_propagates(self):
        """Verify a process-creation error reaches the caller."""
        with patch.object(registry.subprocess, 'Popen', side_effect=OSError('cannot start')):
            with self.assertRaisesRegex(OSError, 'cannot start'):
                registry.spawn_quiet(['missing'])

    def test_interrupt_stop_escalates_only_after_each_timeout(self):
        """Verify SIGINT goes to the leader alone and terminate and kill follow only on timeouts."""
        timeout = subprocess.TimeoutExpired('ros2', 15)
        sigint = call.send_signal(signal.SIGINT)
        for waits, expected in (
            ([0], [sigint, call.wait(timeout=15)]),
            ([timeout, 0], [sigint, call.wait(timeout=15), call.terminate(), call.wait(timeout=5)]),
            ([timeout, timeout, 0], [sigint, call.wait(timeout=15), call.terminate(),
                                     call.wait(timeout=5), call.kill(), call.wait(timeout=5)]),
        ):
            with self.subTest(waits=waits):
                proc = Mock(spec=['send_signal', 'wait', 'terminate', 'kill'])
                proc.wait.side_effect = waits
                registry.interrupt_stop(proc, 15, 5, 5)
                self.assertEqual(proc.mock_calls, expected)

    def test_interrupt_stop_propagates_signal_errors_and_a_timeout_after_kill(self):
        """Verify the caller sees an OS error before any wait, and a final timeout."""
        proc = Mock()
        proc.send_signal.side_effect = PermissionError('cannot signal')
        with self.assertRaisesRegex(PermissionError, 'cannot signal'):
            registry.interrupt_stop(proc, 15, 5, 5)
        proc.wait.assert_not_called()
        proc = Mock()
        proc.wait.side_effect = subprocess.TimeoutExpired('ros2', 5)
        with self.assertRaises(subprocess.TimeoutExpired):
            registry.interrupt_stop(proc, 15, 5, 5)
        proc.kill.assert_called_once_with()
        self.assertEqual(proc.wait.call_count, 3)

    def test_shutdown_stops_a_quietly_spawned_process_without_touching_the_callers_group(self):
        """Verify a spawn_quiet child registered for shutdown is killed alone, not with this process."""
        proc = registry.spawn_quiet([sys.executable, '-c', 'import time; time.sleep(60)'])
        try:
            registry._TOOLS['quiet'] = [proc]
            self.assertNotEqual(os.getpgid(proc.pid), os.getpgrp())
            registry.shutdown_tools()
            self.assertIsNotNone(proc.poll())
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_permission_error_terminating_group_is_not_hidden(self):
        """Verify denied process-group access propagates before waiting for exit."""
        proc = Mock(poll=Mock(return_value=None))
        with patch.object(registry.os, 'getpgid', side_effect=PermissionError('denied')):
            with self.assertRaisesRegex(PermissionError, 'denied'):
                registry.kill_group(proc)
        proc.wait.assert_not_called()


class TestDisplayProcesses(unittest.TestCase):
    def setUp(self):
        """Replace display, process, and timing operations with isolated test doubles."""
        self.exists = self.enterContext(
            patch.object(registry.os.path, 'exists', return_value=False)
        )
        self.remove = self.enterContext(patch.object(registry.os, 'remove'))
        self.run = self.enterContext(patch.object(registry.subprocess, 'run'))
        self.spawn = self.enterContext(patch.object(registry, 'spawn_logged'))
        self.sleep = self.enterContext(patch.object(registry.time, 'sleep'))
        self.daemons = []

    def test_new_display_tracks_xvfb_without_probing_or_removing_locks(self):
        """Verify an unused display starts Xvfb and records its process handle."""
        registry.ensure_xvfb(':99', self.daemons)
        self.spawn.assert_called_once_with(
            ['Xvfb', ':99', '-screen', '0', '1920x1080x24', '-nolisten', 'tcp'], '/tmp/xvfb99.log'
        )
        self.assertEqual(self.daemons, [self.spawn.return_value])
        self.run.assert_not_called()
        self.remove.assert_not_called()

    def test_live_display_is_reused_without_claiming_ownership(self):
        """Verify a running Xvfb is reused without recording it for later cleanup."""
        self.exists.return_value = True
        self.run.return_value.returncode = 0
        registry.ensure_xvfb(':98', self.daemons)
        self.run.assert_called_once_with(
            ['pgrep', '-f', '[X]vfb :98'], capture_output=True, check=False
        )
        self.spawn.assert_not_called()
        self.remove.assert_not_called()
        self.sleep.assert_not_called()
        self.assertEqual(self.daemons, [])

    def test_stale_display_removes_both_locks_even_if_one_removal_fails(self):
        """Verify stale locks are both cleaned up before starting a replacement Xvfb."""
        self.exists.return_value = True
        self.run.return_value.returncode = 1
        self.remove.side_effect = [FileNotFoundError(), None]
        registry.ensure_xvfb(':98', self.daemons)
        self.assertEqual(
            self.remove.call_args_list, [call('/tmp/.X98-lock'), call('/tmp/.X11-unix/X98')]
        )
        self.spawn.assert_called_once()
        self.assertEqual(self.daemons, [self.spawn.return_value])

    def test_failed_xvfb_start_does_not_register_a_handle(self):
        """Verify an Xvfb spawn error leaves the owned daemon list empty."""
        self.spawn.side_effect = OSError('Xvfb missing')
        with self.assertRaisesRegex(OSError, 'Xvfb missing'):
            registry.ensure_xvfb(':99', self.daemons)
        self.assertEqual(self.daemons, [])
        self.sleep.assert_not_called()

    def test_vnc_stack_reaps_old_handles_and_uses_requested_ports(self):
        """Verify VNC startup replaces old daemons and uses the requested display and ports."""
        old, xvfb, vnc, web = Mock(), Mock(), Mock(), Mock()
        self.daemons.append(old)
        self.spawn.side_effect = [xvfb, vnc, web]
        with patch.object(registry, 'kill_group') as kill:
            registry.start_vnc_stack(':98', 5901, 6081, self.daemons)
        kill.assert_called_once_with(old, grace=3.0)
        self.assertEqual(self.daemons, [xvfb, vnc, web])
        self.assertEqual(
            self.spawn.call_args_list[1:],
            [
                call(
                    [
                        'x11vnc',
                        '-display',
                        ':98',
                        '-nopw',
                        '-forever',
                        '-shared',
                        '-quiet',
                        '-localhost',
                        '-rfbport',
                        '5901',
                    ],
                    '/tmp/x11vnc-5901.log',
                ),
                call(
                    ['websockify', '--web', '/usr/share/novnc', '6081', 'localhost:5901'],
                    '/tmp/websockify-6081.log',
                ),
            ],
        )


if __name__ == '__main__':
    unittest.main()
