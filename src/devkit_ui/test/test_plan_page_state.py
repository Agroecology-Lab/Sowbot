"""Plan page state, the mission queue and the view model helpers the Plan cards read."""
# pylint: disable=protected-access,attribute-defined-outside-init
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from devkit_ui.plan_types import MIN_CORNERS, PlannedRows
from devkit_ui.view_models.map_context import MapContext
from devkit_ui.view_models.mission_view_model import MissionQueue, MissionViewModel
from devkit_ui.view_models.plan_view_model import (
    STATUS_ERROR,
    STATUS_INFO,
    STATUS_OK,
    PlanPageState,
    PlanView,
    PlanViewModel,
    RepairOffer,
)

FORM = {'tool_width': 1.2, 'angle_deg': 0, 'headland_m': 0, 'snake': True, 'obstacle_pad_m': 0,
        'contour': False, 'recon_path': '', 'dem_res': 1.0}
CORNERS = ((51.0, -2.0), (51.003, -2.0), (51.0, -1.999))


class Node:
    """A hashable topology node stand-in, as the real nodes are."""

    def __init__(self, name, meta) -> None:
        self.name = name
        self.meta = meta


def planned(tag: float = 51.0) -> PlannedRows:
    """Return one swath, distinguishable by tag."""
    return PlannedRows([[(tag, -2.0), (tag + 0.001, -2.0)]], (tag, -2.0))


def good(tag: float = 51.0) -> PlanView:
    """Return a successful planning view."""
    return PlanView(True, 'planned rows', planned(tag))


class TestPlanPageState(unittest.TestCase):
    def setUp(self) -> None:
        """Open a page and draw a complete boundary."""
        self.state = PlanPageState()
        self.changes = Mock()
        self.state.subscribe(self.changes)
        for corner in CORNERS:
            self.state.add_corner(*corner)
        self.changes.reset_mock()

    def test_a_new_page_has_nothing_drawn_and_allows_planning_only(self) -> None:
        """Verify the starting state."""
        state = PlanPageState()

        self.assertEqual(state.corners, [])
        self.assertIsNone(state.draft)
        self.assertEqual(state.swaths, [])
        self.assertEqual((state.can_plan, state.can_save), (True, False))
        self.assertEqual((state.status, state.status_kind), ('', STATUS_INFO))

    def test_pages_do_not_share_a_boundary(self) -> None:
        """Verify two sessions keep separate corners."""
        other = PlanPageState()

        other.add_corner(1.0, 2.0)

        self.assertEqual(len(self.state.corners), 3)
        self.assertEqual(other.corners, [(1.0, 2.0)])

    def test_adding_a_corner_notifies_subscribers(self) -> None:
        """Verify subscribers hear about each corner."""
        self.state.add_corner(51.1, -2.1)

        self.assertEqual(self.state.corners[-1], (51.1, -2.1))
        self.changes.assert_called_once_with()

    def test_too_few_corners_does_not_start_and_reports_it(self) -> None:
        """Verify planning needs MIN_CORNERS corners and leaves planning enabled."""
        state = PlanPageState()
        state.add_corner(51.0, -2.0)

        self.assertIsNone(state.begin_plan([], **FORM))

        self.assertEqual(state.status, f'Need at least {MIN_CORNERS} corners')
        self.assertEqual(state.status_kind, STATUS_ERROR)
        self.assertTrue(state.can_plan)

    def test_beginning_a_plan_disables_planning_and_copies_the_boundary(self) -> None:
        """Verify the request carries the boundary and rings, and the status shows progress."""
        rings = [[(51.0, -2.0)]]

        run = self.state.begin_plan(rings, **FORM)

        self.assertEqual(run.request.corners_ll, list(CORNERS))
        self.assertIsNot(run.request.corners_ll, self.state.corners)
        self.assertEqual(run.request.obstacle_rings, rings)
        self.assertFalse(self.state.can_plan)
        self.assertEqual((self.state.status, self.state.status_kind), ('Running F2C…', STATUS_INFO))

    def test_invalid_form_values_raise_before_anything_changes(self) -> None:
        """Verify a bad number leaves the page as it was."""
        with self.assertRaises(ValueError):
            self.state.begin_plan([], **{**FORM, 'tool_width': 'wide'})

        self.assertTrue(self.state.can_plan)
        self.assertEqual(self.state.status, '')

    def test_a_current_result_becomes_the_draft_and_enables_save(self) -> None:
        """Verify a finished run supplies the exact rows to save."""
        run = self.state.begin_plan([], **FORM)
        view = good()

        self.assertTrue(self.state.finish_plan(run, view))

        self.assertIs(self.state.draft, view.planned)
        self.assertEqual(self.state.swaths, view.planned.swaths)
        self.assertEqual((self.state.can_plan, self.state.can_save), (True, True))
        self.assertEqual((self.state.status, self.state.status_kind), ('planned rows', STATUS_OK))

    def test_a_result_with_no_rows_does_not_enable_save(self) -> None:
        """Verify an empty plan cannot be saved."""
        run = self.state.begin_plan([], **FORM)

        self.state.finish_plan(run, PlanView(True, 'none', PlannedRows([], None)))

        self.assertFalse(self.state.can_save)

    def test_a_failed_run_keeps_the_earlier_draft(self) -> None:
        """Verify failure shows the error and leaves the last good plan saveable."""
        first = self.state.begin_plan([], **FORM)
        view = good()
        self.state.finish_plan(first, view)
        second = self.state.begin_plan([], **FORM)

        self.state.finish_plan(second, PlanView(False, 'Planning failed: bad field'))

        self.assertIs(self.state.draft, view.planned)
        self.assertTrue(self.state.can_save)
        self.assertTrue(self.state.can_plan)
        self.assertEqual((self.state.status, self.state.status_kind),
                         ('Planning failed: bad field', STATUS_ERROR))

    def test_clear_resets_the_page_and_allows_planning(self) -> None:
        """Verify Clear drops corners, draft and status, even while a run is in flight."""
        self.state.begin_plan([], **FORM)

        self.state.clear()

        self.assertEqual(self.state.corners, [])
        self.assertIsNone(self.state.draft)
        self.assertEqual((self.state.can_plan, self.state.can_save), (True, False))
        self.assertEqual(self.state.status, '')
        self.changes.assert_called()

    def test_clear_discards_a_pending_success_and_a_pending_failure(self) -> None:
        """Verify cleared work cannot restore rows, a draft, a status or an enabled Save."""
        for view in (good(), PlanView(False, 'old failure')):
            with self.subTest(ok=view.ok):
                self.state.clear()
                for corner in CORNERS:
                    self.state.add_corner(*corner)
                run = self.state.begin_plan([], **FORM)
                self.state.clear()
                self.changes.reset_mock()

                self.assertFalse(self.state.finish_plan(run, view))

                self.assertIsNone(self.state.draft)
                self.assertEqual(self.state.status, '')
                self.assertFalse(self.state.can_save)
                self.changes.assert_not_called()

    def test_an_old_result_cannot_disable_planning_during_a_new_run(self) -> None:
        """Verify a late predecessor changes nothing while its successor runs."""
        old = self.state.begin_plan([], **FORM)
        self.state.clear()
        self.assertTrue(self.state.can_plan)
        for corner in CORNERS:
            self.state.add_corner(*corner)
        current = self.state.begin_plan([], **FORM)

        self.assertFalse(self.state.finish_plan(old, good()))

        self.assertFalse(self.state.can_plan)
        self.assertTrue(self.state.finish_plan(current, good()))

    def test_an_old_result_cannot_replace_a_completed_new_run(self) -> None:
        """Verify a late predecessor keeps the new draft."""
        old = self.state.begin_plan([], **FORM)
        self.state.clear()
        for corner in CORNERS:
            self.state.add_corner(*corner)
        current = self.state.begin_plan([], **FORM)
        view = good(52.0)
        self.state.finish_plan(current, view)

        self.state.finish_plan(old, good(53.0))

        self.assertIs(self.state.draft, view.planned)

    def test_a_new_run_without_clear_discards_the_previous_result(self) -> None:
        """Starting another run invalidates earlier work even if the boundary is unchanged."""
        for stale in (good(52.0), PlanView(False, 'old failure')):
            with self.subTest(ok=stale.ok):
                first = self.state.begin_plan([], **FORM)
                current = self.state.begin_plan([], **FORM)
                self.changes.reset_mock()

                self.assertFalse(self.state.finish_plan(first, stale))

                self.assertFalse(self.state.can_plan)
                self.assertEqual(self.state.status, 'Running F2C…')
                self.changes.assert_not_called()
                latest = good(53.0)
                self.assertTrue(self.state.finish_plan(current, latest))
                self.assertIs(self.state.draft, latest.planned)

    def test_invalid_form_does_not_invalidate_an_already_running_plan(self) -> None:
        """A rejected form preserves the draft, status and validity of pending work."""
        earlier = good()
        self.state.apply_import(earlier)
        run = self.state.begin_plan([], **FORM)
        self.changes.reset_mock()

        with self.assertRaises(TypeError):
            self.state.begin_plan([], **{**FORM, 'dem_res': object()})

        self.assertIs(self.state.draft, earlier.planned)
        self.assertEqual((self.state.can_plan, self.state.can_save), (False, True))
        self.assertEqual((self.state.status, self.state.status_kind), ('Running F2C…', STATUS_INFO))
        self.changes.assert_not_called()
        latest = good(52.0)
        self.assertTrue(self.state.finish_plan(run, latest))
        self.assertIs(self.state.draft, latest.planned)

    def test_a_good_import_becomes_the_draft(self) -> None:
        """Verify an imported plan is saveable and shown as a success."""
        view = good()

        self.state.apply_import(view)

        self.assertIs(self.state.draft, view.planned)
        self.assertTrue(self.state.can_save)
        self.assertEqual(self.state.status_kind, STATUS_OK)

    def test_a_failed_import_keeps_the_draft_but_disables_save(self) -> None:
        """Verify an unreadable file is reported and cannot be saved over the earlier draft."""
        view = good()
        self.state.apply_import(view)

        self.state.apply_import(PlanView(False, 'ERROR: not a plan'))

        self.assertIs(self.state.draft, view.planned)
        self.assertFalse(self.state.can_save)
        self.assertEqual((self.state.status, self.state.status_kind),
                         ('ERROR: not a plan', STATUS_ERROR))

    def test_a_good_import_discards_a_pending_success_and_a_pending_failure(self) -> None:
        """Verify a late planning result cannot replace imported rows or their status."""
        for pending in (good(52.0), PlanView(False, 'old failure')):
            with self.subTest(ok=pending.ok):
                run = self.state.begin_plan([], **FORM)
                imported = PlanView(True, 'imported rows', planned(53.0))

                self.state.apply_import(imported)

                self.assertTrue(self.state.can_plan)
                self.changes.reset_mock()
                self.assertFalse(self.state.finish_plan(run, pending))
                self.assertIs(self.state.draft, imported.planned)
                self.assertEqual((self.state.can_plan, self.state.can_save), (True, True))
                self.assertEqual(
                    (self.state.status, self.state.status_kind), ('imported rows', STATUS_OK))
                self.changes.assert_not_called()

    def test_a_failed_import_keeps_the_pending_run_current(self) -> None:
        """Verify an import error preserves the draft and allows pending planning to finish."""
        earlier = good()
        self.state.apply_import(earlier)
        run = self.state.begin_plan([], **FORM)

        self.state.apply_import(PlanView(False, 'ERROR: not a plan'))

        self.assertIs(self.state.draft, earlier.planned)
        self.assertEqual((self.state.can_plan, self.state.can_save), (False, False))
        self.assertEqual(
            (self.state.status, self.state.status_kind), ('ERROR: not a plan', STATUS_ERROR))
        current = good(52.0)
        self.assertTrue(self.state.finish_plan(run, current))
        self.assertIs(self.state.draft, current.planned)
        self.assertEqual((self.state.can_plan, self.state.can_save), (True, True))


class TestMissionQueue(unittest.TestCase):
    def setUp(self) -> None:
        """Start with an empty queue and a subscriber."""
        self.queue = MissionQueue()
        self.changes = Mock()
        self.queue.subscribe(self.changes)

    def test_add_appends_and_allows_duplicates(self) -> None:
        """Verify the same row may be queued twice with different implements."""
        self.queue.add(1, 'drive', {})
        self.queue.add(1, 'weed', {'a': 1})

        self.assertEqual(self.queue.items, [(1, 'drive', {}), (1, 'weed', {'a': 1})])
        self.assertEqual(self.changes.call_count, 2)

    def test_move_swaps_neighbours(self) -> None:
        """Verify moving a step down, then up, reorders the queue."""
        for row in (1, 2, 3):
            self.queue.add(row, 'drive', {})

        self.queue.move(0, 1)
        self.assertEqual([r for r, _, _ in self.queue.items], [2, 1, 3])
        self.queue.move(2, -1)
        self.assertEqual([r for r, _, _ in self.queue.items], [2, 3, 1])

    def test_move_off_either_end_does_nothing(self) -> None:
        """Verify a step cannot leave the queue, and nothing is announced."""
        self.queue.add(1, 'drive', {})
        self.queue.add(2, 'drive', {})
        self.changes.reset_mock()

        self.queue.move(0, -1)
        self.queue.move(1, 1)
        self.queue.move(5, -1)

        self.assertEqual([r for r, _, _ in self.queue.items], [1, 2])
        self.changes.assert_not_called()

    def test_remove_drops_one_step_and_ignores_a_bad_position(self) -> None:
        """Verify removal by position."""
        self.queue.add(1, 'drive', {})
        self.queue.add(2, 'drive', {})
        self.changes.reset_mock()

        self.queue.remove(9)
        self.changes.assert_not_called()
        self.queue.remove(0)

        self.assertEqual([r for r, _, _ in self.queue.items], [2])
        self.changes.assert_called_once_with()

    def test_the_view_model_hands_out_separate_queues(self) -> None:
        """Verify each session gets its own queue."""
        vm = MissionViewModel(Mock(), Mock())

        first, second = vm.new_queue(), vm.new_queue()
        first.add(1, 'drive', {})

        self.assertEqual(second.items, [])


class TestPlanViewModelPageHelpers(unittest.TestCase):
    def setUp(self) -> None:
        """Build the view model over a map with two rows and a stand-alone node."""
        self.doc = SimpleNamespace(
            nodes=[
                Node('A_1', {'row_id': 1, 'row_role': 'entry'}),
                Node('A_1_OUT', {'row_id': 1, 'row_role': 'exit'}),
                Node('B_2', {'row_id': '2', 'row_role': 'entry'}),
                Node('C_bad', {'row_id': 'x', 'row_role': 'entry'}),
                Node('HOME', {}),
            ],
        )
        self.doc.has_node = lambda name: name in {n.name for n in self.doc.nodes}
        self.topo = SimpleNamespace(topo_doc=self.doc, current_node='—', selected_node=None)
        self.app = Mock()
        self.vm = PlanViewModel(self.app, MapContext(self.topo, Mock()))

    def test_open_page_returns_fresh_state_each_time(self) -> None:
        """Verify pages never share state."""
        first, second = self.vm.open_page(), self.vm.open_page()

        self.assertIsInstance(first, PlanPageState)
        self.assertIsNot(first, second)

    def test_available_rows_are_entry_nodes_by_integer_row_id(self) -> None:
        """Verify exits, non-row nodes and unparseable row ids are skipped."""
        self.assertEqual(self.vm.available_rows(), {1: 'A_1', 2: 'B_2'})

    def test_available_rows_is_empty_without_a_map(self) -> None:
        """Verify no map means no rows."""
        self.topo.topo_doc = None

        self.assertEqual(self.vm.available_rows(), {})

    def test_snapshot_is_none_without_a_map(self) -> None:
        """Verify a missing map is distinguishable from an empty one."""
        self.topo.topo_doc = None
        self.assertIsNone(self.vm.topo_snapshot())
        self.topo.topo_doc = SimpleNamespace(nodes=[])
        self.assertEqual(self.vm.topo_snapshot(), frozenset())

    def test_snapshot_changes_when_a_node_is_added_or_its_row_metadata_is_edited(self) -> None:
        """Verify the snapshot differs after an added node and after an in-place edit."""
        before = self.vm.topo_snapshot()

        self.doc.nodes.append(Node('NEW', {}))
        added = self.vm.topo_snapshot()
        self.doc.nodes[0].meta['row_id'] = 9

        self.assertNotEqual(added, before)
        self.assertNotEqual(self.vm.topo_snapshot(), added)

    def test_snapshot_is_stable_while_the_map_is_unchanged(self) -> None:
        """Verify two reads of the same map compare equal, even through different node objects."""
        before = self.vm.topo_snapshot()
        self.doc.nodes = [Node(n.name, dict(n.meta)) for n in self.doc.nodes]

        self.assertEqual(self.vm.topo_snapshot(), before)

    def test_repair_offer_is_none_without_a_map(self) -> None:
        """Verify there is nothing to repair without a map."""
        self.topo.topo_doc = None

        self.assertIsNone(self.vm.repair_offer())

    def test_repair_offer_counts_entry_nodes_and_suggests_the_current_node(self) -> None:
        """Verify the dialog is told the row count and the robot's node."""
        self.topo.current_node = 'HOME'
        self.topo.selected_node = 'A_1'

        self.assertEqual(self.vm.repair_offer(), RepairOffer(3, 'HOME'))

    def test_repair_offer_falls_back_to_the_selected_node(self) -> None:
        """Verify an unknown or placeholder current node is ignored."""
        self.topo.selected_node = 'A_1'
        for current in ('—', 'none', 'None', '', None, 'GONE'):
            with self.subTest(current=current):
                self.topo.current_node = current

                self.assertEqual(self.vm.repair_offer().default_base, 'A_1')

    def test_repair_offer_suggests_nothing_when_neither_node_exists(self) -> None:
        """Verify the base is blank rather than a node that is not in the map."""
        self.topo.current_node = 'GONE'
        self.topo.selected_node = 'ALSO_GONE'

        self.assertEqual(self.vm.repair_offer().default_base, '')


if __name__ == '__main__':
    unittest.main()
