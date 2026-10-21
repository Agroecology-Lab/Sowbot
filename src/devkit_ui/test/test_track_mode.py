"""Verify track mode records nodes on a gateway timer and marks the row exit when stopped."""
# pylint: disable=protected-access
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from devkit_ui.models import TopoDoc, TopoNode
from topo_test_fakes import attach_authoring


class TestTrackMode(unittest.TestCase):
    def setUp(self) -> None:
        """Build the authoring view model over a loaded map, with drops replaced by a mock."""
        self.node = SimpleNamespace(_topo_doc=TopoDoc(name='field.yaml'))
        self.authoring = attach_authoring(self.node, 'unused')
        self.node._topo_vm.update_doc(self.node._topo_doc)
        self.authoring.drop_node = Mock()
        self.track = self.node._run_vm.track
        self.node._topo_app_service.patch_node_role_async = Mock()

    def test_track_timer_uses_the_injected_factory(self) -> None:
        """Verify start records one node at once, then creates the repeating timer."""
        self.authoring.start_track('row a', 2.5, None, 'entry')

        self.node.create_timer.assert_called_once()
        period, callback = self.node.create_timer.call_args.args
        self.assertEqual(period, 2.5)
        self.assertTrue(self.node.track_service.running)
        self.assertEqual(self.authoring.drop_node.call_args_list[0].args, ('ROW_A_1', None, 'entry'))

        callback()

        self.assertEqual(self.authoring.drop_node.call_args_list[1].args, ('ROW_A_2', None, 'entry'))

    def test_track_timer_not_created_when_prefix_missing(self) -> None:
        """Verify an empty prefix reports an error and creates no timer."""
        self.authoring.start_track('  ', 2.5, None, None)

        self.node.create_timer.assert_not_called()
        self.assertFalse(self.node.track_service.running)
        self.assertEqual(self.track.status, 'ERROR: prefix required')

    def test_missing_map_reports_an_error_and_creates_no_timer(self) -> None:
        """Verify starting without a loaded map reports it instead of raising."""
        self.node._topo_vm.update_doc(None)

        self.authoring.start_track('row', 2.5, None, None)

        self.node.create_timer.assert_not_called()
        self.assertEqual(self.track.status, 'ERROR: map not loaded')
        self.assertFalse(self.track.running)

    def test_existing_timer_prevents_duplicate_timer_and_immediate_drop(self) -> None:
        """Starting twice must keep the original timer and its recording callback."""
        self.authoring.start_track('row', 1.0, 2, 'entry')
        self.authoring.drop_node.reset_mock()

        self.authoring.start_track('other', 5.0, 3, 'entry')

        self.node.create_timer.assert_called_once()
        self.authoring.drop_node.assert_not_called()
        self.assertTrue(self.node.track_service.running)
        self.assertEqual(self.track.status, 'ERROR: already running')
        self.assertTrue(self.track.running)

    def test_callback_preserves_row_id_and_advances_from_entry_to_middle(self) -> None:
        """A timer callback must retain row zero and mark only the first node as entry."""
        self.authoring.start_track('row', 0.5, 0, 'exit')
        callback = self.node.create_timer.call_args.args[1]
        callback()
        callback()

        self.assertEqual([c.args for c in self.authoring.drop_node.call_args_list], [
            ('ROW_1', 0, 'entry'), ('ROW_2', 0, 'middle'), ('ROW_3', 0, 'middle'),
        ])
        self.assertEqual(self.track.status, 'recording  ROW_3  (#3)')

    def test_numbering_continues_after_existing_nodes_with_the_prefix(self) -> None:
        """Verify new nodes continue the highest existing number for the prefix."""
        for name in ('ROW_1', 'ROW_7', 'ROW_X', 'OTHER_9'):
            self.node._topo_doc.insert_node(TopoNode(name=name))

        self.authoring.start_track('row', 1.0, None, 'entry')

        self.assertEqual(self.authoring.drop_node.call_args.args[0], 'ROW_8')

    def test_stop_marks_the_last_row_node_as_exit_and_resets_state(self) -> None:
        """Verify stopping a row track cancels the timer, patches the last node and resets."""
        self.authoring.start_track('row', 1.0, 4, 'entry')
        self.node.create_timer.call_args.args[1]()

        self.authoring.stop_track()

        self.node.create_timer.return_value.cancel.assert_called_once_with()
        self.node._topo_app_service.patch_node_role_async.assert_called_once_with('ROW_2', 'exit')
        self.assertEqual(self.track.status, 'stopped — ROW_2 marked exit  (#2 nodes)')
        self.assertFalse(self.track.running)
        self.assertEqual((self.track.prefix, self.track.row_id, self.track.row_role),
                         ('', None, 'entry'))
        self.assertFalse(self.node.track_service.running)

    def test_stop_without_a_row_only_reports_the_count(self) -> None:
        """Verify a standard track marks no exit node."""
        self.authoring.start_track('nav', 1.0, None, 'entry')

        self.authoring.stop_track()

        self.node._topo_app_service.patch_node_role_async.assert_not_called()
        self.assertEqual(self.track.status, 'stopped at #1')

    def test_missing_role_defaults_to_entry_for_each_plain_track_node(self) -> None:
        """Both an unset and an empty role use the view model's entry default."""
        for role in (None, ''):
            with self.subTest(role=role):
                self.authoring.drop_node.reset_mock()

                self.authoring.start_track('nav', 1.0, None, role)
                self.node.create_timer.call_args.args[1]()

                self.assertEqual(self.track.row_role, 'entry')
                self.assertEqual([c.args for c in self.authoring.drop_node.call_args_list], [
                    ('NAV_1', None, 'entry'), ('NAV_2', None, 'entry'),
                ])
                self.assertEqual(self.track.status, 'recording  NAV_2  (#2)')
                self.authoring.stop_track()

    def test_running_check_precedes_missing_map_and_keeps_original_settings(self) -> None:
        """A refused restart preserves the active session even if the map was unloaded."""
        self.authoring.start_track('row', 1.0, 0, 'exit')
        self.node._topo_vm.update_doc(None)
        self.track.running = False

        self.authoring.start_track('other', 5.0, 9, 'waypoint')

        self.assertEqual(self.track.status, 'ERROR: already running')
        self.assertTrue(self.track.running)
        self.assertEqual((self.track.prefix, self.track.interval, self.track.row_id,
                          self.track.row_role), ('ROW', 1.0, 0, 'exit'))
        self.node.create_timer.assert_called_once()
        self.node.create_timer.call_args.args[1]()
        self.authoring.drop_node.assert_called_with('ROW_2', 0, 'middle')

    def test_empty_prefix_is_checked_before_an_active_track(self) -> None:
        """An invalid start reports the prefix error while preserving the existing timer."""
        self.authoring.start_track('row', 1.0, 0, 'entry')
        self.authoring.drop_node.reset_mock()

        self.authoring.start_track('  ', 5.0, None, None)

        self.assertEqual(self.track.status, 'ERROR: prefix required')
        self.assertFalse(self.track.running)
        self.assertTrue(self.node.track_service.running)
        self.assertEqual(self.track.prefix, 'ROW')
        self.node.create_timer.assert_called_once()
        self.authoring.drop_node.assert_not_called()

    def test_stop_row_zero_uses_the_continued_number_for_the_exit(self) -> None:
        """Row zero is a row; the stop status and exit use the existing map's numbering."""
        self.node._topo_doc.insert_node(TopoNode(name='ROW_7'))
        self.authoring.start_track('row', 1.0, 0, 'entry')

        self.authoring.stop_track()

        self.node._topo_app_service.patch_node_role_async.assert_called_once_with('ROW_8', 'exit')
        self.assertEqual(self.track.status, 'stopped — ROW_8 marked exit  (#8 nodes)')
        self.assertFalse(self.track.running)
        self.assertEqual(self.node.track_service.count, 0)

    def test_stopping_twice_does_not_patch_another_exit(self) -> None:
        """Repeated stop is harmless after the first stop clears the row settings."""
        self.authoring.start_track('row', 1.0, 4, 'entry')
        self.authoring.stop_track()

        self.authoring.stop_track()

        self.node.create_timer.return_value.cancel.assert_called_once_with()
        self.node._topo_app_service.patch_node_role_async.assert_called_once_with('ROW_1', 'exit')
        self.assertEqual(self.track.status, 'stopped at #0')
        self.assertFalse(self.track.running)

    def test_stopping_before_start_does_not_schedule_an_exit(self) -> None:
        """Stale row fields alone do not imply that a node has been recorded."""
        self.track.prefix, self.track.row_id, self.track.row_role = 'ROW', 0, 'exit'

        self.authoring.stop_track()

        self.node.create_timer.assert_not_called()
        self.node._topo_app_service.patch_node_role_async.assert_not_called()
        self.assertEqual(self.track.status, 'stopped at #0')
        self.assertEqual((self.track.prefix, self.track.row_id, self.track.row_role),
                         ('', None, 'entry'))

    def test_failed_cancellation_does_not_clear_ui_or_schedule_an_exit(self) -> None:
        """Cancellation errors propagate before any UI fields or row roles change."""
        self.authoring.start_track('row', 1.0, 4, 'entry')
        before = vars(self.track).copy()
        self.node.create_timer.return_value.cancel.side_effect = RuntimeError('cancel failed')

        with self.assertRaisesRegex(RuntimeError, 'cancel failed'):
            self.authoring.stop_track()

        self.assertEqual(vars(self.track), before)
        self.assertTrue(self.node.track_service.running)
        self.node._topo_app_service.patch_node_role_async.assert_not_called()

    def test_exit_scheduling_error_propagates_before_ui_reset(self) -> None:
        """The timer stops even when scheduling the exit edit raises an error."""
        self.authoring.start_track('row', 1.0, 4, 'entry')
        before = vars(self.track).copy()
        patch_role = self.node._topo_app_service.patch_node_role_async
        patch_role.side_effect = RuntimeError('worker unavailable')

        with self.assertRaisesRegex(RuntimeError, 'worker unavailable'):
            self.authoring.stop_track()

        self.node.create_timer.return_value.cancel.assert_called_once_with()
        patch_role.assert_called_once_with('ROW_1', 'exit')
        self.assertFalse(self.node.track_service.running)
        self.assertEqual(self.node.track_service.count, 0)
        self.assertEqual(vars(self.track), before)

    def test_drop_validation_failure_reports_separate_status_and_keeps_tracking(self) -> None:
        """A rejected drop must not prevent the timer from attempting the next node."""
        node = SimpleNamespace(_topo_doc=TopoDoc(name='field.yaml'))
        authoring = attach_authoring(node, 'unused', is_sim=False)
        node._topo_vm.update_doc(node._topo_doc)
        node.telemetry.latest_odom = None

        authoring.start_track('row', 1.0, None, None)
        first_error = node._run_vm.drop_node.status
        node.create_timer.call_args.args[1]()

        self.assertEqual(first_error, 'ERROR: no odometry')
        self.assertEqual(node._run_vm.drop_node.status, first_error)
        self.assertEqual(node._run_vm.track.status, 'recording  ROW_2  (#2)')
        self.assertTrue(node._run_vm.track.running)
        self.assertTrue(node.track_service.running)
        self.assertEqual(node.track_service.count, 2)
        self.assertEqual(list(node._topo_doc.nodes), [])

    def test_timer_creation_error_preserves_the_initial_record_and_ui_state(self) -> None:
        """The initial synchronous drop is not rolled back when timer creation fails."""
        self.node.create_timer.side_effect = RuntimeError('timer unavailable')

        with self.assertRaisesRegex(RuntimeError, 'timer unavailable'):
            self.authoring.start_track('row', 1.0, 4, 'exit')

        self.authoring.drop_node.assert_called_once_with('ROW_1', 4, 'entry')
        self.assertEqual(self.track.status, 'recording  ROW_1  (#1)')
        self.assertTrue(self.track.running)
        self.assertFalse(self.node.track_service.running)
        self.assertEqual(self.node.track_service.count, 1)

    def test_unexpected_initial_drop_error_propagates_without_creating_a_timer(self) -> None:
        """An uncaught drop error leaves the attempted number and configured UI state."""
        self.authoring.drop_node.side_effect = OSError('disk full')

        with self.assertRaisesRegex(OSError, 'disk full'):
            self.authoring.start_track('row', 1.0, 4, None)

        self.node.create_timer.assert_not_called()
        self.assertFalse(self.node.track_service.running)
        self.assertEqual(self.node.track_service.count, 1)
        self.assertTrue(self.track.running)
        self.assertEqual((self.track.prefix, self.track.row_id, self.track.row_role),
                         ('ROW', 4, 'entry'))
        self.assertEqual(self.track.status, '')
