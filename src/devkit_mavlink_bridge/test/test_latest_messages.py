"""LatestMessages keeps the newest message per kind with its age."""

import unittest

from devkit_mavlink_bridge.modules.latest_messages import LatestMessages


class LatestMessagesTest(unittest.TestCase):
    def setUp(self):
        """Create a store on a controllable clock."""
        self.now = 100.0
        self.store = LatestMessages(clock=lambda: self.now)

    def test_unknown_kind_has_no_message_and_no_age(self):
        """Verify nothing is invented before a message arrives."""
        self.assertIsNone(self.store.fresh('a', 1.0))
        self.assertIsNone(self.store.age_s('a'))

    def test_message_is_fresh_until_older_than_the_limit(self):
        """Verify the boundary: exactly the limit is still fresh."""
        self.store.put('a', 'one')
        self.now += 1.0
        self.assertEqual(self.store.fresh('a', 1.0), 'one')
        self.assertEqual(self.store.age_s('a'), 1.0)
        self.now += 0.001
        self.assertIsNone(self.store.fresh('a', 1.0))

    def test_newer_message_replaces_older_and_resets_age(self):
        """Verify only the latest message of a kind is kept."""
        self.store.put('a', 'one')
        self.now += 5.0
        self.store.put('a', 'two')
        self.assertEqual(self.store.fresh('a', 1.0), 'two')

    def test_clear_forgets_everything(self):
        """Verify clear drops all kinds."""
        self.store.put('a', 'one')
        self.store.put('b', 'two')
        self.store.clear()
        self.assertIsNone(self.store.fresh('a', 10.0))
        self.assertIsNone(self.store.fresh('b', 10.0))

    def test_message_kinds_keep_independent_arrival_times(self):
        """Verify refreshing GPS cannot keep an old attitude alive."""
        attitude, gps = object(), object()
        self.store.put('attitude', attitude)
        self.now += 2.0
        self.store.put('gps', gps)
        self.assertIsNone(self.store.fresh('attitude', 1.0))
        self.assertEqual(self.store.age_s('attitude'), 2.0)
        self.assertIs(self.store.fresh('gps', 1.0), gps)
        self.assertEqual(self.store.age_s('gps'), 0.0)

    def test_freshness_limit_is_applied_per_read_without_discarding_the_message(self):
        """Verify a strict consumer does not delete data still fresh for another consumer."""
        message = object()
        self.store.put('a', message)
        self.now += 2.0
        self.assertIsNone(self.store.fresh('a', 1.0))
        self.assertIs(self.store.fresh('a', 3.0), message)
        self.assertEqual(self.store.age_s('a'), 2.0)

    def test_clear_resets_ages_and_allows_a_new_session(self):
        """Verify a new link session gets new arrival times and no old message kinds."""
        self.store.put('a', 'old')
        self.store.put('b', 'old')
        self.store.clear()
        self.assertIsNone(self.store.age_s('a'))
        self.assertIsNone(self.store.age_s('b'))
        self.now += 10.0
        self.store.put('a', 'new')
        self.assertEqual(self.store.fresh('a', 0.0), 'new')
        self.assertEqual(self.store.age_s('a'), 0.0)
        self.assertIsNone(self.store.fresh('b', 100.0))

    def test_repeated_reads_do_not_refresh_arrival_time(self):
        """Verify diagnostic consumers cannot keep a stopped telemetry stream fresh."""
        message = object()
        self.store.put('attitude', message)
        for elapsed in (0.25, 0.5, 1.0):
            with self.subTest(elapsed=elapsed):
                self.now = 100.0 + elapsed
                self.assertIs(self.store.fresh('attitude', 1.0), message)
                self.assertEqual(self.store.age_s('attitude'), elapsed)
        self.now += 0.001
        self.assertIsNone(self.store.fresh('attitude', 1.0))

    def test_zero_age_limit_expires_when_the_clock_advances(self):
        """Verify a zero age limit admits only messages arriving at the current instant."""
        message = object()
        self.store.put('attitude', message)
        self.assertIs(self.store.fresh('attitude', 0.0), message)
        self.now += 0.001
        self.assertIsNone(self.store.fresh('attitude', 0.0))

    def test_separate_stores_cannot_share_or_clear_each_others_messages(self):
        """Verify reconnecting one bridge cannot clear another bridge's telemetry."""
        other = LatestMessages(clock=lambda: self.now)
        first, second = object(), object()
        self.store.put('attitude', first)
        self.assertIsNone(other.fresh('attitude', 1.0))
        other.put('attitude', second)
        self.store.clear()
        self.assertIs(other.fresh('attitude', 1.0), second)
        self.assertIsNone(self.store.fresh('attitude', 1.0))


if __name__ == '__main__':
    unittest.main()
