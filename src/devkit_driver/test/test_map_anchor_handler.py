"""Publishing the anchored map -> odom transform from fixes and odom positions."""
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parent))

from support import load  # pylint: disable=wrong-import-position

handler_module = load('modules.map_anchor_handler')

LAT0, LON0 = 51.0, -2.0
PARAMETERS = {'origin_lat': LAT0, 'origin_lon': LON0, 'map_yaw_deg': 0.0, 'fix_topic': 'fc/ekf_fix',
              'odom_topic': 'fc/odom', 'samples': 2, 'max_skew_s': 0.5, 'max_spread_m': 0.5,
              'map_frame': 'map', 'odom_frame': 'odom'}


def _stamp():
    try:
        from builtin_interfaces.msg import Time  # pylint: disable=import-outside-toplevel
        return Time()
    except ImportError:
        return SimpleNamespace(sec=0, nanosec=0)


def _fix(lat=LAT0, lon=LON0, status=0):
    return SimpleNamespace(latitude=lat, longitude=lon, status=SimpleNamespace(status=status))


def _odom(x=0.0, y=0.0):
    return SimpleNamespace(pose=SimpleNamespace(pose=SimpleNamespace(position=SimpleNamespace(x=x, y=y))))


class TestMapAnchorHandler(unittest.TestCase):
    def setUp(self):
        """Create a handler on a mock node with a controllable clock."""
        self.now = 0.0
        self.node = Mock()
        self.parameters = dict(PARAMETERS)
        self.node.get_parameter.side_effect = lambda name: Mock(value=self.parameters[name])
        self.node.get_clock.return_value.now.return_value.to_msg.return_value = _stamp()
        self.broadcaster = Mock()
        self.handler = self._handler()

    def _handler(self):
        return handler_module.MapAnchorHandler(self.node, self.broadcaster, clock=lambda: self.now)

    def _pair(self, x=0.0, **fix):
        self.handler.handle_fix(_fix(**fix))
        self.handler.handle_odom(_odom(x))
        self.now += 1.0

    def test_subscribes_to_the_configured_topics(self):
        """Verify the fix and odom subscriptions use the configured topic names."""
        topics = [call.args[1] for call in self.node.create_subscription.call_args_list]
        self.assertEqual(topics, ['fc/ekf_fix', 'fc/odom'])

    def test_unset_origin_is_rejected(self):
        """Verify a missing origin raises ValueError."""
        self.parameters['origin_lat'] = math.nan
        with self.assertRaises(ValueError):
            self._handler()

    def test_nothing_is_published_before_the_window_is_full(self):
        """Verify one pair of two samples publishes nothing."""
        self._pair()
        self.broadcaster.sendTransform.assert_not_called()
        self.assertFalse(self.handler.anchored)

    def test_transform_is_published_once_the_estimates_agree(self):
        """Verify the transform cancels the odom offset and is broadcast once."""
        self._pair(x=3.0)
        self._pair(x=3.0)
        self.broadcaster.sendTransform.assert_called_once()
        transform = self.broadcaster.sendTransform.call_args.args[0]
        self.assertEqual(transform.header.frame_id, 'map')
        self.assertEqual(transform.child_frame_id, 'odom')
        self.assertAlmostEqual(transform.transform.translation.x, -3.0)
        self.assertAlmostEqual(transform.transform.rotation.w, 1.0)
        self.assertTrue(self.handler.anchored)

    def test_nothing_more_is_published_after_anchoring(self):
        """Verify later messages are ignored once anchored."""
        self._pair()
        self._pair()
        self._pair(x=50.0)
        self.assertEqual(self.broadcaster.sendTransform.call_count, 1)

    def test_no_fix_and_nonfinite_values_are_ignored(self):
        """Verify a no-fix status, a nonfinite fix and a nonfinite odom position are dropped."""
        self.handler.handle_fix(_fix(status=-1))
        self.handler.handle_fix(_fix(lat=math.nan))
        self.handler.handle_odom(_odom(x=math.inf))
        self._pair()
        self._pair()
        self.broadcaster.sendTransform.assert_called_once()

    def test_disagreeing_estimates_warn_and_do_not_publish(self):
        """Verify a window wider than max_spread_m logs a throttled warning and publishes nothing."""
        self._pair(x=0.0)
        self._pair(x=10.0)
        self.broadcaster.sendTransform.assert_not_called()
        self.node.get_logger().warn.assert_called_once()
        self.assertFalse(self.handler.anchored)

    def _warn_if_waiting(self):
        """Fire the wait-warning timer callback registered by the handler."""
        self.node.create_timer.assert_called_once()
        self.node.create_timer.call_args.args[1]()

    def test_warning_names_both_missing_inputs(self):
        """Verify the timer warns naming the fix and odom topics when nothing has arrived."""
        self._warn_if_waiting()
        message = self.node.get_logger().warn.call_args.args[0]
        self.assertIn('fc/ekf_fix', message)
        self.assertIn('fc/odom', message)

    def test_warning_names_only_the_missing_input(self):
        """Verify the timer warns about the fix topic only once odometry is flowing."""
        self.handler.handle_odom(_odom())
        self._warn_if_waiting()
        message = self.node.get_logger().warn.call_args.args[0]
        self.assertIn('fc/ekf_fix', message)
        self.assertNotIn('fc/odom', message)

    def test_no_warning_when_both_inputs_arrive_or_after_anchoring(self):
        """Verify the timer is silent once both inputs have arrived, and after anchoring."""
        self.handler.handle_fix(_fix())
        self.handler.handle_odom(_odom())
        self._warn_if_waiting()
        self.node.get_logger().warn.assert_not_called()
        self.handler.anchored = True
        self._warn_if_waiting()
        self.node.get_logger().warn.assert_not_called()

    def test_ignored_fixes_do_not_count_as_arrived(self):
        """Verify a no-fix message still leaves the fix topic reported as missing."""
        self.handler.handle_fix(_fix(status=-1))
        self._warn_if_waiting()
        self.assertIn('fc/ekf_fix', self.node.get_logger().warn.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
