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
