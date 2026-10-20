"""Gazebo and row-follow cards: the buttons call the process view model and show its status."""

# pylint: disable=protected-access
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from test_run_ui import fake_ui, nicegui

with (
    patch.dict(sys.modules, {'nicegui': nicegui}),
    patch.object(nicegui, 'run', SimpleNamespace(), create=True),
):
    # pylint: disable-next=ungrouped-imports  # UI imports require the NiceGUI patch.
    from devkit_ui.pages.sim import gazebo_card, row_follow_card
    from devkit_ui.pages.sim.plant_placement_card import PlantSettings
    from devkit_ui.view_models.system_view_model import ToolStatusView

_OK, _ERROR, _MUTED = 'color:#1a7f37', 'color:#cf222e', 'color:#57606a'


class TestGazeboCard(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        """Build a Gazebo card with isolated UI state and view model doubles."""
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        self.enterContext(patch.object(gazebo_card, 'request_host', return_value='robot.local'))
        self.io_bound = AsyncMock(side_effect=lambda fn, *args: fn(*args))
        self.enterContext(
            patch.object(gazebo_card, 'ng_run', SimpleNamespace(io_bound=self.io_bound))
        )
        self.vm = Mock()
        self.process_vm = Mock()
        self.process_vm.gazebo_status.return_value = None
        self.process_vm.gazebo_running.return_value = False
        self.process_vm.launch_gazebo.return_value = ToolStatusView('launched', 'ok')
        self.settings = Mock(return_value=PlantSettings(0.45, 0.8, 1.5, 'crop', 'maize', 0))
        self.card = gazebo_card.GazeboCard(self.vm, self.process_vm, self.settings)

    def test_page_build_shows_a_running_sim(self):
        """Verify a reloaded page shows the sim that is already running."""
        self.process_vm.gazebo_status.return_value = ToolStatusView('running (pid 5)', 'ok')
        card = gazebo_card.GazeboCard(self.vm, self.process_vm, self.settings)
        self.assertEqual(card._status.text, 'running (pid 5)')
        self.assertEqual(card._status.styles[-1], _OK)

    async def test_each_launch_button_passes_its_mode_and_the_selected_robot(self):
        """Verify the three launch buttons select the mode and the chosen robot's xacro."""
        fake_ui.find('toggle').kwargs['on_change'](SimpleNamespace(value='caatinga (diff drive)'))
        for label, mode in (('Launch Sim', 'normal'), ('Launch Sim (headless)', 'headless'),
                            ('Launch Sim (browser)', 'browser')):
            with self.subTest(button=label):
                self.process_vm.launch_gazebo.reset_mock()
                await fake_ui.find('button', label).on_click()
                self.process_vm.launch_gazebo.assert_called_once_with(
                    'robo_caatinga.urdf.xacro', mode)

    async def test_all_robot_choices_map_to_their_xacro(self):
        """Verify each robot selection maps to its expected simulation xacro."""
        for label, xacro in (
            ('sowbot (tracked)', 'sowbot_01.xacro'),
            ('caatinga (diff drive)', 'robo_caatinga.urdf.xacro'),
            ('ifarmate (diff drive)', 'ifarmate.urdf.xacro'),
        ):
            with self.subTest(label=label):
                self.card._select_model(SimpleNamespace(value=label))
                await self.card._launch()
                self.process_vm.launch_gazebo.assert_called_with(xacro, 'normal')

    async def test_launch_runs_off_the_ui_loop_and_shows_the_status(self):
        """Verify the launch is offloaded and its status and colour are shown."""
        await self.card._launch('browser')
        self.io_bound.assert_awaited_once_with(
            self.process_vm.launch_gazebo, 'sowbot_01.xacro', 'browser')
        self.assertEqual(self.card._status.text, 'launched')
        self.assertEqual(self.card._status.styles[-1], _OK)

    async def test_failed_launch_shows_the_error_colour(self):
        """Verify an error status is shown in the error colour."""
        self.process_vm.launch_gazebo.return_value = ToolStatusView('ERROR: launch missing', 'error')
        await self.card._launch()
        self.assertEqual(self.card._status.text, 'ERROR: launch missing')
        self.assertEqual(self.card._status.styles[-1], _ERROR)

    async def test_ignored_launch_leaves_the_status_alone(self):
        """Verify a launch the view model has nothing to say about keeps the label."""
        self.card._status.set_text('last status')
        self.process_vm.launch_gazebo.return_value = None
        await self.card._launch()
        self.assertEqual(self.card._status.text, 'last status')

    async def test_stop_is_offloaded_and_shows_the_status(self):
        """Verify stop runs off the UI loop and shows what it reports."""
        self.process_vm.stop_gazebo.return_value = ToolStatusView('stopped', 'muted')
        await self.card._stop()
        self.io_bound.assert_awaited_once_with(self.process_vm.stop_gazebo)
        self.assertEqual(self.card._status.text, 'stopped')
        self.assertEqual(self.card._status.styles[-1], _MUTED)

    async def test_live_sim_blocks_world_rebuild(self):
        """Verify an active simulation prevents world rebuilding."""
        self.process_vm.gazebo_running.return_value = True
        await self.card._rebuild_world()
        self.io_bound.assert_not_awaited()
        self.settings.assert_not_called()
        self.assertIn('stop the sim before rebuilding', self.card._status.text)

    async def test_rebuild_uses_current_settings_and_reports_zero_density(self):
        """Verify rebuilding forwards current plant settings and displays zero weed density."""
        self.io_bound.side_effect = None
        self.io_bound.return_value = '7'
        await fake_ui.find('button', 'Rebuild World from Map').on_click()
        self.io_bound.assert_awaited_once_with(
            self.vm.rebuild_world, 0.45, 0.8, 1.5, 'crop', 'maize', 0
        )
        self.assertIn('spacing=0.45m, row=0.80m, scale=1.50 on crop', self.card._status.text)
        self.assertIn('weed density=0% (7 weeds), model=maize', self.card._status.text)
        self.assertEqual(self.card._status.styles[-1], _OK)

    async def test_rebuild_cancellation_and_errors_are_not_reported_as_success(self):
        """Verify cancelled and failed rebuilds display the appropriate failure status."""
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
                self.assertEqual(self.card._status.styles[-1], _ERROR)


class TestRowFollowCard(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        """Build a row-follow card with isolated UI state and a view model double."""
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        self.io_bound = AsyncMock(side_effect=lambda fn, *args: fn(*args))
        self.enterContext(
            patch.object(row_follow_card, 'ng_run', SimpleNamespace(io_bound=self.io_bound))
        )
        self.vm = Mock()
        self.vm.row_follow_status.return_value = None
        self.card = row_follow_card.RowFollowCard(self.vm)

    def test_page_build_shows_a_running_stack(self):
        """Verify a reloaded page shows the row-follow process that is already running."""
        self.vm.row_follow_status.return_value = ToolStatusView('running (pid 8)', 'ok')
        card = row_follow_card.RowFollowCard(self.vm)
        self.assertEqual(card._status.text, 'running (pid 8)')

    async def test_start_and_stop_are_offloaded_and_show_the_status(self):
        """Verify the buttons call the view model off the UI loop and show what it reports."""
        self.vm.start_row_follow.return_value = ToolStatusView('running — pid 7', 'ok')
        self.vm.stop_row_follow.return_value = ToolStatusView('stopped', 'muted')
        await fake_ui.find('button', 'Start').on_click()
        self.io_bound.assert_awaited_with(self.vm.start_row_follow)
        self.assertEqual((self.card._status.text, self.card._status.styles[-1]),
                         ('running — pid 7', _OK))
        await fake_ui.find('button', 'Stop').on_click()
        self.io_bound.assert_awaited_with(self.vm.stop_row_follow)
        self.assertEqual((self.card._status.text, self.card._status.styles[-1]),
                         ('stopped', _MUTED))

    async def test_failed_start_shows_the_error(self):
        """Verify an error status is shown in the error colour."""
        self.vm.start_row_follow.return_value = ToolStatusView('ERROR: missing neo', 'error')
        await self.card._start()
        self.assertEqual((self.card._status.text, self.card._status.styles[-1]),
                         ('ERROR: missing neo', _ERROR))


if __name__ == '__main__':
    unittest.main()
