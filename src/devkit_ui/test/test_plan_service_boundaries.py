"""Boundary and failure cases for the Plan application service, with isolated collaborators."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from devkit_ui import plan_import
from devkit_ui.application_services import plan_application_service as plan_module
from devkit_ui.plan_types import PlanFailure, PlanMode, PlannedRows, PlanRequest
from devkit_ui.topo_results import PersistResult, StagedEdit, TopologyEditError

CORNERS = [(51.0, -2.0), (51.003, -2.0), (51.0, -1.999)]
SWATHS = [[(51.0, -2.0), (51.001, -2.0)]]


class PlanServiceCase(unittest.TestCase):
    def setUp(self) -> None:
        """Use mock planners and retain background work for deterministic execution."""
        self.topology = Mock()
        self.edits = Mock()
        self.telemetry = SimpleNamespace(latest_gps=None, latest_odom=None)
        self.pending = []
        self.statuses = []
        self.service = plan_module.PlanApplicationService(
            self.topology, self.edits, self.telemetry, is_sim=False, background=self.pending.append)
        self.straight = self.enterContext(patch.object(plan_module, 'run_f2c', return_value=SWATHS))
        self.contour = self.enterContext(
            patch.object(plan_module, 'plan_contour_rows', return_value=None))


class TestPlanningBoundaries(PlanServiceCase):
    def test_incomplete_boundary_never_calls_either_planner(self) -> None:
        """Reject every boundary below three corners in both planning modes."""
        for count in range(3):
            for contour in (False, True):
                with self.subTest(count=count, contour=contour):
                    outcome = self.service.plan(PlanRequest(CORNERS[:count], contour=contour))

                    self.assertEqual(outcome.failure, PlanFailure.TOO_FEW_CORNERS)
                    self.assertIsNone(outcome.planned)
                    self.assertIsNone(outcome.mode)
        self.straight.assert_not_called()
        self.contour.assert_not_called()

    def test_empty_contour_result_succeeds_without_straight_fallback(self) -> None:
        """Only a missing contour reference triggers fallback; zero surviving rows is valid."""
        self.contour.return_value = []

        outcome = self.service.plan(PlanRequest(CORNERS, contour=True))

        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.mode, PlanMode.CONTOUR)
        self.assertEqual(outcome.planned, PlannedRows([], CORNERS[0], True))
        self.straight.assert_not_called()

    def test_empty_straight_result_succeeds_in_straight_and_fallback_modes(self) -> None:
        """Keep the selected mode and origin even when clipping leaves no rows."""
        self.straight.return_value = []
        for contour, mode in ((False, PlanMode.STRAIGHT), (True, PlanMode.FLAT_FALLBACK)):
            with self.subTest(contour=contour):
                outcome = self.service.plan(PlanRequest(CORNERS, contour=contour))

                self.assertTrue(outcome.ok)
                self.assertEqual(outcome.mode, mode)
                self.assertEqual(outcome.planned, PlannedRows([], CORNERS[0]))

    def test_contour_parameters_and_fragment_breaks_survive_planning(self) -> None:
        """Forward terrain settings and freeze split metadata returned by the planner."""
        rings = [[(51.001, -2.0), (51.002, -2.0), (51.001, -1.999)]]
        request = PlanRequest(
            corners_ll=CORNERS, obstacle_rings=rings, tool_width=2.4, angle_deg=72,
            headland_m=3.5, snake=False, obstacle_pad_m=0.6, contour=True,
            recon_path=' recon.csv ', dem_res=0.5)

        def contour_rows(*_args, break_after):
            """Mark a split as the contour planner does."""
            break_after.add(0)
            return SWATHS

        self.contour.side_effect = contour_rows

        outcome = self.service.plan(request)

        self.contour.assert_called_once_with(
            CORNERS, rings, 2.4, 0.6, 3.5, False, 'recon.csv', 0.5, break_after={0})
        breaks = self.contour.call_args.kwargs['break_after']
        breaks.clear()
        self.assertIsInstance(outcome.planned.break_after, frozenset)
        self.assertEqual(outcome.planned.break_after, {0})
        self.straight.assert_not_called()

    def test_flat_fallback_uses_all_straight_settings(self) -> None:
        """The fallback retains the requested angle, width, padding and row order."""
        request = PlanRequest(
            corners_ll=CORNERS, tool_width=2.4, angle_deg=72, headland_m=3.5,
            snake=False, obstacle_pad_m=0.6, contour=True)

        outcome = self.service.plan(request)

        self.straight.assert_called_once_with(CORNERS, [], 2.4, 72, 0.6, 3.5, False)
        self.assertEqual(outcome.mode, PlanMode.FLAT_FALLBACK)
        self.assertFalse(outcome.planned.contour_used)

    def test_fallback_failure_returns_no_partial_plan(self) -> None:
        """An error in straight fallback is still a failed planning outcome."""
        self.straight.side_effect = ValueError('invalid fallback boundary')

        outcome = self.service.plan(PlanRequest(CORNERS, contour=True))

        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failure, PlanFailure.PLANNING)
        self.assertEqual(outcome.detail, 'invalid fallback boundary')
        self.assertIsNone(outcome.planned)
        self.assertIsNone(outcome.mode)


class TestImportGPSBoundaries(PlanServiceCase):
    def setUp(self) -> None:
        """Supply a parser result so GPS validation can be tested independently of GeoJSON."""
        super().setUp()
        self.plan = plan_import.Plan(SWATHS, CORNERS[0], {0}, {'contour': True})
        self.parser = self.enterContext(
            patch.object(plan_import, 'parse_plan', return_value=self.plan))

    def test_hardware_rejects_nonfinite_and_near_zero_coordinates_before_parsing(self) -> None:
        """Both coordinates must be finite and at least one must exceed the zero tolerance."""
        cases = [(0.0, 0.0), (1e-9, -1e-9), (-1e-10, 1e-10)]
        for value in (float('nan'), float('inf'), float('-inf')):
            cases.extend(((value, -2.0), (51.0, value)))
        for lat, lon in cases:
            with self.subTest(lat=lat, lon=lon):
                self.telemetry.latest_gps = gps(lat, lon)

                outcome = self.service.import_plan('plan text')

                self.assertFalse(outcome.ok)
                self.assertIsNone(outcome.planned)
                self.assertEqual(
                    outcome.error, 'no GPS fix; cannot check the plan is near the robot')
        self.parser.assert_not_called()
        self.topology.persist_and_reload.assert_not_called()

    def test_hardware_accepts_fixes_with_one_zero_axis_and_above_tolerance(self) -> None:
        """Accept valid fixes on either geographic axis and just outside the zero tolerance."""
        for lat, lon in ((0.0, -2.0), (51.0, 0.0), (1.0001e-9, 0.0), (0.0, -1.0001e-9)):
            with self.subTest(lat=lat, lon=lon):
                self.telemetry.latest_gps = gps(lat, lon)

                outcome = self.service.import_plan('plan text')

                self.assertTrue(outcome.ok)
                self.parser.assert_called_with('plan text', robot_ll=(lat, lon))
                self.assertEqual(
                    outcome.planned, PlannedRows(SWATHS, CORNERS[0], True, frozenset({0})))

    def test_simulation_never_passes_a_robot_location_to_the_parser(self) -> None:
        """Simulation skips proximity checks even when telemetry contains a valid GPS fix."""
        service = plan_module.PlanApplicationService(
            self.topology, self.edits, self.telemetry, is_sim=True, background=self.pending.append)
        for fix in (None, gps(51.0, -2.0), gps(51.0, -2.0, status=-1)):
            with self.subTest(fix=fix):
                self.telemetry.latest_gps = fix

                self.assertTrue(service.import_plan('plan text').ok)

                self.parser.assert_called_with('plan text', robot_ll=None)

    def test_only_plan_errors_are_converted_to_failed_import_outcomes(self) -> None:
        """Return expected validation errors and propagate unexpected parser failures."""
        self.telemetry.latest_gps = gps(51.0, -2.0)
        self.parser.side_effect = plan_import.PlanError('unsupported version')

        outcome = self.service.import_plan('plan text')

        self.assertEqual(outcome.error, 'unsupported version')
        self.assertIsNone(outcome.planned)
        self.parser.side_effect = RuntimeError('parser defect')
        with self.assertRaisesRegex(RuntimeError, 'parser defect'):
            self.service.import_plan('plan text')
        self.topology.persist_and_reload.assert_not_called()


class TestStagedWrites(PlanServiceCase):
    def test_save_forwards_pose_and_plan_metadata_and_defers_persistence(self) -> None:
        """Stage the caller's plan and telemetry, then persist through the background runner."""
        self.telemetry.latest_gps = gps(51.0, -2.0)
        self.telemetry.latest_odom = SimpleNamespace(
            pose=SimpleNamespace(pose=SimpleNamespace(position=SimpleNamespace(x=12.0, y=-4.0))))
        planned = PlannedRows(SWATHS, CORNERS[0], True, frozenset({0}))
        edit = StagedEdit('saving rows', 'saved rows', Mock())
        self.edits.stage_f2c_save.return_value = edit
        self.topology.persist_and_reload.return_value = PersistResult('live')

        self.service.save_rows(planned, 'crop', 7, True, row_action='row_follow',
                               current_node='HOME', selected_node='SELECTED',
                               on_status=self.statuses.append)

        self.edits.stage_f2c_save.assert_called_once_with(
            'crop', 7, True, swaths=SWATHS, origin_ll=CORNERS[0], contour_used=True,
            break_after=frozenset({0}), is_sim=False, odom_xy=(12.0, -4.0),
            gps=self.telemetry.latest_gps, row_action='row_follow',
            current_node='HOME', selected_node='SELECTED')
        self.assertEqual(self.statuses, ['saving rows'])
        self.topology.persist_and_reload.assert_not_called()
        self.assertEqual(len(self.pending), 1)

        self.pending.pop()()

        self.topology.persist_and_reload.assert_called_once_with(edit.modify)
        self.assertEqual(self.statuses, ['saving rows', 'saved rows — live'])

    def test_rejected_save_or_repair_logs_warnings_and_schedules_nothing(self) -> None:
        """Staging errors preserve their status and never reach the persistence boundary."""
        operations = (
            (self.edits.stage_f2c_save, lambda: self.service.save_rows(
                None, 'crop', 1, False, row_action='row_follow',
                current_node=None, selected_node=None, on_status=self.statuses.append)),
            (self.edits.stage_repair, lambda: self.service.repair_connectivity(
                'row_follow', on_status=self.statuses.append)),
        )
        for stage, operation in operations:
            with self.subTest(stage=stage):
                stage.side_effect = TopologyEditError('ERROR: rejected', warnings=('check map',))
                with self.assertLogs(plan_module.__name__, level='WARNING') as logs:
                    operation()

                self.assertEqual(self.statuses[-1], 'ERROR: rejected')
                self.assertEqual(logs.records[0].getMessage(), 'check map')
                self.assertEqual(self.pending, [])
                self.topology.persist_and_reload.assert_not_called()

    def test_unexpected_staging_errors_propagate_without_scheduling_a_write(self) -> None:
        """Do not turn geometry or metadata defects into a successful background write."""
        self.edits.stage_f2c_save.side_effect = ValueError('geometry failed')
        with self.assertRaisesRegex(ValueError, 'geometry failed'):
            self.service.save_rows(PlannedRows(SWATHS, None), 'crop', 1, False,
                                   row_action='row_follow', current_node=None, selected_node=None,
                                   on_status=self.statuses.append)
        self.edits.stage_repair.side_effect = OverflowError('infinite row id')
        with self.assertRaisesRegex(OverflowError, 'infinite row id'):
            self.service.repair_connectivity('row_follow', on_status=self.statuses.append)

        self.assertEqual(self.pending, [])
        self.topology.persist_and_reload.assert_not_called()


def gps(lat: float, lon: float, *, status: int = 0) -> SimpleNamespace:
    """Return only the NavSatFix fields used by the application service."""
    return SimpleNamespace(latitude=lat, longitude=lon, status=SimpleNamespace(status=status))


if __name__ == '__main__':
    unittest.main()
