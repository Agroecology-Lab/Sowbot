"""Plan tab boundary map, controls and repair dialog, composed over a small fake UI."""
# pylint: disable=protected-access,attribute-defined-outside-init,no-member
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from devkit_ui.constants import FIELD27_BOUNDS, FIELD27_CENTER
from devkit_ui.plan_fake_ui import (
    CORNERS,
    FORM,
    GREEN,
    GREY,
    RED,
    BoundaryMapCard,
    Deferred,
    PlanControlsCard,
    fake_ui,
    good,
    plan_controls_card,
    repair_dialog,
    run_to_end,
)
from devkit_ui.view_models.plan_view_model import PlanPageState, PlanView, RepairOffer


class TestBoundaryMapCard(unittest.TestCase):
    def setUp(self) -> None:
        """Build the card over a fresh page state."""
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        self.state = PlanPageState()
        self.card = BoundaryMapCard(self.state, (51.0, -2.0))
        self.map = self.card.map

    def test_map_centres_on_the_robot_when_it_has_a_fix(self) -> None:
        """Verify a live fix is the centre and the field extent is not forced."""
        self.assertEqual(self.map.center, (51.0, -2.0))
        self.map.run_map_method.assert_not_called()

    def test_map_fits_the_field_extent_without_a_fix(self) -> None:
        """Verify no fix means the default field, fitted rather than zoomed on its centre."""
        fake_ui.reset()

        card = BoundaryMapCard(self.state, None)

        self.assertEqual(card.map.center, FIELD27_CENTER)
        card.map.run_map_method.assert_called_once_with(
            'fitBounds', [list(FIELD27_BOUNDS[0]), list(FIELD27_BOUNDS[1])])

    def test_each_corner_gets_a_marker_and_two_corners_make_an_outline(self) -> None:
        """Verify markers follow the corners and the polygon appears from the second."""
        self.state.add_corner(*CORNERS[0])
        self.assertEqual(len(self.map.layers_named('marker')), 1)
        self.assertEqual(self.map.layers_named('polygon'), [])

        self.state.add_corner(*CORNERS[1])
        self.state.add_corner(*CORNERS[2])

        self.assertEqual([m.kwargs['latlng'] for m in self.map.layers_named('marker')],
                         list(CORNERS))
        outline = self.map.layers_named('polygon')
        self.assertEqual(len(outline), 1)
        self.assertEqual(outline[0].kwargs['args'][0], [list(c) for c in CORNERS])

    def test_clear_removes_the_markers_the_outline_and_the_rows(self) -> None:
        """Verify nothing of the old field stays on the map."""
        for corner in CORNERS:
            self.state.add_corner(*corner)
        self.state.finish_plan(self.state.begin_plan([], **FORM), good())
        self.assertTrue(self.map.layers_named('polyline'))

        self.state.clear()

        self.assertEqual(self.map.layers, [])

    def test_a_draft_draws_one_line_per_swath_once(self) -> None:
        """Verify unrelated state changes do not redraw the rows."""
        for corner in CORNERS:
            self.state.add_corner(*corner)
        run = self.state.begin_plan([], **FORM)
        self.state.finish_plan(run, good())
        drawn = list(self.map.layers_named('polyline'))

        self.state.apply_import(PlanView(False, 'ERROR: not a plan'))
        self.card.refresh()

        self.assertEqual(len(drawn), 1)
        self.assertEqual(self.map.layers_named('polyline'), drawn)
        self.assertNotIn(drawn[0], self.map.removed)

    def test_a_new_draft_replaces_the_old_rows(self) -> None:
        """Verify an earlier plan is taken off the map."""
        for corner in CORNERS:
            self.state.add_corner(*corner)
        self.state.finish_plan(self.state.begin_plan([], **FORM), good(51.0))
        first = self.map.layers_named('polyline')[0]

        self.state.finish_plan(self.state.begin_plan([], **FORM), good(52.0))

        self.assertIn(first, self.map.removed)
        self.assertEqual(len(self.map.layers_named('polyline')), 1)

    def test_a_failed_plan_leaves_the_earlier_rows(self) -> None:
        """Verify an error does not wipe the map."""
        for corner in CORNERS:
            self.state.add_corner(*corner)
        self.state.finish_plan(self.state.begin_plan([], **FORM), good())

        self.state.finish_plan(self.state.begin_plan([], **FORM), PlanView(False, 'failed'))

        self.assertEqual(len(self.map.layers_named('polyline')), 1)

    def test_an_imported_plan_is_drawn_for_review(self) -> None:
        """Verify import shows the rows before they are saved."""
        self.state.apply_import(good())

        self.assertEqual(len(self.map.layers_named('polyline')), 1)


class ControlsCase(unittest.TestCase):
    def setUp(self) -> None:
        """Build the map and controls over a fresh page state and mock view models."""
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        self.state = PlanPageState()
        self.plan_vm = Mock()
        self.plan_vm.save_status = ''
        self.obstacle_vm = Mock()
        self.obstacle_vm.rings_ll.return_value = []
        self.handle = Mock(obstacle_pad=SimpleNamespace(value=0.5))
        self.attach = Mock(return_value=self.handle)
        self.enterContext(patch.object(plan_controls_card, 'attach_mission_sidebar_controls',
                                       self.attach))
        self.io_bound = Mock(return_value=Deferred())
        self.enterContext(patch.object(plan_controls_card, 'ng_run',
                                       SimpleNamespace(io_bound=self.io_bound)))
        self.map = BoundaryMapCard(self.state, None).map
        self.card = PlanControlsCard(self.state, self.plan_vm, self.obstacle_vm, self.map)

    def button(self, text):
        """Return the button with this label."""
        return fake_ui.find('button', text)[0]

    def draw_boundary(self) -> None:
        """Draw three corners through the callback the map click is wired to."""
        for corner in CORNERS:
            self.attach.call_args.args[2](*corner)

    def start_plan(self):
        """Press Plan Rows and run until the worker is awaited."""
        pending = self.button('Plan Rows').click()
        self.addCleanup(pending.close)
        pending.send(None)
        return pending

    def finish_plan(self, pending, view) -> None:
        """Deliver a worker result and require the click handler to finish."""
        with self.assertRaises(StopIteration):
            pending.send(view)


class TestPlanControlsCard(ControlsCase):
    def test_the_map_click_adds_a_boundary_corner(self) -> None:
        """Verify the draw controls are given the state's corner callback and the same map."""
        self.assertIs(self.attach.call_args.args[1], self.map)

        self.draw_boundary()

        self.assertEqual(self.state.corners, list(CORNERS))

    def test_corner_count_follows_the_boundary(self) -> None:
        """Verify the label asks for three corners, then confirms them."""
        self.assertEqual(self.card._corners_lbl.text, '0 corners — need 3+')
        self.state.add_corner(*CORNERS[0])
        self.assertEqual(self.card._corners_lbl.text, '1 corner — need 3+')

        self.draw_boundary()

        self.assertEqual(self.card._corners_lbl.text, '4 corners ✓')

    def test_save_starts_disabled_and_planning_enabled(self) -> None:
        """Verify the starting button state."""
        self.assertFalse(self.button('Save as Topo Rows').enabled)
        self.assertTrue(self.button('Plan Rows').enabled)

    def test_planning_without_a_boundary_reports_it_and_starts_no_worker(self) -> None:
        """Verify too few corners show a red message."""
        run_to_end(self.button('Plan Rows').click())

        self.assertEqual(self.card._status_lbl.text, 'Need at least 3 corners')
        self.assertEqual(self.card._status_lbl.styles[-1], RED)
        self.io_bound.assert_not_called()

    def test_the_form_reaches_the_planner(self) -> None:
        """Verify every setting, the obstacle rings and the padding go into the request."""
        self.draw_boundary()
        self.card._width.value = 2.5
        self.card._angle.value = 30
        self.card._headland.value = 1.5
        self.card._snake.value = False
        self.card._contour.value = True
        self.card._recon_path.value = '/x.csv'
        self.card._dem_res.value = 2.0
        self.obstacle_vm.rings_ll.return_value = [[(51.0, -2.0)]]

        self.start_plan()

        self.assertIs(self.io_bound.call_args.args[0], self.plan_vm.plan)
        request = self.io_bound.call_args.args[1]
        self.assertEqual(
            (request.tool_width, request.angle_deg, request.headland_m, request.snake,
             request.obstacle_pad_m, request.contour, request.recon_path, request.dem_res),
            (2.5, 30.0, 1.5, False, 0.5, True, '/x.csv', 2.0))
        self.assertEqual(request.obstacle_rings, [[(51.0, -2.0)]])
        self.assertEqual(request.corners_ll, list(CORNERS))

    def test_planning_disables_the_button_until_the_result_arrives(self) -> None:
        """Verify the running status and the disabled button, then green status and Save."""
        self.draw_boundary()
        pending = self.start_plan()
        self.assertFalse(self.button('Plan Rows').enabled)
        self.assertEqual(self.card._status_lbl.text, 'Running F2C…')
        self.assertEqual(self.card._status_lbl.styles[-1], GREY)

        self.finish_plan(pending, good())

        self.assertTrue(self.button('Plan Rows').enabled)
        self.assertTrue(self.button('Save as Topo Rows').enabled)
        self.assertEqual(self.card._status_lbl.text, 'planned rows')
        self.assertEqual(self.card._status_lbl.styles[-1], GREEN)
        self.assertEqual(len(self.map.layers_named('polyline')), 1)

    def test_a_failed_plan_is_red_and_keeps_save_off(self) -> None:
        """Verify an error shows in red and planning can be retried."""
        self.draw_boundary()
        pending = self.start_plan()

        self.finish_plan(pending, PlanView(False, 'Planning failed: bad field'))

        self.assertEqual(self.card._status_lbl.text, 'Planning failed: bad field')
        self.assertEqual(self.card._status_lbl.styles[-1], RED)
        self.assertTrue(self.button('Plan Rows').enabled)
        self.assertFalse(self.button('Save as Topo Rows').enabled)

    def test_save_passes_the_draft_with_the_form_values(self) -> None:
        """Verify Save writes exactly the planned rows with prefix, first id and overwrite."""
        self.draw_boundary()
        view = good()
        self.finish_plan(self.start_plan(), view)
        self.card._prefix.value = 'N'
        self.card._row_id_start.value = 7
        self.card._overwrite.value = True

        self.button('Save as Topo Rows').click()

        self.plan_vm.save_rows.assert_called_once_with(view.planned, 'N', 7, overwrite=True)

    def test_save_defaults_a_blank_prefix_and_first_id(self) -> None:
        """Verify empty fields fall back to F2C and 1."""
        self.card._prefix.value = ''
        self.card._row_id_start.value = None

        self.button('Save as Topo Rows').click()

        self.plan_vm.save_rows.assert_called_once_with(None, 'F2C', 1, overwrite=False)

    def test_clear_discards_pending_success_and_failure(self) -> None:
        """Verify cleared work cannot restore rows, a draft, a status or an enabled Save."""
        for view in (good(), PlanView(False, 'old failure')):
            with self.subTest(ok=view.ok):
                self.button('Clear').click()
                self.draw_boundary()
                pending = self.start_plan()
                self.button('Clear').click()
                layers = len(self.map.layers)

                self.finish_plan(pending, view)
                self.button('Save as Topo Rows').click()

                self.assertEqual(len(self.map.layers), layers)
                self.assertEqual(self.card._status_lbl.text, '')
                self.assertFalse(self.button('Save as Topo Rows').enabled)
                self.assertTrue(self.button('Plan Rows').enabled)
                self.assertIsNone(self.plan_vm.save_rows.call_args.args[0])

    def test_an_old_result_cannot_enable_planning_during_a_new_run(self) -> None:
        """Verify Clear permits another run while its predecessor finishes harmlessly."""
        self.draw_boundary()
        old = self.start_plan()
        self.button('Clear').click()
        self.assertTrue(self.button('Plan Rows').enabled)
        self.draw_boundary()
        current = self.start_plan()

        self.finish_plan(old, good())

        self.assertFalse(self.button('Plan Rows').enabled)
        self.finish_plan(current, good())
        self.assertTrue(self.button('Plan Rows').enabled)

    def test_clear_resets_the_boundary_map_and_obstacle_drawing(self) -> None:
        """Verify Clear removes corner markers, ends an obstacle polygon and recentres the map."""
        self.draw_boundary()

        self.button('Clear').click()

        self.assertEqual(self.map.layers, [])
        self.assertEqual(self.card._corners_lbl.text, '0 corners — need 3+')
        self.handle.clear_in_progress.assert_called_once_with()
        self.map.set_center.assert_called_once_with(self.map.center)

    def test_a_good_import_enables_save_and_shows_the_rows(self) -> None:
        """Verify the uploaded text is decoded and its plan becomes the draft."""
        self.plan_vm.import_plan.return_value = good()
        upload = SimpleNamespace(file=SimpleNamespace(read=AsyncMock(return_value=b'{"a": 1}')))

        run_to_end(fake_ui.find('upload')[0].kwargs['on_upload'](upload))

        self.plan_vm.import_plan.assert_called_once_with('{"a": 1}')
        self.assertTrue(self.button('Save as Topo Rows').enabled)
        self.assertEqual(self.card._status_lbl.styles[-1], GREEN)
        self.assertEqual(len(self.map.layers_named('polyline')), 1)

    def test_a_bad_import_is_red_and_cannot_be_saved(self) -> None:
        """Verify an unreadable plan reports the error and leaves Save off."""
        self.plan_vm.import_plan.return_value = PlanView(False, 'ERROR: not a plan')
        upload = SimpleNamespace(content=SimpleNamespace(read=Mock(return_value=b'\xff')))

        run_to_end(fake_ui.find('upload')[0].kwargs['on_upload'](upload))

        self.assertEqual(self.card._status_lbl.text, 'ERROR: not a plan')
        self.assertEqual(self.card._status_lbl.styles[-1], RED)
        self.assertFalse(self.button('Save as Topo Rows').enabled)
        self.plan_vm.import_plan.assert_called_once_with('\ufffd')

    def test_contour_mode_shows_its_inputs_and_disables_the_angle(self) -> None:
        """Verify the toggle swaps the row angle for the recon log and grid resolution."""
        angle = self.card._angle
        self.assertTrue(angle.enabled)
        self.assertFalse(any(e.visible for e in self.card._contour_only))

        self.card._contour.handlers['value'](SimpleNamespace(value=True))

        self.assertFalse(angle.enabled)
        self.assertTrue(all(e.visible for e in self.card._contour_only))
        self.card._contour.handlers['value'](SimpleNamespace(value=False))
        self.assertTrue(angle.enabled)
        self.assertFalse(any(e.visible for e in self.card._contour_only))

    def test_the_save_status_follows_the_plan_service_and_only_when_it_changes(self) -> None:
        """Verify the label colours for an error, a success and a blank status."""
        timer = next(cb for interval, cb in fake_ui.timers if interval == 0.4)
        for status, colour in (('ERROR: disk', RED), ('saved 3 rows', GREEN), ('', GREY)):
            with self.subTest(status=status):
                self.plan_vm.save_status = status

                timer()
                timer()

                self.assertEqual(self.card._save_lbl.text, status)
                self.assertEqual(self.card._save_lbl.styles[-1], colour)
        self.assertEqual(len(self.card._save_lbl.styles), 4)


class TestRepair(ControlsCase):
    def setUp(self) -> None:
        """Offer a repair of two rows, and answer the dialog as the test directs."""
        super().setUp()
        self.plan_vm.repair_offer.return_value = RepairOffer(2, 'HOME')
        self.answer = ('ok', None)
        self.seen = []

        def answer(_dialog):
            """Type into the dialog's input, if asked, and return the pressed button."""
            field = fake_ui.find('input')[-1]
            self.seen.append((field.value, [e.text for e in fake_ui.find('label')]))
            if self.answer[1] is not None:
                field.value = self.answer[1]
            return self.answer[0]
        fake_ui.answer_dialog = answer
        self.addCleanup(setattr, fake_ui, 'answer_dialog', None)

    def repair(self) -> None:
        """Press Repair Connectivity."""
        run_to_end(self.button('Repair Connectivity').click())

    def test_no_map_opens_no_dialog(self) -> None:
        """Verify nothing is asked, and nothing repaired, without a map."""
        self.plan_vm.repair_offer.return_value = None

        self.repair()

        self.assertEqual(self.seen, [])
        self.plan_vm.repair.assert_not_called()

    def test_the_dialog_names_the_row_count_and_suggests_the_base(self) -> None:
        """Verify what the user is shown."""
        self.repair()

        value, labels = self.seen[0]
        self.assertEqual(value, 'HOME')
        self.assertIn('Add navigate_to_pose edges between 2 consecutive row IN/OUT pairs. '
                      'Optionally splice the chain into a base node so the planner can reach it.',
                      labels)

    def test_confirming_repairs_with_the_typed_base_stripped(self) -> None:
        """Verify the node name is passed on without surrounding spaces."""
        self.answer = ('ok', '  B_IN ')

        self.repair()

        self.plan_vm.repair.assert_called_once_with('B_IN')

    def test_confirming_with_a_blank_base_wires_the_chain_alone(self) -> None:
        """Verify blank means no base node."""
        self.answer = ('ok', '   ')

        self.repair()

        self.plan_vm.repair.assert_called_once_with(None)

    def test_cancelling_repairs_nothing(self) -> None:
        """Verify Cancel leaves the map alone."""
        self.answer = ('cancel', 'B_IN')

        self.repair()

        self.plan_vm.repair.assert_not_called()

    def test_the_dialog_buttons_submit_their_answers(self) -> None:
        """Verify Cancel and Repair submit the values the card checks for."""
        run_to_end(repair_dialog.ask_repair_base(RepairOffer(1, '')))
        dialog = fake_ui.find('dialog')[-1]

        fake_ui.find('button', 'Cancel')[-1].click()
        fake_ui.find('button', 'Repair')[-1].click()

        self.assertEqual(dialog.submitted, ['cancel', 'ok'])


if __name__ == '__main__':
    unittest.main()
