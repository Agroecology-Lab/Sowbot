"""Simulation launch and rebuild guards with all process boundaries mocked."""

# pylint: disable=protected-access
import shlex
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

from devkit_ui import process_registry
from devkit_ui.test_run_ui import fake_ui, nicegui

with (
    patch.dict(sys.modules, {'nicegui': nicegui}),
    patch.object(nicegui, 'run', SimpleNamespace(), create=True),
):
    from devkit_ui.pages.sim import gazebo_card, row_follow_card
    from devkit_ui.pages.sim.plant_placement_card import PlantSettings


class TestGazeboCard(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        self.enterContext(patch.dict(process_registry._TOOLS, clear=True))
        self.enterContext(
            patch.dict(
                gazebo_card.os.environ,
                {'GZ_SIM_RESOURCE_PATH': '/custom/models', 'DISPLAY': ':0'},
                clear=True,
            )
        )
        self.enterContext(patch.object(gazebo_card, 'request_host', return_value='robot.local'))
        self.spawn = self.enterContext(patch.object(gazebo_card, 'spawn_logged'))
        self.spawn.return_value = Mock(pid=123, poll=Mock(return_value=None))
        self.io_bound = AsyncMock(return_value='7')
        self.enterContext(
            patch.object(gazebo_card, 'ng_run', SimpleNamespace(io_bound=self.io_bound))
        )
        self.vm = Mock()
        self.settings = Mock(return_value=PlantSettings(0.45, 0.8, 1.5, 'crop', 'maize', 0))
        self.card = gazebo_card.GazeboCard(self.vm, self.settings)

    def test_launch_uses_selected_robot_full_stack_and_resource_search_order(self):
        fake_ui.find('toggle').kwargs['on_change'](SimpleNamespace(value='caatinga (diff drive)'))
        fake_ui.find('button', 'Launch Sim (headless)').on_click()
        self.spawn.assert_called_once_with(
            [
                'ros2',
                'launch',
                'devkit_bringup',
                'sowbot_sim.launch.py',
                'world:=maize.world',
                'urdf:=robo_caatinga.urdf.xacro',
                'headless:=true',
            ],
            '/tmp/gazebo_sim.log',
            env={
                'DISPLAY': ':0',
                'TMAP2_FILE': '/workspace/maps/maize_map',
                'GZ_SIM_RESOURCE_PATH': '/workspace/models:/workspace/install/virtual_maize_field/share/'
                'virtual_maize_field/models:/custom/models',
            },
        )
        self.assertIs(self.card._gazebo[0], self.spawn.return_value)
        self.assertIn('headless', self.card._status.text)
        self.assertEqual(self.card._status.styles[-1], 'color:#1a7f37')

    def test_all_robot_choices_produce_expected_launch_arguments(self):
        for label, xacro in (
            ('sowbot (tracked)', 'sowbot_01.xacro'),
            ('caatinga (diff drive)', 'robo_caatinga.urdf.xacro'),
            ('ifarmate (diff drive)', 'ifarmate.urdf.xacro'),
        ):
            with self.subTest(label=label):
                self.card._select_model(SimpleNamespace(value=label))
                self.assertEqual(
                    self.card._sim_cmd(),
                    [
                        'ros2',
                        'launch',
                        'devkit_bringup',
                        'sowbot_sim.launch.py',
                        'world:=maize.world',
                        f'urdf:={xacro}',
                        'headless:=false',
                    ],
                )

    async def test_live_process_blocks_all_launch_modes_and_rebuild(self):
        self.card._gazebo[0] = self.spawn.return_value
        self.card._launch()
        await self.card._launch_in_browser()
        await self.card._rebuild_world()
        self.spawn.assert_not_called()
        self.io_bound.assert_not_awaited()
        self.settings.assert_not_called()
        self.assertIn('stop the sim before rebuilding', self.card._status.text)

    def test_reload_retains_process_and_refuses_duplicate_launch(self):
        self.card._launch()
        second = gazebo_card.GazeboCard(self.vm, self.settings)
        self.assertIs(second._gazebo, self.card._gazebo)
        self.assertIs(second._daemons, self.card._daemons)
        self.assertEqual(second._status.text, 'running (pid 123)')
        second._launch()
        self.spawn.assert_called_once()
        self.assertEqual(second._status.text, 'already running')

    def test_exited_process_can_be_replaced(self):
        self.card._gazebo[0] = Mock(poll=Mock(return_value=1))
        self.card._launch()
        self.spawn.assert_called_once()
        self.assertIs(self.card._gazebo[0], self.spawn.return_value)

    def test_launch_failure_leaves_handle_empty_and_reports_error(self):
        self.spawn.side_effect = OSError('launch missing')
        self.card._launch()
        self.assertIsNone(self.card._gazebo[0])
        self.assertEqual(self.card._status.text, 'ERROR: launch missing')
        self.assertEqual(self.card._status.styles[-1], 'color:#cf222e')

    async def test_browser_launch_uses_virtual_display_without_mutating_normal_environment(self):
        original = self.card._env.copy()
        await self.card._launch_in_browser()
        self.io_bound.assert_awaited_once_with(
            gazebo_card.start_vnc_stack, ':99', 5900, 6080, self.card._daemons
        )
        command = self.spawn.call_args.args[0]
        self.assertEqual(
            command[:7], ['unshare', '--mount', '--propagation', 'private', '--', 'bash', '-c']
        )
        self.assertTrue(command[7].startswith('mount -t tmpfs tmpfs /dev/dri 2>/dev/null; exec '))
        self.assertEqual(shlex.split(command[7].split('; exec ', 1)[1]), self.card._sim_cmd())
        self.assertEqual(
            self.spawn.call_args.kwargs['env'],
            {
                **original,
                'DISPLAY': ':99',
                'LIBGL_ALWAYS_SOFTWARE': '1',
                'GALLIUM_DRIVER': 'llvmpipe',
                'MESA_LOADER_DRIVER_OVERRIDE': 'llvmpipe',
            },
        )
        self.assertEqual(self.card._env, original)

    async def test_browser_display_failure_does_not_start_gazebo(self):
        self.io_bound.side_effect = OSError('display unavailable')
        await self.card._launch_in_browser()
        self.spawn.assert_not_called()
        self.assertIsNone(self.card._gazebo[0])
        self.assertEqual(self.card._status.text, 'ERROR: display unavailable')

    async def test_stop_offloads_both_processes_then_display_cleanup_and_releases_handles(self):
        gazebo, robot = Mock(), Mock()
        self.card._gazebo[0], self.card._spawn[0] = gazebo, robot
        await self.card._stop()
        self.assertEqual(
            self.io_bound.await_args_list,
            [
                call(gazebo_card.kill_group, gazebo),
                call(gazebo_card.kill_group, robot),
                call(gazebo_card.stop_all, self.card._daemons),
            ],
        )
        self.assertEqual((self.card._gazebo, self.card._spawn), ([None], [None]))
        self.assertEqual(self.card._status.text, 'stopped')

    async def test_rebuild_uses_current_settings_and_reports_zero_density(self):
        await fake_ui.find('button', 'Rebuild World from Map').on_click()
        self.io_bound.assert_awaited_once_with(
            self.vm.rebuild_world, 0.45, 0.8, 1.5, 'crop', 'maize', 0
        )
        self.assertIn('spacing=0.45m, row=0.80m, scale=1.50 on crop', self.card._status.text)
        self.assertIn('weed density=0% (7 weeds), model=maize', self.card._status.text)
        self.assertEqual(self.card._status.styles[-1], 'color:#1a7f37')

    async def test_rebuild_cancellation_and_errors_are_not_reported_as_success(self):
        for error, text in (
            (None, 'rebuild cancelled'),
            (gazebo_card.WorldRebuildError('no map'), 'no map'),
            (OSError('cannot spawn'), 'ERROR: cannot spawn'),
        ):
            with self.subTest(error=error):
                self.io_bound.return_value = None
                self.io_bound.side_effect = error
                await self.card._rebuild_world()
                self.assertEqual(self.card._status.text, text)
                self.assertEqual(self.card._status.styles[-1], 'color:#cf222e')


class TestRowFollowCard(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        self.enterContext(patch.dict(process_registry._TOOLS, clear=True))
        self.spawn = self.enterContext(patch.object(row_follow_card, 'spawn_logged'))
        self.spawn.return_value = Mock(pid=456, poll=Mock(return_value=None))
        self.io_bound = AsyncMock()
        self.enterContext(
            patch.object(row_follow_card, 'ng_run', SimpleNamespace(io_bound=self.io_bound))
        )
        self.card = row_follow_card.RowFollowCard()

    def test_start_uses_sim_camera_and_reload_reuses_handle(self):
        fake_ui.find('button', 'Start').on_click()
        self.assertEqual(
            self.spawn.call_args.args,
            (
                [
                    'ros2',
                    'launch',
                    'devkit_bringup',
                    'neo.launch.py',
                    'use_camera:=false',
                    'detector:=tsm',
                    'image_topic:=/camera/image_raw',
                ],
                '/tmp/neo_sim.log',
            ),
        )
        second = row_follow_card.RowFollowCard()
        second._start()
        self.spawn.assert_called_once()
        self.assertIs(second._proc, self.card._proc)
        self.assertEqual(second._status.text, 'already running')

    async def test_stop_releases_handle_and_second_stop_is_noop(self):
        self.card._start()
        await self.card._stop()
        self.io_bound.assert_awaited_once_with(row_follow_card.kill_group, self.spawn.return_value)
        self.assertIsNone(self.card._proc[0])
        self.assertEqual(self.card._status.text, 'stopped')
        await self.card._stop()
        self.io_bound.assert_awaited_once()
        self.assertEqual(self.card._status.text, 'not running')

    def test_failed_start_displays_error_and_can_be_retried(self):
        self.spawn.side_effect = OSError('missing neo')
        self.card._start()
        self.assertEqual(self.card._status.text, 'ERROR: missing neo')
        self.assertIsNone(self.card._proc[0])
        self.spawn.side_effect = None
        self.card._start()
        self.assertIs(self.card._proc[0], self.spawn.return_value)


if __name__ == '__main__':
    unittest.main()
