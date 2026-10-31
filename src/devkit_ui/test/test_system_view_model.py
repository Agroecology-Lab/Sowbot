"""System view model: status wording and levels for each service result."""

import json
import sys
import unittest
from types import ModuleType
from unittest.mock import Mock, patch

from devkit_ui.constants import TOOL_EXPLORER, TOOL_GRAPHER, TOOL_MEDKIT, TOOL_RVIZ
from devkit_ui.models import TopoDoc, TopoNode, TopoProperties
from devkit_ui.view_models.system_view_model import RowPlanDownload, SystemViewModel, ToolStatusView
from test_row_plan_export import latlon, node, simple_row

packages = ModuleType('ament_index_python.packages')
packages.PackageNotFoundError = type('PackageNotFoundError', (LookupError,), {})
packages.get_package_share_directory = Mock()
with patch.dict(sys.modules, {'ament_index_python.packages': packages}):
    # pylint: disable-next=ungrouped-imports  # needs the ament stub above.
    from devkit_ui.application_services.tool_state import ToolState


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
        # pylint: disable-next=no-member
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


class TestRowPlanExport(unittest.TestCase):
    def vm(self, doc):
        return SystemViewModel(Mock(), Mock(topo_doc=doc))

    def test_the_loaded_map_becomes_a_geojson_download(self):
        result = self.vm(TopoDoc('my map', nodes=[*simple_row(1, 0.0), *simple_row(2, 3.0)])).export_row_plan()
        self.assertEqual(result.status.level, 'ok')
        self.assertRegex(result.status.text, r'^2 rows, 20\.0 m, my_map-[0-9a-f]{12}$')
        self.assertRegex(result.filename, r'^my_map-[0-9a-f]{12}\.geojson$')
        plan = json.loads(result.content)
        self.assertEqual((plan['properties']['source'], len(plan['features'])), ('robot', 2))

    def test_the_file_name_has_no_colons(self):
        doc = TopoDoc('a:b', nodes=simple_row(1, 0.0))
        self.assertRegex(self.vm(doc).export_row_plan().filename, r'^a_b-[0-9a-f]{12}\.geojson$')

    def test_no_map_loaded(self):
        for vm in (self.vm(None), SystemViewModel(Mock())):
            with self.subTest(vm=vm):
                self.assertEqual(vm.export_row_plan(), RowPlanDownload(ToolStatusView('no map loaded', 'error')))

    def test_a_map_that_cannot_be_exported_gives_the_reason_and_no_file(self):
        for nodes, reason in (([], 'no row nodes'),
                              ([node('IN', 1, 'entry', 0, 0)], 'one entry and one exit'),
                              ([node('IN', 1, 'entry', 0, 0, gps=False), node('OUT', 1, 'exit', 10, 0)],
                               'no GPS position on IN')):
            with self.subTest(reason=reason):
                result = self.vm(TopoDoc('f', nodes=nodes)).export_row_plan()
                self.assertEqual(result.status.level, 'error')
                self.assertTrue(result.status.text.startswith('ERROR: '))
                self.assertIn(reason, result.status.text)
                self.assertEqual((result.filename, result.content), (None, None))

    def test_warnings_are_shown_with_the_summary(self):
        entry = simple_row(1, 0.0)[0]
        lat, lon = latlon(60.0, 0.0)  # the map says 10 m, the GPS says 60 m
        far = TopoNode(name='F_R1_OUT', x=10.0, y=0.0,
                       properties=TopoProperties(row_id=1, row_role='exit', gps_lat=lat, gps_lon=lon))
        result = self.vm(TopoDoc('f', nodes=[entry, far])).export_row_plan()
        self.assertEqual(result.status.level, 'ok')
        self.assertIn('warning: row 1: 10.0 m on the map', result.status.text)
        self.assertIsNotNone(result.content)

    def test_the_map_is_read_each_time(self):
        context = Mock(topo_doc=None)
        vm = SystemViewModel(Mock(), context)
        self.assertEqual(vm.export_row_plan().status.text, 'no map loaded')
        context.topo_doc = TopoDoc('f', nodes=simple_row(1, 0.0))
        self.assertEqual(vm.export_row_plan().status.level, 'ok')


if __name__ == '__main__':
    unittest.main()
