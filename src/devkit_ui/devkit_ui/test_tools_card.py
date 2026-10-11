"""Diagnostic tool callbacks with process creation and package lookup isolated."""

# pylint: disable=protected-access
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

from devkit_ui import process_registry
from devkit_ui.test_run_ui import fake_ui, nicegui

packages = ModuleType('ament_index_python.packages')
packages.PackageNotFoundError = type('PackageNotFoundError', (LookupError,), {})
packages.get_package_share_directory = Mock()
with (
    patch.dict(sys.modules, {'nicegui': nicegui, 'ament_index_python.packages': packages}),
    patch.object(nicegui, 'run', SimpleNamespace(), create=True),
):
    from devkit_ui.pages.system import tools_card


class TestToolsCard(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        self.enterContext(patch.dict(process_registry._TOOLS, clear=True))
        self.enterContext(patch.object(tools_card, 'ui', fake_ui))
        self.spawn = self.enterContext(patch.object(tools_card, 'spawn_logged'))
        self.spawn.return_value = Mock(pid=101, poll=Mock(return_value=None))
        self.report = self.enterContext(patch.object(tools_card, 'report_if_exited'))
        self.mkdir = self.enterContext(patch.object(tools_card.os, 'makedirs'))
        self.package = self.enterContext(
            patch.object(tools_card, 'get_package_share_directory', return_value='/ros/topo')
        )
        self.io_bound = AsyncMock()
        self.enterContext(
            patch.object(tools_card, 'ng_run', SimpleNamespace(io_bound=self.io_bound))
        )

    async def test_graph_explorer_start_reuses_process_across_sessions_and_stop_releases_it(self):
        tools_card._graph_explorer('robot.local')
        fake_ui.find('button', 'Start Graph Explorer').on_click()
        self.spawn.assert_called_once_with(
            ['ros2', 'run', 'ros2graph_explorer', 'ros2graph_explorer'],
            '/tmp/ros2graph_explorer.log',
        )
        handle = process_registry.shared('explorer', list)
        fake_ui.reset()
        tools_card._graph_explorer('robot.local')
        self.assertEqual(fake_ui.find('label').text, 'running (pid 101)')
        fake_ui.find('button', 'Start Graph Explorer').on_click()
        self.spawn.assert_called_once()
        await fake_ui.find('button', 'Stop').on_click()
        self.io_bound.assert_awaited_once_with(tools_card.kill_group, self.spawn.return_value)
        self.assertEqual(handle, [None])
        self.assertEqual(fake_ui.find('label').text, 'stopped')

    def test_grapher_serves_only_private_output_directory(self):
        tools_card._grapher('robot.local')
        fake_ui.find('button', 'Start ros2grapher').on_click()
        self.mkdir.assert_called_once_with('/tmp/ros2grapher', exist_ok=True)
        self.spawn.assert_called_once_with(
            ['ros2grapher', '/workspace', '-o', '/tmp/ros2grapher/index.html', '--port', '8888'],
            '/tmp/ros2grapher.log',
            cwd='/tmp/ros2grapher',
        )
        self.report.assert_called_once_with(
            self.spawn.return_value, fake_ui.find('label'), '/tmp/ros2grapher.log'
        )
        fake_ui.find('button', 'Start ros2grapher').on_click()
        self.spawn.assert_called_once()

    async def test_grapher_stop_offloads_cleanup_and_allows_restart(self):
        tools_card._grapher('robot.local')
        start = fake_ui.find('button', 'Start ros2grapher').on_click
        start()
        await fake_ui.find('button', 'Stop').on_click()
        self.io_bound.assert_awaited_once_with(tools_card.kill_group, self.spawn.return_value)
        self.assertEqual(process_registry.shared('grapher', list), [None])
        start()
        self.assertEqual(self.spawn.call_count, 2)

    def test_failed_directory_creation_never_starts_grapher(self):
        self.mkdir.side_effect = PermissionError('private directory denied')
        tools_card._grapher('robot.local')
        fake_ui.find('button', 'Start ros2grapher').on_click()
        self.spawn.assert_not_called()
        self.report.assert_not_called()
        self.assertEqual(fake_ui.find('label').text, 'ERROR: private directory denied')

    async def test_rviz_uses_topology_configuration_virtual_display_and_cleanup(self):
        tools_card._rviz('robot.local')
        await fake_ui.find('button', 'Launch RViz').on_click()
        daemons = process_registry.shared('rviz_daemons', list)
        self.package.assert_called_once_with('topological_navigation')
        self.io_bound.assert_awaited_once_with(
            tools_card.start_vnc_stack, ':98', 5901, 6081, daemons
        )
        self.assertEqual(
            self.spawn.call_args.args,
            (
                [
                    'ros2',
                    'run',
                    'rviz2',
                    'rviz2',
                    '-d',
                    '/ros/topo/rviz/topological_navigation.rviz',
                ],
                '/tmp/rviz.log',
            ),
        )
        self.assertEqual(self.spawn.call_args.kwargs['env']['DISPLAY'], ':98')
        await fake_ui.find('button', 'Launch RViz').on_click()
        self.spawn.assert_called_once()
        await fake_ui.find('button', 'Stop RViz').on_click()
        self.assertEqual(
            self.io_bound.await_args_list[-2:],
            [
                call(tools_card.kill_group, self.spawn.return_value),
                call(tools_card.stop_all, daemons),
            ],
        )
        self.assertEqual(process_registry.shared('rviz', list), [None])

    async def test_rviz_without_topology_package_launches_without_configuration(self):
        self.package.side_effect = tools_card.PackageNotFoundError('not installed')
        tools_card._rviz('robot.local')
        await fake_ui.find('button', 'Launch RViz').on_click()
        self.assertEqual(self.spawn.call_args.args[0], ['ros2', 'run', 'rviz2', 'rviz2'])
        self.assertIn('no topo config found', fake_ui.find('label').text)

    async def test_rviz_display_failure_prevents_process_creation(self):
        self.io_bound.side_effect = OSError('no display')
        tools_card._rviz('robot.local')
        await fake_ui.find('button', 'Launch RViz').on_click()
        self.spawn.assert_not_called()
        self.report.assert_not_called()
        self.assertEqual(fake_ui.find('label').text, 'ERROR: no display')
        self.assertEqual(process_registry.shared('rviz', list), [None])

    def test_graph_tool_spawn_failures_report_error_without_registering_process(self):
        for build, button, key in (
            (tools_card._graph_explorer, 'Start Graph Explorer', 'explorer'),
            (tools_card._grapher, 'Start ros2grapher', 'grapher'),
        ):
            with self.subTest(tool=key):
                fake_ui.reset()
                self.spawn.side_effect = OSError('missing executable')
                build('robot.local')
                fake_ui.find('button', button).on_click()
                self.assertEqual(fake_ui.find('label').text, 'ERROR: missing executable')
                self.assertEqual(fake_ui.find('label').styles[-1], 'color:#cf222e')
                self.assertEqual(process_registry.shared(key, list), [None])
                self.report.assert_not_called()


if __name__ == '__main__':
    unittest.main()
