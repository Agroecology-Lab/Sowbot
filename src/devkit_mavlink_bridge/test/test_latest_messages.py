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


if __name__ == '__main__':
    unittest.main()
