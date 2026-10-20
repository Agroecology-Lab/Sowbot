"""System view model: status wording and levels for each service result."""

import sys
import unittest
from types import ModuleType
from unittest.mock import Mock, patch

from devkit_ui.constants import TOOL_EXPLORER, TOOL_GRAPHER, TOOL_MEDKIT, TOOL_RVIZ
from devkit_ui.view_models.system_view_model import SystemViewModel, ToolStatusView

packages = ModuleType('ament_index_python.packages')
packages.PackageNotFoundError = type('PackageNotFoundError', (LookupError,), {})
packages.get_package_share_directory = Mock()
with patch.dict(sys.modules, {'ament_index_python.packages': packages}):
    from devkit_ui.application_services.system_application_service import ToolState


class TestSystemViewModel(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.vm = SystemViewModel(self.service)

    def test_started_wording_per_tool(self):
        cases = (
            (TOOL_EXPLORER, ToolState('started', 7, '/tmp/ros2graph_explorer.log'),
             'started (pid 7) - log: /tmp/ros2graph_explorer.log'),
            (TOOL_GRAPHER, ToolState('started', 7, '/tmp/ros2grapher.log'),
             'started (pid 7) - open http://robot.local:8888 (scan takes a few seconds)'
             ' - log: /tmp/ros2grapher.log'),
            (TOOL_RVIZ, ToolState('started', 7, '/tmp/rviz.log'), 'started (pid 7) - log: /tmp/rviz.log'),
            (TOOL_RVIZ, ToolState('started', 7, '/tmp/rviz.log', 'no topo config found'),
             'started (pid 7) (no topo config found) - log: /tmp/rviz.log'),
            (TOOL_MEDKIT, ToolState('started', 7), 'started (pid 7)'),
        )
        for tool, state, text in cases:
            with self.subTest(tool=tool, detail=state.detail):
                self.service.start.return_value = state
                self.assertEqual(self.vm.start(tool, 'robot.local'), ToolStatusView(text, 'ok'))
                self.service.start.assert_called_with(tool)

    def test_other_results(self):
        cases = (
            (ToolState('already_running', 7), ToolStatusView('already running', None)),
            (ToolState('running', 7), ToolStatusView('running (pid 7)', 'ok')),
            (ToolState('stopped'), ToolStatusView('stopped', 'muted')),
            (ToolState('exited', 7, '/tmp/rviz.log', '1'),
             ToolStatusView('exited (1) - see /tmp/rviz.log', 'error')),
            (ToolState('exited', 7, '', '-15'), ToolStatusView('exited (-15)', 'error')),
            (ToolState('error', detail='no display'), ToolStatusView('ERROR: no display', 'error')),
            (ToolState('busy'), None),
            (ToolState('idle'), None),
        )
        for state, view in cases:
            with self.subTest(kind=state.kind, detail=state.detail):
                self.service.start.return_value = state
                self.assertEqual(self.vm.start(TOOL_RVIZ, 'h'), view)

    def test_stop_and_status_delegate_and_word_the_result(self):
        self.service.stop.return_value = ToolState('stopped')
        self.service.status.return_value = ToolState('running', 9)
        self.assertEqual(self.vm.stop(TOOL_MEDKIT), ToolStatusView('stopped', 'muted'))
        self.service.stop.assert_called_once_with(TOOL_MEDKIT)
        self.assertEqual(self.vm.status(TOOL_MEDKIT), ToolStatusView('running (pid 9)', 'ok'))
        self.service.status.assert_called_once_with(TOOL_MEDKIT)
        self.service.stop.return_value = ToolState('busy')
        self.service.status.return_value = ToolState('idle')
        self.assertIsNone(self.vm.stop(TOOL_MEDKIT))
        self.assertIsNone(self.vm.status(TOOL_MEDKIT))

    def test_exited_only_reports_a_process_that_has_died(self):
        self.service.exited.return_value = ToolState('exited', 7, '/tmp/rviz.log', '2')
        self.assertEqual(self.vm.exited(TOOL_RVIZ),
                         ToolStatusView('exited (2) - see /tmp/rviz.log', 'error'))
        self.service.exited.assert_called_once_with(TOOL_RVIZ)
        for kind in ('running', 'idle', 'busy'):
            with self.subTest(kind=kind):
                self.service.exited.return_value = ToolState(kind, 7)
                self.assertIsNone(self.vm.exited(TOOL_RVIZ))

    def test_stopping_leaves_the_colour_alone(self):
        self.assertEqual(self.vm.stopping(), ToolStatusView('stopping', None))


if __name__ == '__main__':
    unittest.main()
