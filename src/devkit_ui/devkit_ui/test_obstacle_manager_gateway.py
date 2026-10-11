"""Verify ObstacleManager reaches ROS only through the injected RosGateway."""
# pylint: disable=protected-access
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


class FakePolygon:
    """Stand in for geometry_msgs/PolygonStamped."""

    def __init__(self) -> None:
        self.header = SimpleNamespace(stamp=None, frame_id='')
        self.polygon = SimpleNamespace(points=[])


_geometry = ModuleType('geometry_msgs.msg')
_geometry.Point32 = lambda: SimpleNamespace(x=0.0, y=0.0, z=0.0)
_geometry.PolygonStamped = FakePolygon
_qos = ModuleType('rclpy.qos')
_qos.QoSProfile = SimpleNamespace
_qos.DurabilityPolicy = SimpleNamespace(TRANSIENT_LOCAL='transient_local')
_qos.HistoryPolicy = SimpleNamespace(KEEP_LAST='keep_last')
_qos.ReliabilityPolicy = SimpleNamespace(RELIABLE='reliable')

with patch.dict(sys.modules, {
    'geometry_msgs': ModuleType('geometry_msgs'), 'geometry_msgs.msg': _geometry,
    'rclpy': ModuleType('rclpy'), 'rclpy.qos': _qos,
}):
    from devkit_ui.obstacles import ObstacleManager

# Use the globals the class actually runs with: another test may have imported its own copy of
# devkit_ui.obstacles under different stubs, so the package attribute is not reliable.
obstacles = SimpleNamespace(**ObstacleManager.start.__globals__)


class TestObstacleManagerGateway(unittest.TestCase):
    def setUp(self) -> None:
        """Create a manager on a mock gateway with settable odometry and GPS getters."""
        self.ros = Mock()
        self.odom = None
        self.gps = None
        tmp = self.enterContext(tempfile.TemporaryDirectory())  # pylint: disable=consider-using-with
        self.mgr = ObstacleManager(
            self.ros, lambda: self.odom, lambda: self.gps,
            path=str(Path(tmp) / 'obstacles.yaml'))

    def test_start_creates_publisher_and_timer_on_gateway(self) -> None:
        """Verify start() creates the /obstacles publisher and 0.5 s cold-start timer via the gateway."""
        with patch.object(obstacles.threading, 'Thread'):
            self.mgr.start()

        self.ros.create_publisher.assert_called_once_with(
            obstacles.PolygonStamped, '/obstacles', obstacles._OBS_QOS)
        self.ros.create_timer.assert_called_once_with(0.5, self.mgr._cold_start_publish)
        self.assertIs(self.mgr._publisher, self.ros.create_publisher.return_value)
        self.assertEqual(self.mgr.obstacles, ())
        self.assertEqual((self.mgr.version, self.mgr.status), (0, ''))

    def test_publish_failure_is_logged_through_gateway(self) -> None:
        """Verify a publish error is logged through the gateway."""
        with patch.object(obstacles.threading, 'Thread'):
            self.mgr.start()
        self.mgr._publisher.publish.side_effect = RuntimeError('boom')
        self.odom = SimpleNamespace(
            pose=SimpleNamespace(pose=SimpleNamespace(position=SimpleNamespace(x=0.0, y=0.0))))
        self.gps = SimpleNamespace(latitude=51.0, longitude=-1.0)
        self.mgr._obstacles = ({'name': 'A', 'type': 'circle',
                                'center': {'lat': 51.0, 'lon': -1.0}, 'radius_m': 0.5},)

        self.mgr._publish()

        self.ros.get_clock.assert_called()
        self.ros.get_logger.return_value.warn.assert_called()


if __name__ == '__main__':
    unittest.main()
