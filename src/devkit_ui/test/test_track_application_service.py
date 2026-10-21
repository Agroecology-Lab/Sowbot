"""Track mode's timer, numbering and entry/middle sequence, with the timer and drop mocked."""
import unittest
from unittest.mock import Mock, call

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
        self.recorded.assert_not_called()
        self.assertFalse(self.service.running)
        self.assertEqual(self.service.count, 1)
        self.assertEqual(self.service.stop(), 1)
        self.assertEqual(self.service.count, 0)

    def test_stop_before_start_is_a_no_op(self) -> None:
        """A fresh service has no attempted nodes or timer to cancel."""
        self.assertFalse(self.service.running)
        self.assertEqual(self.service.count, 0)
        self.assertEqual(self.service.stop(), 0)
        self.create_timer.assert_not_called()
        self.drop.assert_not_called()
        self.recorded.assert_not_called()

    def test_drop_precedes_notification_for_every_record(self) -> None:
        """Notify only after each drop, with the first pair before timer creation."""
        events = Mock()
        events.attach_mock(self.drop, 'drop')
        events.attach_mock(self.recorded, 'recorded')
        events.attach_mock(self.create_timer, 'timer')

        self.start()
        callback = self.create_timer.call_args.args[1]
        self.tick()

        self.assertEqual(events.mock_calls, [
            call.drop('ROW_1', None, 'entry'), call.recorded('ROW_1', 1),
            call.timer(2.0, callback),
            call.drop('ROW_2', None, 'entry'), call.recorded('ROW_2', 2),
        ])

    def test_numbering_accepts_an_iterable_and_matches_the_entire_prefix_and_suffix(self) -> None:
        """Zero padding is numeric; partial prefixes and malformed numbers are ignored."""
        names = (name for name in (
            'ROW_A_0007', 'ROW_A_2', 'ROW_90', 'ROW_AB_80', 'OTHER_ROW_A_70',
            'row_a_60', 'ROW_A_-9', 'ROW_A_9.5', 'ROW_A_9_more', 'ROW_A_',
        ))

        self.start(prefix='ROW_A', existing=names)

        self.drop.assert_called_once_with('ROW_A_8', None, 'entry')
        self.recorded.assert_called_once_with('ROW_A_8', 8)
        self.assertEqual(self.service.count, 8)

    def test_refused_start_keeps_original_callbacks_numbering_and_roles(self) -> None:
        """Rejecting a second start must preserve the original recording session."""
        self.start(row_id=4, existing=['ROW_7'])
        other_drop, other_recorded = Mock(), Mock()
        names = iter(['OTHER_99'])

        self.assertFalse(self.service.start(
            'OTHER', 9.0, None, 'exit', names, other_drop, other_recorded))
        self.assertEqual(list(names), ['OTHER_99'])
        self.assertEqual(self.service.count, 8)
        self.tick()

        self.drop.assert_called_with('ROW_9', 4, 'middle')
        self.recorded.assert_called_with('ROW_9', 9)
        other_drop.assert_not_called()
        other_recorded.assert_not_called()
        self.create_timer.assert_called_once()

    def test_restart_uses_fresh_names_and_starts_a_new_row_with_entry(self) -> None:
        """A new session derives numbering from the map and resets the role sequence."""
        first_timer, second_timer = Mock(), Mock()
        self.create_timer.side_effect = [first_timer, second_timer]
        self.start(row_id=4, existing=['ROW_5'])
        self.tick()
        self.assertEqual(self.service.stop(), 7)
        self.assertEqual(self.service.count, 0)

        self.assertTrue(self.start(row_id=0, existing=['ROW_12']))

        first_timer.cancel.assert_called_once_with()
        second_timer.cancel.assert_not_called()
        self.drop.assert_called_with('ROW_13', 0, 'entry')
        self.assertEqual(self.service.count, 13)
        self.tick()
        self.drop.assert_called_with('ROW_14', 0, 'middle')
        self.assertEqual(self.service.stop(), 14)
        second_timer.cancel.assert_called_once_with()

    def test_failed_initial_notification_keeps_the_attempt_but_creates_no_timer(self) -> None:
        """A notification failure propagates after the first drop has completed."""
        self.recorded.side_effect = RuntimeError('status unavailable')

        with self.assertRaisesRegex(RuntimeError, 'status unavailable'):
            self.start()

        self.drop.assert_called_once_with('ROW_1', None, 'entry')
        self.create_timer.assert_not_called()
        self.assertFalse(self.service.running)
        self.assertEqual(self.service.count, 1)
        self.assertEqual(self.service.stop(), 1)

    def test_factory_failure_does_not_undo_the_initial_record(self) -> None:
        """Timer creation errors preserve the synchronous drop and continued number."""
        self.create_timer.side_effect = RuntimeError('timer unavailable')

        with self.assertRaisesRegex(RuntimeError, 'timer unavailable'):
            self.start(existing=['ROW_7'])

        self.drop.assert_called_once_with('ROW_8', None, 'entry')
        self.recorded.assert_called_once_with('ROW_8', 8)
        self.assertFalse(self.service.running)
        self.assertEqual(self.service.count, 8)
        self.assertEqual(self.service.stop(), 8)
        self.assertEqual(self.service.count, 0)

    def test_failed_timer_drop_advances_the_attempt_without_notifying(self) -> None:
        """A failing tick keeps its timer; the next tick uses the next number and role."""
        self.start(row_id=0)
        self.drop.side_effect = OSError('disk full')

        with self.assertRaisesRegex(OSError, 'disk full'):
            self.tick()

        self.drop.assert_called_with('ROW_2', 0, 'middle')
        self.recorded.assert_called_once_with('ROW_1', 1)
        self.assertTrue(self.service.running)
        self.assertEqual(self.service.count, 2)
        self.drop.side_effect = None
        self.tick()
        self.drop.assert_called_with('ROW_3', 0, 'middle')
        self.recorded.assert_called_with('ROW_3', 3)

    def test_failed_timer_notification_keeps_the_timer_and_attempt_number(self) -> None:
        """Notification errors propagate without dropping the active timer or count."""
        self.start()
        self.recorded.side_effect = RuntimeError('status unavailable')

        with self.assertRaisesRegex(RuntimeError, 'status unavailable'):
            self.tick()

        self.drop.assert_called_with('ROW_2', None, 'entry')
        self.recorded.assert_called_with('ROW_2', 2)
        self.assertTrue(self.service.running)
        self.assertEqual(self.service.count, 2)
        self.assertEqual(self.service.stop(), 2)
        self.create_timer.return_value.cancel.assert_called_once_with()

    def test_failed_cancellation_preserves_the_session_for_retry(self) -> None:
        """Do not reset the counter or allow another start while cancellation fails."""
        self.start(row_id=4, existing=['ROW_5'])
        timer = self.create_timer.return_value
        timer.cancel.side_effect = RuntimeError('cancel failed')

        with self.assertRaisesRegex(RuntimeError, 'cancel failed'):
            self.service.stop()

        self.assertTrue(self.service.running)
        self.assertEqual(self.service.count, 6)
        self.assertFalse(self.start(prefix='OTHER'))
        self.tick()
        self.drop.assert_called_with('ROW_7', 4, 'middle')
        timer.cancel.side_effect = None
        self.assertEqual(self.service.stop(), 7)
        self.assertFalse(self.service.running)
        self.assertEqual(self.service.count, 0)
        self.assertEqual(timer.cancel.call_count, 2)


if __name__ == '__main__':
    unittest.main()
