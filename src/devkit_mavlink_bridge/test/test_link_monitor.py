"""HeartbeatMonitor reports never, ok and lost from an injected clock."""

import unittest

from devkit_mavlink_bridge.modules.link_monitor import LOST, NEVER, OK, HeartbeatMonitor


class HeartbeatMonitorTest(unittest.TestCase):
    """State transitions of the flight-controller link."""

    def setUp(self):
        """Create a monitor with a 3 s timeout and a controllable clock."""
        self.now = 10.0
        self.monitor = HeartbeatMonitor(3.0, clock=lambda: self.now)

    def test_never_before_the_first_heartbeat(self):
        """Verify a link that has not been seen is not reported as ok."""
        self.assertEqual(self.monitor.state(), NEVER)
        self.now += 100.0
        self.assertEqual(self.monitor.state(), NEVER)

    def test_ok_until_the_timeout_then_lost(self):
        """Verify exactly the timeout is still ok and anything older is lost."""
        self.monitor.beat()
        self.now += 3.0
        self.assertEqual(self.monitor.state(), OK)
        self.now += 0.01
        self.assertEqual(self.monitor.state(), LOST)

    def test_new_heartbeat_restores_the_link(self):
        """Verify a heartbeat after a loss makes the link ok again."""
        self.monitor.beat()
        self.now += 10.0
        self.assertEqual(self.monitor.state(), LOST)
        self.monitor.beat()
        self.assertEqual(self.monitor.state(), OK)

    def test_bad_timeout_is_rejected(self):
        """Verify nonpositive or nonfinite timeouts raise ValueError."""
        for bad in (0.0, -1.0, float('nan'), float('inf')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                HeartbeatMonitor(bad)


if __name__ == '__main__':
    unittest.main()
