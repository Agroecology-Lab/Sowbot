"""Planning, plan import and write behavior of the Plan tab's application service and view model."""
# PlanHarness supplies the services and fixes normally provided by the ROS node.
# pylint: disable=missing-docstring,no-member,protected-access
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from devkit_ui.application_services import plan_application_service as plan_module
from devkit_ui.models import TopoDoc
from devkit_ui.plan_types import PlanRequest
from devkit_ui.test_f2c_row_metadata import SERVICE_LOGGER, make_node, patch_row_geometry
from devkit_ui.test_plan_import import ROW0, ROW1, _feat, _plan

CORNERS = [(51.0, -2.0), (51.003, -2.0), (51.0, -1.999)]
SWATHS = [[(51.0, -2.0), (51.001, -2.0)], [(51.001, -1.999), (51.0, -1.999)]]


class TestPlan(unittest.TestCase):
    def setUp(self) -> None:
        self.node = make_node([])
        self.run_f2c = self.enterContext(
            patch.object(plan_module, 'run_f2c', Mock(return_value=SWATHS)))
        self.contour = self.enterContext(
            patch.object(plan_module, 'plan_contour_rows', Mock(return_value=None)))

    def test_fewer_than_three_corners_plan_nothing(self) -> None:
        for corners in ([], CORNERS[:2]):
            with self.subTest(corners=len(corners)):
                outcome = self.node.vm.plan(PlanRequest(corners_ll=corners))

                self.assertFalse(outcome.ok)
                self.assertEqual(outcome.status, 'Need at least 3 corners')
                self.run_f2c.assert_not_called()
        self.assertEqual(self.node.vm.swaths, [])

    def test_straight_plan_runs_planner_and_keeps_swaths_anchored_at_first_corner(self) -> None:
        request = PlanRequest(
            corners_ll=CORNERS, obstacle_rings=[[(1.0, 2.0)]], tool_width=2.5, angle_deg=30,
            headland_m=1.5, snake=False, obstacle_pad_m=0.4)

        outcome = self.node.vm.plan(request)

        self.run_f2c.assert_called_once_with(CORNERS, [[(1.0, 2.0)]], 2.5, 30, 0.4, 1.5, False)
        self.contour.assert_not_called()
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.swaths, SWATHS)
        self.assertEqual(
            outcome.status, '2 rows · 2.5m wide · 30° · 1.5m headland · 1 obs avoided')
        self.assertEqual(self.node.vm.swaths, SWATHS)
        planned = self.node.service._planned
        self.assertEqual(planned.origin_ll, CORNERS[0])
        self.assertFalse(planned.contour_used)
        self.assertEqual(planned.break_after, frozenset())

    def test_snake_is_reported_and_angle_is_rounded(self) -> None:
        outcome = self.node.vm.plan(PlanRequest(corners_ll=CORNERS, angle_deg=12.6))

        self.assertEqual(outcome.status, '2 rows · 1.2m wide · 13° · snake')

    def test_contour_plan_keeps_fragment_breaks_and_ignores_angle(self) -> None:
        def contour_rows(*_args, break_after):
            break_after.add(0)
            return SWATHS

        self.contour.side_effect = contour_rows

        outcome = self.node.vm.plan(PlanRequest(
            corners_ll=CORNERS, angle_deg=45, contour=True, recon_path='  log.csv ', dem_res=2.0))

        self.assertEqual(self.contour.call_args.args[6:], ('log.csv', 2.0))
        self.run_f2c.assert_not_called()
        self.assertEqual(outcome.status, '2 rows · 1.2m wide · snake · contour rows')
        planned = self.node.service._planned
        self.assertTrue(planned.contour_used)
        self.assertEqual(planned.break_after, {0})

    def test_blank_recon_path_uses_the_default_log(self) -> None:
        self.node.vm.plan(PlanRequest(corners_ll=CORNERS, contour=True, recon_path='  '))

        self.assertEqual(self.contour.call_args.args[6], plan_module.DEFAULT_RECON_PATH)

    def test_flat_field_falls_back_to_straight_rows(self) -> None:
        outcome = self.node.vm.plan(PlanRequest(corners_ll=CORNERS, contour=True))

        self.run_f2c.assert_called_once()
        self.assertTrue(outcome.ok)
        self.assertEqual(
            outcome.status, '2 rows · 1.2m wide · 0° · snake · flat field, straight swaths used')
        self.assertFalse(self.node.service._planned.contour_used)

    def test_expected_failures_report_the_stage_and_keep_the_previous_plan(self) -> None:
        self.node.vm.plan(PlanRequest(corners_ll=CORNERS))
        cases = (
            (False, self.run_f2c, ValueError('bad field'), 'Planning failed: bad field'),
            (True, self.contour, FileNotFoundError('no log'), 'Contour planning failed: no log'),
        )
        for contour, planner, error, status in cases:
            with self.subTest(contour=contour):
                planner.side_effect = error

                outcome = self.node.vm.plan(PlanRequest(corners_ll=CORNERS[:3], contour=contour))

                self.assertEqual((outcome.ok, outcome.status, outcome.swaths), (False, status, []))
                self.assertEqual(self.node.vm.swaths, SWATHS)

    def test_unexpected_failure_is_logged_and_reported(self) -> None:
        self.run_f2c.side_effect = RuntimeError('boom')

        with self.assertLogs(SERVICE_LOGGER, level='ERROR'):
            outcome = self.node.vm.plan(PlanRequest(corners_ll=CORNERS))

        self.assertEqual((outcome.ok, outcome.status), (False, 'ERROR: boom'))
        self.assertEqual(self.node.vm.swaths, [])


class TestSaveWithoutRows(unittest.TestCase):
    def test_saving_before_planning_reports_and_writes_nothing(self) -> None:
        node = make_node([])
        node.service._planned = None

        node.save('crop', 1)

        self.assertEqual(node.save_status, 'ERROR: no rows planned — click Plan Rows first')
        node.topology.persist_and_reload.assert_not_called()

    def test_planned_rows_can_be_saved_straight_after_planning(self) -> None:
        patch_row_geometry(self)
        node = make_node([])
        node.service._planned = None
        with patch.object(plan_module, 'run_f2c', Mock(return_value=SWATHS[:1])):
            node.vm.plan(PlanRequest(corners_ll=CORNERS))

        node.save('crop', 1)

        self.assertEqual({n.name for n in node.doc.nodes}, {'CROP_R1_IN', 'CROP_R1_OUT'})
        self.assertEqual(node.save_status, 'saved 1 rows · CROP · standalone')


class TestImportPlan(unittest.TestCase):
    def setUp(self) -> None:
        self.node = make_node([])
        self.text = _plan([_feat(ROW0, 0, 0), _feat(ROW0, 0, 1), _feat(ROW1, 1)])

    def test_simulation_imports_without_a_gps_check(self) -> None:
        self.node.latest_gps = None

        status = self.node.vm.import_plan(self.text)

        self.assertEqual(
            status, 'imported 3 rows · 1 obstacle splits (not linked); review, then Save as Topo Rows')
        planned = self.node.service._planned
        self.assertEqual(len(planned.swaths), 3)
        self.assertEqual(planned.origin_ll, (51.45, -2.6))
        self.assertEqual(planned.break_after, {0})
        self.assertFalse(planned.contour_used)

    def test_contour_flag_is_kept(self) -> None:
        self.node.vm.import_plan(_plan([_feat(ROW0, 0)], contour=True))

        self.assertTrue(self.node.service._planned.contour_used)

    def test_hardware_needs_a_usable_fix(self) -> None:
        self.node.is_sim = False
        for gps in (None, SimpleNamespace(latitude=0.0, longitude=0.0),
                    SimpleNamespace(latitude=float('nan'), longitude=-2.6)):
            with self.subTest(gps=gps):
                self.node.latest_gps = gps

                status = self.node.vm.import_plan(self.text)

                self.assertEqual(
                    status, 'ERROR: no GPS fix; cannot check the plan is near the robot')
                self.assertEqual(self.node.vm.swaths, [])

    def test_hardware_refuses_a_plan_far_from_the_robot(self) -> None:
        self.node.is_sim = False
        self.node.latest_gps = SimpleNamespace(latitude=40.0, longitude=10.0)

        status = self.node.vm.import_plan(self.text)

        self.assertTrue(status.startswith('ERROR: plan is '), status)
        self.assertEqual(self.node.vm.swaths, [])

    def test_hardware_accepts_a_plan_near_the_robot(self) -> None:
        self.node.is_sim = False
        self.node.latest_gps = SimpleNamespace(latitude=51.45, longitude=-2.6)

        self.assertTrue(self.node.vm.import_plan(self.text).startswith('imported 3 rows'))

    def test_malformed_plan_reports_an_error_and_keeps_the_previous_plan(self) -> None:
        self.node.vm.import_plan(self.text)

        status = self.node.vm.import_plan('not json')

        self.assertTrue(status.startswith('ERROR: not valid JSON'), status)
        self.assertEqual(len(self.node.vm.swaths), 3)


class TestWrites(unittest.TestCase):
    def setUp(self) -> None:
        patch_row_geometry(self)
        self.node = make_node([[(51.0, -2.0), (51.5, -1.5)]])

    def test_failed_write_reports_the_error_and_is_logged(self) -> None:
        self.node.topology.persist_and_reload.side_effect = OSError('disk full')

        with self.assertLogs(SERVICE_LOGGER, level='ERROR'):
            self.node.save('crop', 1)

        self.assertEqual(self.node.save_status, 'ERROR: disk full')

    def test_status_shows_how_the_map_went_live(self) -> None:
        result = Mock()
        result.describe.return_value = 'saved 1 rows · CROP · standalone — live'
        self.node.topology.persist_and_reload.side_effect = None
        self.node.topology.persist_and_reload.return_value = result

        self.node.save('crop', 1)

        result.describe.assert_called_once_with('saved 1 rows · CROP · standalone')
        self.assertEqual(self.node.save_status, 'saved 1 rows · CROP · standalone — live')

    def test_default_background_runs_the_write_on_another_thread(self) -> None:
        done = threading.Event()
        threads = []

        def work() -> None:
            threads.append(threading.current_thread())
            done.set()

        plan_module._start_thread(work)

        self.assertTrue(done.wait(5))
        self.assertIsNot(threads[0], threading.current_thread())
        self.assertTrue(threads[0].daemon)


class TestViewModel(unittest.TestCase):
    def test_repair_rejects_an_unknown_base_node_without_repairing(self) -> None:
        node = make_node([])
        node.service.repair_connectivity = Mock()

        node.repair('NOPE')

        self.assertEqual(node.save_status, "ERROR: base node 'NOPE' not in map")
        node.service.repair_connectivity.assert_not_called()

    def test_repair_passes_the_row_action_and_base_node_on(self) -> None:
        node = make_node([])
        node.row_action = 'limbic_row_follow'
        node.doc = TopoDoc(name='field')
        node.service.repair_connectivity = Mock()

        node.repair(None)

        node.service.repair_connectivity.assert_called_once_with('limbic_row_follow', None)

    def test_save_uses_the_current_row_action_and_selection(self) -> None:
        node = make_node([])
        node.service.save_rows = Mock()
        node.row_action = 'row_traversal'
        node.topo_vm.current_node = 'HERE'
        node.topo_vm.selected_node = 'THERE'

        node.save('crop', 4, overwrite=True)

        node.service.save_rows.assert_called_once_with(
            'crop', 4, True, row_action='row_traversal', current_node='HERE',
            selected_node='THERE')


if __name__ == '__main__':
    unittest.main()
