"""Tools card: labels and buttons over a view model double; no process control lives here."""

# pylint: disable=protected-access
import asyncio
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from devkit_ui.constants import TOOL_EXPLORER, TOOL_GRAPHER, TOOL_MEDKIT, TOOL_RVIZ
from devkit_ui.view_models.system_view_model import RowPlanDownload, ToolStatusView
from test_run_ui import fake_ui, nicegui

with (
    patch.dict(sys.modules, {'nicegui': nicegui}),
    patch.object(nicegui, 'run', SimpleNamespace(), create=True),
):
    # pylint: disable-next=ungrouped-imports  # UI imports require the NiceGUI patch.
    from devkit_ui.pages.system import tools_card


async def run_inline(func, *args):
    """Stand in for ng_run.io_bound: call func directly."""
    return func(*args)


class TestToolControls(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        self.enterContext(patch.object(tools_card, 'ui', fake_ui))
        self.io_bound = Mock(side_effect=run_inline)
        self.enterContext(patch.object(tools_card, 'ng_run', SimpleNamespace(io_bound=self.io_bound)))
        self.vm = Mock()
        self.vm.status.return_value = None
        self.vm.exited.return_value = None
        self.vm.stopping.return_value = ToolStatusView('stopping')

    def build(self, tool=TOOL_EXPLORER, start='Start', stop='Stop'):
        tools_card._tool_controls(self.vm, tool, start, stop, 'robot.local')
        return fake_ui.find('label'), fake_ui.find('button', start), fake_ui.find('button', stop)

    def test_label_starts_muted_and_empty_for_a_tool_that_is_not_running(self):
        label, _, _ = self.build()
        self.assertEqual((label.text, label.styles), ('', ['color:#57606a']))
        self.vm.status.assert_called_once_with(TOOL_EXPLORER)

    def test_running_tool_is_shown_after_a_page_reload(self):
        self.vm.status.return_value = ToolStatusView('running (pid 42)', 'ok')
        label, _, _ = self.build()
        self.assertEqual(label.text, 'running (pid 42)')
        self.assertEqual(label.styles[-1], 'color:#1a7f37')

    async def test_start_runs_off_the_loop_shows_the_result_and_schedules_an_exit_check(self):
        self.vm.start.return_value = ToolStatusView('started (pid 7)', 'ok')
        label, start, _ = self.build(TOOL_RVIZ)
        await start.on_click()
        self.io_bound.assert_called_once_with(self.vm.start, TOOL_RVIZ, 'robot.local')
        self.assertEqual(label.text, 'started (pid 7)')
        self.assertEqual(label.styles[-1], 'color:#1a7f37')
        self.assertEqual(len(fake_ui.timers), 1)
        timer = fake_ui.timers[0]
        self.assertEqual((timer.interval, timer.once), (2.0, True))
        self.vm.exited.return_value = ToolStatusView('exited (1) - see /tmp/rviz.log', 'error')
        timer.callback()
        self.vm.exited.assert_called_once_with(TOOL_RVIZ)
        self.assertEqual(label.text, 'exited (1) - see /tmp/rviz.log')
        self.assertEqual(label.styles[-1], 'color:#cf222e')

    async def test_exit_check_leaves_the_label_alone_while_the_tool_lives(self):
        self.vm.start.return_value = ToolStatusView('started (pid 7)', 'ok')
        label, start, _ = self.build()
        await start.on_click()
        fake_ui.timers[0].callback()
        self.assertEqual(label.text, 'started (pid 7)')

    async def test_start_that_the_view_model_ignores_changes_nothing(self):
        self.vm.start.return_value = None
        label, start, _ = self.build()
        await start.on_click()
        self.assertEqual((label.text, label.styles, fake_ui.timers), ('', ['color:#57606a'], []))

    async def test_a_level_of_none_keeps_the_current_colour(self):
        self.vm.start.return_value = ToolStatusView('already running')
        label, start, _ = self.build()
        await start.on_click()
        self.assertEqual((label.text, label.styles), ('already running', ['color:#57606a']))

    async def test_stop_shows_the_interim_label_then_the_result(self):
        seen = []

        def stop(tool):
            seen.append((tool, label.text))
            return ToolStatusView('stopped', 'muted')

        self.vm.stop.side_effect = stop
        label, _, stop_button = self.build(TOOL_MEDKIT, 'Start Medkit Gateway', 'Stop Medkit Gateway')
        await stop_button.on_click()
        self.assertEqual(seen, [(TOOL_MEDKIT, 'stopping')])
        self.io_bound.assert_called_once_with(self.vm.stop, TOOL_MEDKIT)
        self.assertEqual(label.text, 'stopped')
        self.assertEqual(label.styles[-1], 'color:#57606a')

    async def test_ignored_stop_puts_the_previous_label_back(self):
        self.vm.start.return_value = ToolStatusView('started (pid 7)', 'ok')
        self.vm.stop.return_value = None
        label, start, stop = self.build()
        await start.on_click()
        await stop.on_click()
        self.assertEqual(label.text, 'started (pid 7)')

    async def test_a_stop_overlapping_a_slow_stop_does_not_lose_the_interim_label(self):
        release = asyncio.Event()

        async def slow_stop(_func, _tool):
            await release.wait()
            return ToolStatusView('stopped', 'muted')

        self.io_bound.side_effect = None
        self.io_bound.side_effect = [slow_stop(None, None), run_inline(lambda: None)]
        label, _, stop = self.build()
        first = asyncio.ensure_future(stop.on_click())
        await asyncio.sleep(0)
        self.assertEqual(label.text, 'stopping')
        await stop.on_click()  # ignored by the view model: busy
        self.assertEqual(label.text, 'stopping')
        release.set()
        await first
        self.assertEqual(label.text, 'stopped')

    async def test_failed_stop_shows_the_error(self):
        self.vm.stop.return_value = ToolStatusView('ERROR: cannot signal', 'error')
        label, _, stop = self.build()
        await stop.on_click()
        self.assertEqual((label.text, label.styles[-1]), ('ERROR: cannot signal', 'color:#cf222e'))


class TestToolsCardLinks(unittest.TestCase):
    def setUp(self):
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        self.enterContext(patch.object(tools_card, 'ui', fake_ui))
        self.enterContext(patch.object(tools_card, 'ng_run', SimpleNamespace(io_bound=AsyncMock())))
        self.vm = Mock()
        self.vm.status.return_value = None

    def html(self, host):
        fake_ui.reset()
        with patch.object(tools_card, 'request_host', return_value=host):
            tools_card.ToolsCard(self.vm)
        return '\n'.join(e.text for e in fake_ui.elements if e.kind == 'html')

    def test_every_tool_is_wired_to_its_own_key(self):
        self.html('robot.local')
        self.assertEqual([c.args[0] for c in self.vm.status.call_args_list],
                         [TOOL_EXPLORER, TOOL_GRAPHER, TOOL_RVIZ, TOOL_MEDKIT])

    def test_links_use_the_page_host(self):
        html = self.html('robot.local')
        for url in ('http://robot.local:8734/', 'http://robot.local:8888/',
                    'http://robot.local:6081/vnc.html', 'http://robot.local:8080/'):
            self.assertIn(f'href="{url}"', html)

    def test_gateway_and_sovd_links_use_page_host(self):
        for authority in ('robot.local', '192.0.2.10', 'localhost', '[2001:db8::1]'):
            with self.subTest(authority=authority):
                html = self.html(authority)
                self.assertIn(f'href="http://{authority}:8080/"', html)
                self.assertIn(f'href="http://{authority}:3000/"', html)
                self.assertIn(f'<code>http://{authority}:8080</code>', html)
                self.assertIn('docker run -p 3000:80 ghcr.io/selfpatch/sovd_web_ui:latest', html)
                self.assertIn('authentication, TLS, and restricted network access', html)

    def test_page_does_not_import_process_control(self):
        self.assertFalse(hasattr(tools_card, 'subprocess'))
        self.assertFalse(hasattr(tools_card, 'spawn_logged'))


class TestLiteFarmExport(unittest.TestCase):
    def setUp(self):
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        self.enterContext(patch.object(tools_card, 'ui', fake_ui))
        self.download = Mock()
        self.enterContext(patch.object(fake_ui, 'download', self.download, create=True))
        self.vm = Mock()

    def build(self):
        tools_card._litefarm_export(self.vm)
        return fake_ui.find('label'), fake_ui.find('button', 'LiteFarm GeoJSON export')

    def test_label_starts_muted_and_empty_and_nothing_is_exported_yet(self):
        label, _ = self.build()
        self.assertEqual((label.text, label.styles), ('', ['color:#57606a']))
        self.vm.export_row_plan.assert_not_called()
        self.download.assert_not_called()

    def test_click_downloads_the_file_and_shows_the_summary(self):
        self.vm.export_row_plan.return_value = RowPlanDownload(
            ToolStatusView('4 rows, 15.9 m, maize-1', 'ok'), 'maize-1.geojson', b'{}')
        label, button = self.build()
        button.on_click()
        self.download.assert_called_once_with(b'{}', 'maize-1.geojson', 'application/geo+json')
        self.assertEqual(label.text, '4 rows, 15.9 m, maize-1')
        self.assertEqual(label.styles[-1], 'color:#1a7f37')

    def test_a_failed_export_shows_the_reason_and_downloads_nothing(self):
        self.vm.export_row_plan.return_value = RowPlanDownload(ToolStatusView('no map loaded', 'error'))
        label, button = self.build()
        button.on_click()
        self.download.assert_not_called()
        self.assertEqual((label.text, label.styles[-1]), ('no map loaded', 'color:#cf222e'))


if __name__ == '__main__':
    unittest.main()
