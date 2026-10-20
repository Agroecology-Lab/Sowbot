"""Status wording for the Sim page processes."""

import unittest
from unittest.mock import Mock

from devkit_ui.application_services.tool_state import ToolState
from devkit_ui.view_models.sim_process_view_model import SimProcessViewModel
from devkit_ui.view_models.system_view_model import ToolStatusView


class TestSimProcessViewModel(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.vm = SimProcessViewModel(self.service)

    def test_polls_show_only_a_running_process(self):
        """Verify a page build shows a live process and leaves the label alone otherwise."""
        for poll, status in ((self.vm.row_follow_status, self.service.row_follow_status),
                             (self.vm.gazebo_status, self.service.gazebo_status)):
            with self.subTest(poll=poll.__name__):
                status.return_value = ToolState('running', 42)
                self.assertEqual(poll(), ToolStatusView('running (pid 42)', 'ok'))
                status.return_value = ToolState('idle')
                self.assertIsNone(poll())

    def test_row_follow_wording(self):
        """Verify each row-follow outcome has its own text and level."""
        for state, view in (
            (ToolState('started', 7, '/tmp/neo_sim.log'),
             ToolStatusView('running — pid 7 · log: /tmp/neo_sim.log', 'ok')),
            (ToolState('already_running', 7), ToolStatusView('already running', 'muted')),
            (ToolState('error', detail='missing neo'), ToolStatusView('ERROR: missing neo', 'error')),
        ):
            with self.subTest(kind=state.kind):
                self.service.start_row_follow.return_value = state
                self.assertEqual(self.vm.start_row_follow(), view)
        for state, view in ((ToolState('stopped'), ToolStatusView('stopped', 'muted')),
                            (ToolState('idle'), ToolStatusView('not running', 'muted'))):
            with self.subTest(kind=state.kind):
                self.service.stop_row_follow.return_value = state
                self.assertEqual(self.vm.stop_row_follow(), view)

    def test_gazebo_launch_wording_names_the_mode_model_and_pid(self):
        """Verify a started sim names its mode, robot model and pid."""
        for mode, text in (('normal', 'sim launching — sowbot_01.xacro — pid 9'),
                           ('headless', 'sim launching (headless) — sowbot_01.xacro — pid 9'),
                           ('browser', 'browser mode — sowbot_01.xacro — pid 9')):
            with self.subTest(mode=mode):
                self.service.launch_gazebo.return_value = ToolState('started', 9, detail=mode)
                self.assertEqual(
                    self.vm.launch_gazebo('sowbot_01.xacro', mode), ToolStatusView(text, 'ok'))
                self.service.launch_gazebo.assert_called_with('sowbot_01.xacro', mode)

    def test_gazebo_failures_and_stop(self):
        """Verify refused and failed launches and a stop are worded."""
        self.service.launch_gazebo.return_value = ToolState('already_running', 9)
        self.assertEqual(self.vm.launch_gazebo('x'), ToolStatusView('already running', 'muted'))
        self.service.launch_gazebo.return_value = ToolState('error', detail='launch missing')
        self.assertEqual(self.vm.launch_gazebo('x'), ToolStatusView('ERROR: launch missing', 'error'))
        self.service.stop_gazebo.return_value = ToolState('stopped')
        self.assertEqual(self.vm.stop_gazebo(), ToolStatusView('stopped', 'muted'))

    def test_running_flag_comes_from_the_service(self):
        """Verify the rebuild guard reads the service's view of the sim."""
        self.service.gazebo_running.return_value = True
        self.assertTrue(self.vm.gazebo_running())


if __name__ == '__main__':
    unittest.main()
