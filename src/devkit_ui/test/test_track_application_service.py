"""Track mode's timer, numbering and entry/middle sequence, with the timer and drop mocked."""
import unittest
from unittest.mock import Mock

from devkit_ui.application_services.track_application_service import TrackApplicationService


class TestTrackApplicationService(unittest.TestCase):
    def setUp(self) -> None:
        """Build the service on a mock timer factory with mock drop and status callbacks."""
        self.create_timer = Mock()
        self.service = TrackApplicationService(self.create_timer)
        self.drop, self.recorded = Mock(), Mock()

    def start(self, prefix='ROW', interval=2.0, row_id=None, row_role='entry', existing=()):
        """Start a track with the mock callbacks and return the service's answer."""
        return self.service.start(
            prefix, interval, row_id, row_role, existing, self.drop, self.recorded)

    def tick(self) -> None:
        """Fire the repeating timer's callback once."""
        self.create_timer.call_args.args[1]()

    def test_start_records_one_node_at_once_then_creates_the_timer(self) -> None:
        """Verify the first node is dropped before the timer exists, at the given interval."""
        self.drop.side_effect = lambda *_: self.create_timer.assert_not_called()

        self.assertTrue(self.start(interval=2.5))

        self.drop.assert_called_once_with('ROW_1', None, 'entry')
        self.recorded.assert_called_once_with('ROW_1', 1)
        self.assertEqual(self.create_timer.call_args.args[0], 2.5)
        self.assertTrue(self.service.running)

    def test_each_tick_records_the_next_number(self) -> None:
        """Verify ticks continue the numbering and report each node."""
        self.start()
        self.tick()
        self.tick()

        self.assertEqual([c.args[0] for c in self.drop.call_args_list], ['ROW_1', 'ROW_2', 'ROW_3'])
        self.assertEqual(self.recorded.call_args.args, ('ROW_3', 3))
        self.assertEqual(self.service.count, 3)

    def test_a_row_track_is_entry_then_middle_and_keeps_row_zero(self) -> None:
        """Verify only the first node of a row is an entry, including for row id 0."""
        self.start(row_id=0, row_role='exit')
        self.tick()
        self.tick()

        self.assertEqual([c.args for c in self.drop.call_args_list],
                         [('ROW_1', 0, 'entry'), ('ROW_2', 0, 'middle'), ('ROW_3', 0, 'middle')])

    def test_a_plain_track_uses_the_given_role_for_every_node(self) -> None:
        """Verify without a row id every node gets the role passed in."""
        self.start(row_role='waypoint')
        self.tick()

        self.assertEqual([c.args[2] for c in self.drop.call_args_list], ['waypoint', 'waypoint'])

    def test_numbering_continues_after_the_highest_existing_node_with_the_prefix(self) -> None:
        """Verify other prefixes and non-numeric suffixes are ignored."""
        self.start(existing=['ROW_1', 'ROW_7', 'ROW_X', 'OTHER_9', 'ROW_'])

        self.assertEqual(self.drop.call_args.args[0], 'ROW_8')

    def test_second_start_is_refused_and_changes_nothing(self) -> None:
        """Verify a running track keeps its timer and drops nothing."""
        self.start()
        self.drop.reset_mock()

        self.assertFalse(self.start(prefix='OTHER', interval=9.0))

        self.drop.assert_not_called()
        self.create_timer.assert_called_once()

    def test_stop_cancels_the_timer_and_returns_the_last_number(self) -> None:
        """Verify stop cancels once, returns the count and resets for the next track."""
        self.start()
        self.tick()

        self.assertEqual(self.service.stop(), 2)

        self.create_timer.return_value.cancel.assert_called_once_with()
        self.assertFalse(self.service.running)
        self.assertEqual(self.service.stop(), 0)
        self.create_timer.return_value.cancel.assert_called_once_with()
        self.drop.reset_mock()
        self.start()
        self.assertEqual(self.drop.call_args.args[:3], ('ROW_1', None, 'entry'))

    def test_a_failed_first_drop_leaves_nothing_running(self) -> None:
        """Verify an error from the first drop propagates and no timer is created."""
        self.drop.side_effect = OSError('disk full')

        with self.assertRaises(OSError):
            self.start()

        self.create_timer.assert_not_called()
        self.assertFalse(self.service.running)


if __name__ == '__main__':
    unittest.main()
