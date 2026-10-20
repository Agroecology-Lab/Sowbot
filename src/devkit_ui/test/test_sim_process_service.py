"""Gazebo and row-follow process control with every process boundary mocked."""

# pylint: disable=protected-access
import shlex
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import partial
from unittest.mock import Mock, call, patch

from devkit_ui import process_registry
from devkit_ui.application_services import sim_process_service
from devkit_ui.application_services.sim_process_service import SimProcessApplicationService

_ENV = {'GZ_SIM_RESOURCE_PATH': '/custom/models', 'DISPLAY': ':0'}


class SimProcessTestCase(unittest.TestCase):
    def setUp(self):
        """Isolate the shared handle registry and the environment, and mock process creation."""
        self.enterContext(patch.dict(process_registry._TOOLS, clear=True))
        self.enterContext(patch.dict(sim_process_service.os.environ, _ENV, clear=True))
        self.spawn = self.enterContext(patch.object(sim_process_service, 'spawn_logged'))
        self.spawn.return_value = Mock(pid=123, poll=Mock(return_value=None))
        self.kill = self.enterContext(patch.object(sim_process_service, 'kill_group'))
        self.stop_all = self.enterContext(patch.object(sim_process_service, 'stop_all'))
        self.vnc = self.enterContext(patch.object(sim_process_service, 'start_vnc_stack'))
        self.service = SimProcessApplicationService()


class TestGazebo(SimProcessTestCase):
    def test_launch_uses_selected_robot_full_stack_and_resource_search_order(self):
        """Verify a headless launch uses the selected robot and the ordered model search paths."""
        state = self.service.launch_gazebo('robo_caatinga.urdf.xacro', 'headless')
        self.spawn.assert_called_once_with(
            ['ros2', 'launch', 'devkit_bringup', 'sowbot_sim.launch.py', 'world:=maize.world',
             'urdf:=robo_caatinga.urdf.xacro', 'headless:=true'],
            '/tmp/gazebo_sim.log',
            env={
                'DISPLAY': ':0',
                'TMAP2_FILE': '/workspace/maps/maize_map',
                'GZ_SIM_RESOURCE_PATH': '/workspace/models:/workspace/install/virtual_maize_field/'
                'share/virtual_maize_field/models:/custom/models',
            })
        self.assertEqual(
            (state.kind, state.pid, state.log, state.detail),
            ('started', 123, '/tmp/gazebo_sim.log', 'headless'))
        self.assertTrue(self.service.gazebo_running())

    def test_resource_path_has_no_trailing_separator_without_an_inherited_one(self):
        """Verify the model search path is only the workspace models when none was inherited."""
        with patch.dict(sim_process_service.os.environ, clear=True):
            self.service.launch_gazebo('sowbot_01.xacro')
        env = self.spawn.call_args.kwargs['env']
        self.assertFalse(env['GZ_SIM_RESOURCE_PATH'].endswith(':'))

    def test_normal_launch_is_not_headless(self):
        """Verify the default mode asks for a visible simulation."""
        self.service.launch_gazebo('sowbot_01.xacro')
        self.assertEqual(self.spawn.call_args.args[0][-1], 'headless:=false')

    def test_live_process_blocks_every_launch_mode(self):
        """Verify an active simulation prevents a duplicate launch in any mode."""
        self.service.launch_gazebo('sowbot_01.xacro')
        for mode in ('normal', 'headless', 'browser'):
            with self.subTest(mode=mode):
                state = self.service.launch_gazebo('sowbot_01.xacro', mode)
                self.assertEqual((state.kind, state.pid), ('already_running', 123))
        self.spawn.assert_called_once()
        self.vnc.assert_not_called()

    def test_reload_shares_the_running_process_with_a_new_service(self):
        """Verify a second service instance sees and refuses to duplicate the running sim."""
        self.service.launch_gazebo('sowbot_01.xacro')
        second = SimProcessApplicationService()
        self.assertEqual(second.gazebo_status().kind, 'running')
        self.assertEqual(second.launch_gazebo('sowbot_01.xacro').kind, 'already_running')
        self.spawn.assert_called_once()

    def test_exited_process_can_be_replaced(self):
        """Verify a completed simulation process can be replaced by a new launch."""
        process_registry.shared('gazebo', lambda: [None])[0] = Mock(poll=Mock(return_value=1))
        self.assertFalse(self.service.gazebo_running())
        self.assertEqual(self.service.launch_gazebo('sowbot_01.xacro').kind, 'started')

    def test_launch_failure_leaves_handle_empty_and_reports_error(self):
        """Verify failed spawning leaves no process handle and reports the error."""
        self.spawn.side_effect = OSError('launch missing')
        state = self.service.launch_gazebo('sowbot_01.xacro')
        self.assertEqual((state.kind, state.detail), ('error', 'launch missing'))
        self.assertIsNone(process_registry.shared('gazebo', lambda: [None])[0])
        self.spawn.side_effect = None
        self.assertEqual(self.service.launch_gazebo('sowbot_01.xacro').kind, 'started')

    def test_browser_launch_uses_virtual_display_without_changing_the_normal_environment(self):
        """Verify browser launch isolates display and software-rendering settings."""
        self.service.launch_gazebo('sowbot_01.xacro', 'browser')
        self.vnc.assert_called_once_with(
            ':99', 5900, 6080, process_registry.shared('gazebo_daemons', list))
        command = self.spawn.call_args.args[0]
        self.assertEqual(
            command[:7], ['unshare', '--mount', '--propagation', 'private', '--', 'bash', '-c'])
        self.assertTrue(command[7].startswith('mount -t tmpfs tmpfs /dev/dri 2>/dev/null; exec '))
        self.assertEqual(
            shlex.split(command[7].split('; exec ', 1)[1]), self.service._sim_cmd('sowbot_01.xacro'))
        self.assertEqual(
            self.spawn.call_args.kwargs['env'],
            {**self.service._env(), 'DISPLAY': ':99', 'LIBGL_ALWAYS_SOFTWARE': '1',
             'GALLIUM_DRIVER': 'llvmpipe', 'MESA_LOADER_DRIVER_OVERRIDE': 'llvmpipe'})
        self.assertEqual(self.service._env()['DISPLAY'], ':0')

    def test_browser_display_failure_does_not_start_gazebo(self):
        """Verify a virtual display error prevents Gazebo startup and is reported."""
        self.vnc.side_effect = OSError('display unavailable')
        state = self.service.launch_gazebo('sowbot_01.xacro', 'browser')
        self.spawn.assert_not_called()
        self.assertEqual((state.kind, state.detail), ('error', 'display unavailable'))
        self.assertFalse(self.service.gazebo_running())
        self.vnc.side_effect = None
        self.assertEqual(self.service.launch_gazebo('sowbot_01.xacro', 'browser').kind, 'started')

    def test_stop_kills_both_processes_then_the_display_and_releases_handles(self):
        """Verify stop terminates the sim and the robot spawn, then the daemons, and clears them."""
        gazebo, robot = Mock(), Mock()
        process_registry.shared('gazebo', lambda: [None])[0] = gazebo
        process_registry.shared('gazebo_spawn', lambda: [None])[0] = robot
        daemons = process_registry.shared('gazebo_daemons', list)
        self.assertEqual(self.service.stop_gazebo().kind, 'stopped')
        self.assertEqual(self.kill.call_args_list, [call(gazebo), call(robot)])
        self.stop_all.assert_called_once_with(daemons)
        for name in ('gazebo', 'gazebo_spawn'):
            self.assertEqual(process_registry.shared(name, lambda: [None]), [None])


class TestRowFollow(SimProcessTestCase):
    def test_start_uses_sim_camera_and_a_second_service_reuses_the_handle(self):
        """Verify row following uses the simulated camera and shares its process on reload."""
        state = self.service.start_row_follow()
        self.assertEqual(
            self.spawn.call_args.args,
            (['ros2', 'launch', 'devkit_bringup', 'neo.launch.py', 'use_camera:=false',
              'detector:=tsm', 'image_topic:=/camera/image_raw'], '/tmp/neo_sim.log'))
        self.assertEqual((state.kind, state.pid, state.log), ('started', 123, '/tmp/neo_sim.log'))
        second = SimProcessApplicationService()
        self.assertEqual(second.row_follow_status().kind, 'running')
        self.assertEqual(second.start_row_follow().kind, 'already_running')
        self.spawn.assert_called_once()

    def test_stop_releases_the_handle_and_a_second_stop_is_a_no_op(self):
        """Verify stopping clears the shared handle and repeated stops do no cleanup."""
        self.service.start_row_follow()
        proc = self.spawn.return_value
        self.assertEqual(self.service.stop_row_follow().kind, 'stopped')
        self.kill.assert_called_once_with(proc)
        self.assertEqual(self.service.row_follow_status().kind, 'idle')
        self.assertEqual(self.service.stop_row_follow().kind, 'idle')
        self.kill.assert_called_once()
        self.assertEqual(self.service.start_row_follow().kind, 'started')

    def test_failed_start_reports_an_error_and_can_be_retried(self):
        """Verify failed startup reports an error, leaves no handle, and allows a retry."""
        self.spawn.side_effect = OSError('missing neo')
        state = self.service.start_row_follow()
        self.assertEqual((state.kind, state.detail), ('error', 'missing neo'))
        self.assertEqual(self.service.row_follow_status().kind, 'idle')
        self.spawn.side_effect = None
        self.assertEqual(self.service.start_row_follow().kind, 'started')


class TestOverlappingActions(SimProcessTestCase):
    @contextmanager
    def blocked(self, boundary, action, expected):
        """Hold a mocked process operation until the caller finishes its overlap checks."""
        entered, release = threading.Event(), threading.Event()

        def wait(*_args, **_kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return self.spawn.return_value

        boundary.side_effect = wait
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(action)
            try:
                self.assertTrue(entered.wait(5))
                yield
            finally:
                release.set()
                boundary.side_effect = None
            self.assertEqual(result.result(timeout=5).kind, expected)

    def test_start_blocks_overlapping_actions_across_sessions(self):
        """A start owns its handle and display stack until the process has been registered."""
        for start, stop, args, boundary in (
            ('launch_gazebo', 'stop_gazebo', ('robot.xacro', 'browser'), self.vnc),
            ('start_row_follow', 'stop_row_follow', (), self.spawn),
        ):
            with self.subTest(start=start):
                action = partial(getattr(self.service, start), *args)
                with self.blocked(boundary, action, 'started'):
                    for service in (self.service, SimProcessApplicationService()):
                        self.assertEqual(getattr(service, start)(*args).kind, 'busy')
                        self.assertEqual(getattr(service, stop)().kind, 'busy')
                self.assertEqual(getattr(self.service, start)(*args).kind, 'already_running')
                self.spawn.assert_called_once()
                self.assertEqual(getattr(self.service, stop)().kind, 'stopped')
                self.spawn.reset_mock()

    def test_stop_blocks_actions_through_display_cleanup_and_keeps_other_tool_available(self):
        """A start cannot race with termination or daemon cleanup, even after handles clear."""
        for start, stop, args, boundary, other_start, other_args in (
            ('launch_gazebo', 'stop_gazebo', ('robot.xacro',), self.stop_all,
             'start_row_follow', ()),
            ('start_row_follow', 'stop_row_follow', (), self.kill,
             'launch_gazebo', ('robot.xacro',)),
        ):
            with self.subTest(stop=stop):
                getattr(self.service, start)(*args)
                with self.blocked(boundary, getattr(self.service, stop), 'stopped'):
                    for service in (self.service, SimProcessApplicationService()):
                        self.assertEqual(getattr(service, start)(*args).kind, 'busy')
                        self.assertEqual(getattr(service, stop)().kind, 'busy')
                    self.assertEqual(getattr(self.service, other_start)(*other_args).kind,
                                     'started')
                self.assertEqual(getattr(self.service, start)(*args).kind, 'started')
                self.service.stop_gazebo()
                self.service.stop_row_follow()

    def test_stop_failure_releases_lock_and_preserves_handle_for_retry(self):
        """A termination exception cannot leave either process permanently busy."""
        for start, stop, args in (('launch_gazebo', 'stop_gazebo', ('robot.xacro',)),
                                  ('start_row_follow', 'stop_row_follow', ())):
            with self.subTest(stop=stop):
                getattr(self.service, start)(*args)
                self.kill.side_effect = OSError('stop failed')
                try:
                    with self.assertRaisesRegex(OSError, 'stop failed'):
                        getattr(self.service, stop)()
                finally:
                    self.kill.side_effect = None
                self.assertEqual(getattr(self.service, start)(*args).kind, 'already_running')
                self.assertEqual(getattr(self.service, stop)().kind, 'stopped')


if __name__ == '__main__':
    unittest.main()
