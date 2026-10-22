"""Planning, plan import and write behavior of the Plan tab's application service and view model."""
# PlanHarness supplies the services and fixes normally provided by the ROS node.
# pylint: disable=missing-docstring,no-member,protected-access
import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from devkit_ui.application_services import plan_application_service as plan_module
from devkit_ui.models import TopoDoc
from devkit_ui.plan_types import PlanFailure, PlanMode, PlanRequest
from devkit_ui.view_models.plan_view_model import PlanPageState, PlanViewModel
from test_f2c_row_metadata import SERVICE_LOGGER, make_node, patch_row_geometry
from test_plan_import import ROW0, ROW1, _feat, _plan

CORNERS = [(51.0, -2.0), (51.003, -2.0), (51.0, -1.999)]
SWATHS = [[(51.0, -2.0), (51.001, -2.0)], [(51.001, -1.999), (51.0, -1.999)]]


class TestPlan(unittest.TestCase):
    def setUp(self) -> None:
        """Create an empty plan harness with straight and contour planner mocks."""
        self.node = make_node([])
        self.run_f2c = self.enterContext(
            patch.object(plan_module, 'run_f2c', Mock(return_value=SWATHS)))
        self.contour = self.enterContext(
            patch.object(plan_module, 'plan_contour_rows', Mock(return_value=None)))

    def test_fewer_than_three_corners_plan_nothing(self) -> None:
        """Verify incomplete boundaries are rejected before calling the planner."""
        for corners in ([], CORNERS[:2]):
            with self.subTest(corners=len(corners)):
                outcome = self.node.plan(PlanRequest(corners_ll=corners))

                self.assertFalse(outcome.ok)
                self.assertEqual(outcome.status, 'Need at least 3 corners')
                self.assertIsNone(outcome.planned)
                self.run_f2c.assert_not_called()

    def test_straight_plan_runs_planner_and_keeps_swaths_anchored_at_first_corner(self) -> None:
        """Verify straight planning forwards parameters and retains swaths with their origin."""
        request = PlanRequest(
            corners_ll=CORNERS, obstacle_rings=[[(1.0, 2.0)]], tool_width=2.5, angle_deg=30,
            headland_m=1.5, snake=False, obstacle_pad_m=0.4)

        outcome = self.node.plan(request)

        self.run_f2c.assert_called_once_with(CORNERS, [[(1.0, 2.0)]], 2.5, 30, 0.4, 1.5, False)
        self.contour.assert_not_called()
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.swaths, SWATHS)
        self.assertEqual(
            outcome.status, '2 rows · 2.5m wide · 30° · 1.5m headland · 1 obs avoided')
        planned = outcome.planned
        self.assertEqual(planned.swaths, SWATHS)
        self.assertEqual(planned.origin_ll, CORNERS[0])
        self.assertFalse(planned.contour_used)
        self.assertEqual(planned.break_after, frozenset())

    def test_snake_is_reported_and_angle_is_rounded(self) -> None:
        """Verify the status includes snake ordering and the angle rounded to whole degrees."""
        outcome = self.node.plan(PlanRequest(corners_ll=CORNERS, angle_deg=12.6))

        self.assertEqual(outcome.status, '2 rows · 1.2m wide · 13° · snake')

    def test_contour_plan_keeps_fragment_breaks_and_ignores_angle(self) -> None:
        """Verify contour planning retains obstacle splits and omits the straight-row angle."""
        def contour_rows(*_args, break_after):
            """Return canned contour swaths and mark the break after their first fragment."""
            break_after.add(0)
            return SWATHS

        self.contour.side_effect = contour_rows

        outcome = self.node.plan(PlanRequest(
            corners_ll=CORNERS, angle_deg=45, contour=True, recon_path='  log.csv ', dem_res=2.0))

        self.assertEqual(self.contour.call_args.args[6:], ('log.csv', 2.0))
        self.run_f2c.assert_not_called()
        self.assertEqual(outcome.status, '2 rows · 1.2m wide · snake · contour rows')
        planned = outcome.planned
        self.assertTrue(planned.contour_used)
        self.assertEqual(planned.break_after, {0})

    def test_blank_recon_path_uses_the_default_log(self) -> None:
        """Verify a blank reconstruction path selects the default contour elevation log."""
        self.node.plan(PlanRequest(corners_ll=CORNERS, contour=True, recon_path='  '))

        self.assertEqual(self.contour.call_args.args[6], plan_module.DEFAULT_RECON_PATH)

    def test_flat_field_falls_back_to_straight_rows(self) -> None:
        """Verify absent contour swaths trigger straight planning and a fallback status."""
        outcome = self.node.plan(PlanRequest(corners_ll=CORNERS, contour=True))

        self.run_f2c.assert_called_once()
        self.assertTrue(outcome.ok)
        self.assertEqual(
            outcome.status, '2 rows · 1.2m wide · 0° · snake · flat field, straight swaths used')
        self.assertFalse(outcome.planned.contour_used)

    def test_expected_failures_report_the_stage_and_keep_the_previous_plan(self) -> None:
        """Verify expected planner errors identify the failing stage and preserve prior swaths."""
        earlier = self.node.plan(PlanRequest(corners_ll=CORNERS))
        cases = (
            (False, self.run_f2c, ValueError('bad field'), 'Planning failed: bad field'),
            (True, self.contour, FileNotFoundError('no log'), 'Contour planning failed: no log'),
        )
        for contour, planner, error, status in cases:
            with self.subTest(contour=contour):
                planner.side_effect = error

                outcome = self.node.plan(PlanRequest(corners_ll=CORNERS[:3], contour=contour))

                self.assertEqual((outcome.ok, outcome.status, outcome.planned), (False, status, None))
                self.assertEqual(self.node.draft, earlier.planned)
                self.assertEqual(self.node.draft.swaths, SWATHS)

    def test_unexpected_failure_is_logged_and_reported(self) -> None:
        """Verify unexpected planner exceptions are logged and returned as failed outcomes."""
        self.run_f2c.side_effect = RuntimeError('boom')

        with self.assertLogs(SERVICE_LOGGER, level='ERROR'):
            outcome = self.node.plan(PlanRequest(corners_ll=CORNERS))

        self.assertEqual((outcome.ok, outcome.status), (False, 'ERROR: boom'))
        self.assertIsNone(outcome.planned)


class TestPlanOutcome(unittest.TestCase):
    """The service returns plain data and wording is left to the view model."""

    def setUp(self) -> None:
        """Create a plan harness with straight and contour planner mocks."""
        self.service = make_node([]).service
        self.run_f2c = self.enterContext(
            patch.object(plan_module, 'run_f2c', Mock(return_value=SWATHS)))
        self.contour = self.enterContext(
            patch.object(plan_module, 'plan_contour_rows', Mock(return_value=None)))

    def test_mode_says_how_the_rows_were_made(self) -> None:
        """Verify straight, contour and flat-field fallback runs report their mode."""
        straight = self.service.plan(PlanRequest(corners_ll=CORNERS))
        flat = self.service.plan(PlanRequest(corners_ll=CORNERS, contour=True))
        self.contour.return_value = SWATHS
        contour = self.service.plan(PlanRequest(corners_ll=CORNERS, contour=True))

        self.assertEqual(
            [straight.mode, flat.mode, contour.mode],
            [PlanMode.STRAIGHT, PlanMode.FLAT_FALLBACK, PlanMode.CONTOUR])
        self.assertTrue(all(o.ok and o.failure is None for o in (straight, flat, contour)))

    def test_failures_carry_a_kind_and_the_error_text_not_a_sentence(self) -> None:
        """Verify failed runs report a failure kind and detail, with no rows and no status text."""
        self.assertEqual(self.service.plan(PlanRequest(corners_ll=[])).failure,
                         PlanFailure.TOO_FEW_CORNERS)
        self.run_f2c.side_effect = ValueError('bad field')
        expected = self.service.plan(PlanRequest(corners_ll=CORNERS))
        self.run_f2c.side_effect = RuntimeError('boom')
        with self.assertLogs(SERVICE_LOGGER, level='ERROR'):
            unexpected = self.service.plan(PlanRequest(corners_ll=CORNERS))

        self.assertEqual((expected.failure, expected.detail), (PlanFailure.PLANNING, 'bad field'))
        self.assertEqual((unexpected.failure, unexpected.detail), (PlanFailure.UNEXPECTED, 'boom'))
        self.assertFalse(hasattr(expected, 'status'))
        self.assertIsNone(expected.planned)

    def test_service_keeps_no_draft_between_calls(self) -> None:
        """Verify planning leaves nothing behind on the service for another caller to save."""
        self.service.plan(PlanRequest(corners_ll=CORNERS))

        self.assertFalse(hasattr(self.service, 'swaths'))
        self.assertFalse(hasattr(self.service, '_planned'))


class TestSaveWritesTheRowsItIsGiven(unittest.TestCase):
    def test_a_save_writes_its_own_plan_not_the_latest_one_planned(self) -> None:
        """Verify two sessions planning in turn each save their own rows."""
        patch_row_geometry(self)
        node = make_node([])
        with patch.object(plan_module, 'run_f2c', Mock(return_value=SWATHS[:1])):
            mine = asyncio.run(node.vm.plan(PlanRequest(corners_ll=CORNERS))).planned
        with patch.object(plan_module, 'run_f2c', Mock(return_value=SWATHS)):
            asyncio.run(node.vm.plan(PlanRequest(corners_ll=CORNERS)))

        node.vm.save_rows(mine, 'crop', 1, False, node.page.set_save_status)

        self.assertEqual({n.name for n in node.doc.nodes}, {'CROP_R1_IN', 'CROP_R1_OUT'})


class TestSaveWithoutRows(unittest.TestCase):
    def test_saving_before_planning_reports_and_writes_nothing(self) -> None:
        """Verify saving without planned rows reports an error and skips persistence."""
        node = make_node([])
        node.draft = None

        node.save('crop', 1)

        self.assertEqual(node.save_status, 'ERROR: no rows planned — click Plan Rows first')
        node.topology.persist_and_reload.assert_not_called()

    def test_planned_rows_can_be_saved_straight_after_planning(self) -> None:
        """Verify a successful plan can immediately be saved as topology entry and exit nodes."""
        patch_row_geometry(self)
        node = make_node([])
        node.draft = None
        with patch.object(plan_module, 'run_f2c', Mock(return_value=SWATHS[:1])):
            node.plan(PlanRequest(corners_ll=CORNERS))

        node.save('crop', 1)

        self.assertEqual({n.name for n in node.doc.nodes}, {'CROP_R1_IN', 'CROP_R1_OUT'})
        self.assertEqual(node.save_status, 'saved 1 rows · CROP · standalone')


class TestImportPlan(unittest.TestCase):
    def setUp(self) -> None:
        """Create an empty plan harness and an import containing an obstacle-split row."""
        self.node = make_node([])
        self.text = _plan([_feat(ROW0, 0, 0), _feat(ROW0, 0, 1), _feat(ROW1, 1)])

    def test_simulation_imports_without_a_gps_check(self) -> None:
        """Verify simulation imports preserve origin and split metadata without a GPS fix."""
        self.node.latest_gps = None

        view = self.node.import_plan(self.text)

        self.assertTrue(view.ok)
        self.assertEqual(
            view.status, 'imported 3 rows · 1 obstacle splits (not linked); review, then Save as Topo Rows')
        planned = view.planned
        self.assertEqual(len(planned.swaths), 3)
        self.assertEqual(planned.origin_ll, (51.45, -2.6))
        self.assertEqual(planned.break_after, {0})
        self.assertFalse(planned.contour_used)

    def test_contour_flag_is_kept(self) -> None:
        """Verify importing a contour plan retains its contour flag for saving."""
        view = self.node.import_plan(_plan([_feat(ROW0, 0)], contour=True))

        self.assertTrue(view.planned.contour_used)

    def test_hardware_needs_a_usable_fix(self) -> None:
        """Verify hardware imports reject missing, invalid, zero, and nonfinite GPS fixes."""
        self.node.is_sim = False
        for gps in (None,
                    SimpleNamespace(latitude=51.45, longitude=-2.6,
                                    status=SimpleNamespace(status=-1)),
                    SimpleNamespace(latitude=0.0, longitude=0.0,
                                    status=SimpleNamespace(status=0)),
                    SimpleNamespace(latitude=float('nan'), longitude=-2.6,
                                    status=SimpleNamespace(status=0))):
            with self.subTest(gps=gps):
                self.node.latest_gps = gps

                view = self.node.import_plan(self.text)

                self.assertFalse(view.ok)
                self.assertEqual(
                    view.status, 'ERROR: no GPS fix; cannot check the plan is near the robot')
                self.assertIsNone(view.planned)

    def test_hardware_refuses_a_plan_far_from_the_robot(self) -> None:
        """Verify hardware imports reject plans outside the robot's allowed proximity."""
        self.node.is_sim = False
        self.node.latest_gps = SimpleNamespace(
            latitude=40.0, longitude=10.0, status=SimpleNamespace(status=0))

        view = self.node.import_plan(self.text)

        self.assertFalse(view.ok)
        self.assertTrue(view.status.startswith('ERROR: plan is '), view.status)
        self.assertIsNone(view.planned)

    def test_hardware_accepts_a_plan_near_the_robot(self) -> None:
        """Verify hardware imports accept a valid plan near the current GPS fix."""
        self.node.is_sim = False
        for status in (0, 1, 2):
            with self.subTest(status=status):
                self.node.latest_gps = SimpleNamespace(
                    latitude=51.45, longitude=-2.6, status=SimpleNamespace(status=status))

                self.assertTrue(self.node.import_plan(self.text).status.startswith('imported 3 rows'))

    def test_malformed_plan_reports_an_error_and_keeps_the_previous_plan(self) -> None:
        """Verify invalid plan JSON reports an error and preserves the last imported swaths."""
        self.node.import_plan(self.text)

        view = self.node.import_plan('not json')

        self.assertFalse(view.ok)
        self.assertTrue(view.status.startswith('ERROR: not valid JSON'), view.status)
        self.assertEqual(len(self.node.draft.swaths), 3)


class TestWrites(unittest.TestCase):
    def setUp(self) -> None:
        """Create a plan harness with one swath and deterministic row geometry."""
        patch_row_geometry(self)
        self.node = make_node([[(51.0, -2.0), (51.5, -1.5)]])

    def test_failed_write_reports_the_error_and_is_logged(self) -> None:
        """Verify persistence failures are logged and exposed through the save status."""
        self.node.topology.persist_and_reload.side_effect = OSError('disk full')

        with self.assertLogs(SERVICE_LOGGER, level='ERROR'):
            self.node.save('crop', 1)

        self.assertEqual(self.node.save_status, 'ERROR: disk full')

    def test_status_shows_how_the_map_went_live(self) -> None:
        """Verify the save status uses the persistence result's description of map activation."""
        result = Mock()
        result.describe.return_value = 'saved 1 rows · CROP · standalone — live'
        self.node.topology.persist_and_reload.side_effect = None
        self.node.topology.persist_and_reload.return_value = result

        self.node.save('crop', 1)

        result.describe.assert_called_once_with('saved 1 rows · CROP · standalone')
        self.assertEqual(self.node.save_status, 'saved 1 rows · CROP · standalone — live')

    def test_default_background_runs_the_write_on_another_thread(self) -> None:
        """Verify the default background runner executes work on a separate daemon thread."""
        done = threading.Event()
        threads = []

        def work() -> None:
            """Record the worker thread and signal that it ran."""
            threads.append(threading.current_thread())
            done.set()

        plan_module._start_thread(work)

        self.assertTrue(done.wait(5))
        self.assertIsNot(threads[0], threading.current_thread())
        self.assertTrue(threads[0].daemon)


class TestViewModel(unittest.TestCase):
    def test_repair_rejects_an_unknown_base_node_without_repairing(self) -> None:
        """Verify an unknown base node sets an error before the repair service is called."""
        node = make_node([])
        node.service.repair_connectivity = Mock()

        node.repair('NOPE')

        self.assertEqual(node.save_status, "ERROR: base node 'NOPE' not in map")
        node.service.repair_connectivity.assert_not_called()

    def test_repair_passes_the_row_action_and_base_node_on(self) -> None:
        """Verify repair forwards the selected row action and optional base node."""
        node = make_node([])
        node.row_action = 'limbic_row_follow'
        node.doc = TopoDoc(name='field')
        node.service.repair_connectivity = Mock()

        node.repair(None)

        node.service.repair_connectivity.assert_called_once_with(
            'limbic_row_follow', None, on_status=node.page.set_save_status)

    def test_save_uses_the_current_row_action_and_selection(self) -> None:
        """Verify saving forwards row options, the selected action, and both candidate nodes."""
        node = make_node([])
        node.service.save_rows = Mock()
        node.row_action = 'row_traversal'
        node.topo_vm.current_node = 'HERE'
        node.topo_vm.selected_node = 'THERE'

        node.save('crop', 4, overwrite=True)

        node.service.save_rows.assert_called_once_with(
            node.draft, 'crop', 4, True, row_action='row_traversal', current_node='HERE',
            selected_node='THERE', on_status=node.page.set_save_status)


if __name__ == '__main__':
    unittest.main()


class TestSessionsKeepTheirOwnStatus(unittest.TestCase):
    def test_two_pages_saving_one_service_each_see_only_their_own_status(self) -> None:
        """Verify one session's save or error never appears on another session's page."""
        patch_row_geometry(self)
        node = make_node(SWATHS)
        other = PlanPageState()
        node.queue_writes()

        node.save('crop', 1)
        node.vm.save_rows(None, 'crop', 1, False, other.set_save_status)

        self.assertEqual(other.save_status, 'ERROR: no rows planned — click Plan Rows first')
        self.assertNotEqual(node.save_status, other.save_status)
        self.assertEqual(node.save_status, 'writing 2 rows · CROP · standalone…')

        node.queued.pop()()

        self.assertEqual(node.save_status, 'saved 2 rows · CROP · standalone')
        self.assertEqual(other.save_status, 'ERROR: no rows planned — click Plan Rows first')

    def test_the_service_keeps_no_status_of_its_own(self) -> None:
        """Verify the service holds nothing a second caller could read."""
        node = make_node(SWATHS)

        self.assertFalse(hasattr(node.service, 'save_status'))
        self.assertFalse(hasattr(node.service, '_save_status'))


class TestPlanDoesNotBlockTheCaller(unittest.TestCase):
    def test_planning_runs_off_the_event_loop_thread(self) -> None:
        """Verify an awaiting caller needs no worker thread: the planner runs on another one."""
        threads = []
        service = Mock()
        service.plan.side_effect = lambda _request: (
            threads.append(threading.current_thread()), Mock(ok=False, failure=None))[1]
        vm = PlanViewModel(service, Mock())
        vm._describe_plan = Mock(return_value='x')  # pylint: disable=protected-access

        asyncio.run(vm.plan(PlanRequest(corners_ll=CORNERS)))

        self.assertEqual(len(threads), 1)
        self.assertIsNot(threads[0], threading.main_thread())

    def test_the_event_loop_keeps_running_while_a_plan_is_in_flight(self) -> None:
        """Verify other tasks make progress while the planner is still busy."""
        release = threading.Event()
        service = Mock()
        service.plan.side_effect = lambda _request: (release.wait(5), Mock(ok=False))[1]
        vm = PlanViewModel(service, Mock())
        vm._describe_plan = Mock(return_value='x')  # pylint: disable=protected-access
        ticks = []

        async def scenario() -> None:
            task = asyncio.create_task(vm.plan(PlanRequest(corners_ll=CORNERS)))
            for _ in range(3):
                await asyncio.sleep(0)
                ticks.append(task.done())
            release.set()
            await task

        asyncio.run(scenario())

        self.assertEqual(ticks, [False, False, False])
