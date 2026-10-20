"""Plan tab mission queue, mission store and page composition, over a small fake UI."""
# pylint: disable=protected-access,attribute-defined-outside-init,no-member
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from devkit_ui.actions import ActionDef
from devkit_ui.plan_fake_ui import (
    GREEN,
    GREY,
    RED,
    MissionQueueCard,
    MissionStoreCard,
    PlanPage,
    fake_ui,
    mission_queue_card,
    mission_store_card,
    plan_controls_card,
    plan_page,
    texts,
    walk,
)
from devkit_ui.test_mission_domain_service import mds  # installs ROS message stand-ins
from devkit_ui.view_models.mission_view_model import MissionQueue
from devkit_ui.view_models.plan_view_model import PlanPageState

DEPTH = SimpleNamespace(key='depth', label='Depth', unit='m', default=0.4, min=0.0, max=1.0,
                        step=0.1, precision=2)


class TestMissionQueueCard(unittest.TestCase):
    def setUp(self) -> None:
        """Build the card over a real queue and a map with rows 1 and 2."""
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        actions = {'drive': ActionDef('Drive only', 'D'),
                   'till': ActionDef('Till', 'T', param_schema=(DEPTH,))}
        self.enterContext(patch.object(mission_queue_card, 'ACTIONS', actions))
        self.queue = MissionQueue()
        self.vm = Mock()
        self.vm.topo_snapshot.return_value = frozenset({('A_IN', 1, 'entry')})
        self.vm.available_rows.return_value = {2: 'B_IN', 1: 'A_IN'}
        self.mission_vm, self.on_delete = Mock(), Mock()
        self.card = MissionQueueCard(self.queue, self.vm, self.mission_vm, self.on_delete)
        self.tick = fake_ui.timers[-1][1]

    def buttons(self, container, text):
        """Return the buttons in a container with this label, in order."""
        return [e for e in walk(container) if e.kind == 'button' and e.text == text]

    def queue_rows(self):
        """Return the label texts shown in the queue panel."""
        return texts(self.card._queue_col)

    def test_the_implement_picker_offers_every_action_and_starts_on_drive(self) -> None:
        """Verify the options and the default."""
        picker = self.card._action

        self.assertEqual(picker.kwargs['options'], {'drive': 'D Drive only', 'till': 'T Till'})
        self.assertEqual(picker.value, 'drive')
        self.assertEqual(self.card._param_inputs, {})

    def test_choosing_an_action_with_parameters_shows_an_input_for_each(self) -> None:
        """Verify the depth input appears with its label and default, then goes again."""
        self.card._action.value = 'till'
        self.card._action.handlers['value'](None)

        depth = self.card._param_inputs['depth']
        self.assertEqual((depth.kwargs['label'], depth.value), ('Depth (m)', 0.4))
        self.assertEqual(depth.kwargs['min'], 0.0)
        self.assertEqual(depth.kwargs['max'], 1.0)

        self.card._action.value = 'drive'
        self.card._action.handlers['value'](None)
        self.assertEqual(self.card._param_inputs, {})
        self.assertEqual(self.card._param_row.children, [])

    def test_the_queue_starts_empty_with_its_hint(self) -> None:
        """Verify the empty-queue hint is shown."""
        self.assertTrue(self.card._queue_lbl.visible)
        self.assertEqual(self.card._queue_col.children, [])

    def test_available_rows_are_listed_in_row_order(self) -> None:
        """Verify rows 1 and 2 appear with their entry nodes, sorted."""
        self.assertEqual(
            texts(self.card._available_col),
            ['Row 1', 'A_IN', 'Row 2', 'B_IN'])

    def test_adding_a_row_queues_it_with_the_selected_action_and_parameters(self) -> None:
        """Verify the step carries the action key and the float parameter values."""
        self.card._action.value = 'till'
        self.card._action.handlers['value'](None)
        self.card._param_inputs['depth'].value = 0.7

        self.buttons(self.card._available_col, 'Add →')[1].click(None)

        self.assertEqual(self.queue.items, [(2, 'till', {'depth': 0.7})])
        self.assertFalse(self.card._queue_lbl.visible)
        self.assertEqual(self.queue_rows(), ['Row 2  T Till', 'Depth: 0.70m'])

    def test_the_same_row_can_be_queued_twice(self) -> None:
        """Verify duplicates are kept, as one row may run with two implements."""
        add = self.buttons(self.card._available_col, 'Add →')[0]

        add.click(None)
        add.click(None)

        self.assertEqual([r for r, _, _ in self.queue.items], [1, 1])

    def test_an_action_without_parameters_queues_empty_parameters(self) -> None:
        """Verify drive carries no parameters."""
        self.buttons(self.card._available_col, 'Add →')[0].click(None)

        self.assertEqual(self.queue.items, [(1, 'drive', {})])
        self.assertEqual(self.queue_rows(), ['Row 1  D Drive only'])

    def test_a_step_with_an_unknown_action_still_shows(self) -> None:
        """Verify a queued key that no longer exists is shown by key."""
        self.queue.add(5, 'gone', {})

        self.assertEqual(self.queue_rows(), ['Row 5  ? gone'])

    def test_move_buttons_are_disabled_at_the_ends_and_reorder_the_queue(self) -> None:
        """Verify first cannot go up, last cannot go down, and the buttons swap neighbours."""
        for row in (1, 2, 3):
            self.queue.add(row, 'drive', {})
        up = self.buttons(self.card._queue_col, '↑')
        down = self.buttons(self.card._queue_col, '↓')
        self.assertEqual([b.enabled for b in up], [False, True, True])
        self.assertEqual([b.enabled for b in down], [True, True, False])

        down[0].click(None)
        self.assertEqual([r for r, _, _ in self.queue.items], [2, 1, 3])

        self.buttons(self.card._queue_col, '↑')[2].click(None)
        self.assertEqual([r for r, _, _ in self.queue.items], [2, 3, 1])
        self.assertEqual(self.queue_rows(), ['Row 2  D Drive only', 'Row 3  D Drive only',
                                             'Row 1  D Drive only'])

    def test_the_remove_button_drops_that_step(self) -> None:
        """Verify removal by position, then the hint returns."""
        self.queue.add(1, 'drive', {})
        self.queue.add(2, 'drive', {})

        self.buttons(self.card._queue_col, '✕')[0].click(None)
        self.assertEqual([r for r, _, _ in self.queue.items], [2])
        self.buttons(self.card._queue_col, '✕')[0].click(None)

        self.assertTrue(self.card._queue_lbl.visible)

    def test_run_passes_the_queue_and_the_status_reporter(self) -> None:
        """Verify Run Mission hands the live steps and the card's status reporter to the view model."""
        self.queue.add(1, 'drive', {})

        self.card.run_btn.click()

        self.mission_vm.run.assert_called_once_with(self.queue.items, self.card._show_status)

    def test_cancel_cancels_the_mission_in_the_view_model(self) -> None:
        """Verify Cancel is wired straight to the view model."""
        fake_ui.find('button', 'Cancel')[0].click()

        self.mission_vm.cancel.assert_called_once_with()

    def test_each_status_level_sets_the_label_text_and_colour(self) -> None:
        """Verify every level the executor reports is shown in its own colour."""
        for level, colour in [('info', '#57606a'), ('progress', '#0969da'),
                              ('warn', '#9a6700'), ('error', '#cf222e'), ('ok', '#1a7f37')]:
            with self.subTest(level=level):
                self.card._show_status(level, f'mission {level}')

                self.assertEqual(self.card._status_lbl.text, f'mission {level}')
                self.assertEqual(self.card._status_lbl.styles[-1], f'color:{colour}')

    def test_colours_cover_exactly_the_levels_the_executor_reports(self) -> None:
        """Verify a new executor level cannot reach the card without a colour."""
        levels = {mds.INFO, mds.PROGRESS, mds.WARN, mds.ERROR, mds.OK}

        self.assertEqual(set(mission_queue_card.STATUS_COLOURS), levels)

    def test_run_can_be_disabled_while_a_mission_runs(self) -> None:
        """Verify the store card's switch reaches the button."""
        self.card.set_run_enabled(False)
        self.assertFalse(self.card.run_btn.enabled)
        self.card.set_run_enabled(True)
        self.assertTrue(self.card.run_btn.enabled)

    def test_deleting_a_row_goes_through_the_callback_with_its_id(self) -> None:
        """Verify the map's row delete asks the node, which confirms it."""
        self.buttons(self.card._available_col, '✕')[1].click(None)

        self.on_delete.assert_called_once_with(2)

    def test_a_changed_map_is_drawn_once_it_has_settled(self) -> None:
        """Verify the list waits one tick, then follows the map, and not again if it is unchanged."""
        self.vm.topo_snapshot.return_value = frozenset({('C_IN', 3, 'entry')})
        self.vm.available_rows.return_value = {3: 'C_IN'}
        self.vm.available_rows.reset_mock()

        self.tick()
        self.assertEqual(texts(self.card._available_col), ['Row 1', 'A_IN', 'Row 2', 'B_IN'])
        self.tick()
        self.assertEqual(texts(self.card._available_col), ['Row 3', 'C_IN'])
        self.tick()
        self.tick()

        self.vm.available_rows.assert_called_once_with()

    def test_a_map_that_keeps_changing_is_not_drawn(self) -> None:
        """Verify a map still being rewritten leaves the list as it was."""
        for n in range(4):
            self.vm.topo_snapshot.return_value = frozenset({('X', n, 'entry')})
            self.tick()

        self.assertEqual(texts(self.card._available_col), ['Row 1', 'A_IN', 'Row 2', 'B_IN'])

    def test_no_map_and_no_rows_say_so(self) -> None:
        """Verify an unloaded map, and a map without rows, show the placeholder."""
        self.vm.topo_snapshot.return_value = None
        self.vm.available_rows.return_value = {}

        self.tick()
        self.tick()

        self.assertEqual(texts(self.card._available_col), ['No rows in map yet'])


MISSIONS = (
    {'id': 'M1', 'name': 'NORTH', 'action': 'drive', 'rows': [1, 2], 'active': True,
     'last_run_success': True},
    {'id': 'M2', 'action': 'gone', 'rows': [], 'active': False, 'last_run_success': False},
    {'id': 'M3', 'name': 'SOUTH', 'action': 'drive', 'rows': [3, 4, 5], 'active': True},
)


class TestMissionStoreCard(unittest.TestCase):
    def setUp(self) -> None:
        """Build the card over a mock view model holding three saved missions."""
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        self.enterContext(patch.object(
            mission_store_card, 'ACTIONS', {'drive': ActionDef('Drive only', 'D')}))
        self.queue = MissionQueue()
        self.vm = Mock()
        self.vm.status = ''
        self.vm.version = 1
        self.vm.running = False
        self.vm.missions = MISSIONS
        self.vm.next_due_in_hours.side_effect = lambda mission_id: {
            'M1': 0.0, 'M2': None, 'M3': 1.5}[mission_id]
        self.set_run_enabled = Mock()
        self.card = MissionStoreCard(self.vm, self.queue, self.set_run_enabled)
        self.tick = fake_ui.timers[-1][1]

    def rows(self):
        """Return each saved mission's row of label texts."""
        return [texts(row) for row in self.card._missions_col.children]

    def test_each_mission_shows_its_action_name_size_due_time_and_last_result(self) -> None:
        """Verify the row for a due mission, an unknown action with no name, and a later one."""
        self.assertEqual(self.rows(), [
            ['D NORTH', '2 rows', 'due now', '✓'],
            ['? M2', '0 rows', 'done', '✗'],
            ['D SOUTH', '3 rows', 'in 1.5h', '—'],
        ])
        self.assertFalse(self.card._empty_lbl.visible)

    def test_due_times_are_coloured_by_urgency(self) -> None:
        """Verify due now is green, done is muted and a later time is amber."""
        styles = [row.children[2].styles[-1] for row in self.card._missions_col.children]

        self.assertEqual(styles, [GREEN, 'color:#8c959f', 'color:#9a6700'])

    def test_the_active_box_and_delete_button_act_on_that_mission(self) -> None:
        """Verify the controls carry the mission's id."""
        row = self.card._missions_col.children[1]
        checkbox = next(e for e in row.children if e.kind == 'checkbox')
        delete = next(e for e in row.children if e.kind == 'button')

        self.assertFalse(checkbox.value)
        checkbox.handlers['value'](SimpleNamespace(value=True))
        delete.click(None)

        self.vm.set_active.assert_called_once_with('M2', True)
        self.vm.delete.assert_called_once_with('M2')

    def test_no_saved_missions_shows_the_placeholder(self) -> None:
        """Verify an empty store shows its hint and no rows."""
        self.vm.missions = ()
        self.vm.version = 2

        self.tick()

        self.assertTrue(self.card._empty_lbl.visible)
        self.assertEqual(self.rows(), [])

    def test_the_list_is_rebuilt_only_when_the_store_version_changes(self) -> None:
        """Verify an unchanged version leaves the list while refreshing the Run button."""
        self.vm.missions = ()
        self.set_run_enabled.reset_mock()

        self.tick()
        self.assertEqual(len(self.rows()), 3)
        self.set_run_enabled.assert_called_once_with(True)

        self.vm.version = 2
        self.tick()
        self.assertEqual(self.rows(), [])

    def test_run_is_disabled_while_a_mission_runs(self) -> None:
        """Verify Run follows the executor without a store version change."""
        self.set_run_enabled.assert_called_once_with(True)
        self.vm.running = True

        self.tick()

        self.set_run_enabled.assert_called_with(False)

        self.vm.running = False
        self.tick()

        self.set_run_enabled.assert_called_with(True)

    def test_save_stores_the_queue_under_the_name_and_repeat_interval(self) -> None:
        """Verify the live queue steps, the name and the interval reach the view model."""
        self.queue.add(1, 'drive', {})
        self.card._name.value = 'my run'
        self.card._repeat.value = 4.0

        fake_ui.find('button', 'Save')[0].click()

        self.vm.save_queue.assert_called_once_with(self.queue.items, 'my run', 4)

    def test_save_with_a_blank_name_and_repeat_is_a_one_shot(self) -> None:
        """Verify blank fields become an empty name and no interval."""
        self.card._name.value = None
        self.card._repeat.value = None

        fake_ui.find('button', 'Save')[0].click()

        self.vm.save_queue.assert_called_once_with([], '', None)

    def test_the_status_line_is_red_for_errors_green_for_news_and_grey_when_blank(self) -> None:
        """Verify the status text and colour follow the view model."""
        for status, colour in (('ERROR: queue is empty', RED), ('saved M4', GREEN), ('', GREY)):
            with self.subTest(status=status):
                self.vm.status = status

                self.tick()

                self.assertEqual(self.card._status_lbl.text, status)
                self.assertEqual(self.card._status_lbl.styles[-1], colour)


class TestPlanPage(unittest.TestCase):
    def setUp(self) -> None:
        """Compose the page from real cards over mock view models."""
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        self.enterContext(patch.object(
            mission_queue_card, 'ACTIONS', {'drive': ActionDef('Drive only', 'D')}))
        self.enterContext(patch.object(
            mission_store_card, 'ACTIONS', {'drive': ActionDef('Drive only', 'D')}))
        self.handle = Mock(obstacle_pad=SimpleNamespace(value=0.5))
        self.attach = Mock(return_value=self.handle)
        self.enterContext(patch.object(
            plan_controls_card, 'attach_mission_sidebar_controls', self.attach))
        self.obstacle_panel = Mock()
        self.enterContext(patch.object(
            plan_page, 'attach_mission_obstacle_panel', self.obstacle_panel))
        self.plan_vm = Mock()
        self.plan_vm.open_page.side_effect = PlanPageState
        self.plan_vm.save_status = ''
        self.plan_vm.topo_snapshot.return_value = frozenset()
        self.plan_vm.available_rows.return_value = {1: 'A_IN'}
        self.mission_vm = Mock()
        self.mission_vm.new_queue.side_effect = MissionQueue
        self.mission_vm.status = ''
        self.mission_vm.version = 1
        self.mission_vm.running = False
        self.mission_vm.missions = ()
        self.obstacle_vm = Mock()
        self.topo_vm, self.authoring = Mock(), Mock()

    def build(self):
        """Build a page, as one browser session opening the tab would."""
        return PlanPage(self.plan_vm, self.mission_vm, self.obstacle_vm, self.topo_vm,
                        self.authoring, (51.0, -2.0))

    def test_the_page_is_a_full_width_column_without_extra_gaps(self) -> None:
        """Verify the cards carry their own spacing."""
        self.assertEqual(self.build().class_names, 'w-full gap-0')

    def test_cards_render_in_workflow_order_and_the_store_sits_in_the_queue_card(self) -> None:
        """Verify map and controls, then queue with its store, then the obstacle panel."""
        page = self.build()

        top, queue_card = page.children
        self.assertEqual(top.class_names, 'w-full gap-3 items-start mb-3')
        self.assertEqual([c.__class__.__name__ for c in top.children],
                         ['BoundaryMapCard', 'PlanControlsCard'])
        self.assertIsInstance(queue_card, MissionQueueCard)
        self.assertEqual([c.__class__.__name__ for c in queue_card.children][-2:],
                         ['Element', 'MissionStoreCard'])
        self.obstacle_panel.assert_called_once_with(self.handle)

    def test_the_controls_draw_on_the_pages_own_map(self) -> None:
        """Verify the obstacle controls and the boundary card share one map."""
        page = self.build()
        boundary, controls = page.children[0].children

        self.assertIs(self.attach.call_args.args[1], boundary.map)
        self.assertIs(controls.draw_handle, self.handle)

    def test_two_pages_do_not_share_a_boundary_or_a_queue(self) -> None:
        """Verify a corner drawn, and a row queued, in one session do not appear in another."""
        first, second = self.build(), self.build()
        first_map = first.children[0].children[0].map
        second_map = second.children[0].children[0].map

        self.attach.call_args_list[0].args[2](51.0, -2.0)
        first.children[1]._queue.add(1, 'drive', {})

        self.assertEqual(len(first_map.layers_named('marker')), 1)
        self.assertEqual(second_map.layers, [])
        self.assertEqual(second.children[1]._queue.items, [])

    def test_the_store_saves_the_queue_the_queue_card_builds(self) -> None:
        """Verify one queue feeds both cards."""
        page = self.build()
        queue_card = page.children[1]
        next(b for b in walk(queue_card) if b.kind == 'button' and b.text == 'Add →').click(None)

        next(b for b in walk(queue_card) if b.kind == 'button' and b.text == 'Save').click()

        self.mission_vm.save_queue.assert_called_once_with([(1, 'drive', {})], '', None)

    def test_the_store_switches_the_queue_cards_run_button(self) -> None:
        """Verify a running mission disables Run Mission."""
        self.mission_vm.running = True
        self.mission_vm.version = 2
        page = self.build()

        self.assertFalse(page.children[1].run_btn.enabled)


if __name__ == '__main__':
    unittest.main()

class TestPlanPageDeleteRow(unittest.IsolatedAsyncioTestCase):
    async def test_deleting_a_row_asks_for_confirmation_then_deletes_it_through_authoring(self):
        """Verify the page confirms against the live map and hands the delete to authoring."""
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        enter = patch.object
        self.enterContext(enter(mission_queue_card, 'ACTIONS', {'drive': ActionDef('Drive', 'D')}))
        self.enterContext(enter(mission_store_card, 'ACTIONS', {'drive': ActionDef('Drive', 'D')}))
        self.enterContext(enter(plan_controls_card, 'attach_mission_sidebar_controls',
                                Mock(return_value=Mock(obstacle_pad=SimpleNamespace(value=0.5)))))
        self.enterContext(enter(plan_page, 'attach_mission_obstacle_panel', Mock()))
        confirm = self.enterContext(enter(plan_page, 'confirm_delete_row', Mock()))
        confirm.side_effect = _completed
        plan_vm, mission_vm = Mock(), Mock()
        plan_vm.open_page.side_effect = PlanPageState
        plan_vm.save_status = ''
        plan_vm.topo_snapshot.return_value = frozenset({('A_IN', 1, 'entry')})
        plan_vm.available_rows.return_value = {1: 'A_IN'}
        mission_vm.new_queue.side_effect = MissionQueue
        mission_vm.status, mission_vm.version, mission_vm.running = '', 1, False
        mission_vm.missions = ()
        topo_vm, authoring = Mock(), Mock()
        page = PlanPage(plan_vm, mission_vm, Mock(), topo_vm, authoring, None)

        delete = next(b for b in walk(page) if b.kind == 'button' and b.text == '✕')
        await delete.click(None)

        confirm.assert_called_once_with(topo_vm, 1, authoring.delete_row)


async def _completed(*_args) -> None:
    """Stand in for a confirmation dialog the user accepted."""
