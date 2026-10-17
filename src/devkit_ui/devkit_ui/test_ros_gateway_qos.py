"""Test that RosGateway converts QosSpec values to rclpy QoS profiles and reads node parameters."""
import enum
import importlib
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

# Other tests import these modules under their own stubs and leave stale copies behind. Import fresh
# copies together so QosSpec in the gateway is the class the qos constants are built from.
with patch.dict(sys.modules, {'rclpy': ModuleType('rclpy'),
                              'rclpy.node': SimpleNamespace(Node=type('Node', (), {}))}):
    sys.modules.pop('devkit_ui.qos', None)
    sys.modules.pop('devkit_ui.ros_gateway', None)
    qos = importlib.import_module('devkit_ui.qos')
    _gateway_module = importlib.import_module('devkit_ui.ros_gateway')
RosGateway = _gateway_module.RosGateway
to_qos_profile = _gateway_module.to_qos_profile


class _Reliability(enum.Enum):
    RELIABLE = 1
    BEST_EFFORT = 2


class _Durability(enum.Enum):
    VOLATILE = 1
    TRANSIENT_LOCAL = 2


class _History(enum.Enum):
    KEEP_LAST = 1
    KEEP_ALL = 2


class _Liveliness(enum.Enum):
    AUTOMATIC = 1
    MANUAL_BY_TOPIC = 3


def _rclpy_qos_stub() -> ModuleType:
    """Build an rclpy.qos stand-in whose QoSProfile records its keyword arguments."""
    module = ModuleType('rclpy.qos')
    module.QoSProfile = SimpleNamespace
    module.Duration = lambda seconds: ('duration', seconds)
    module.ReliabilityPolicy = _Reliability
    module.DurabilityPolicy = _Durability
    module.HistoryPolicy = _History
    module.LivelinessPolicy = _Liveliness
    return module


class TestToQosProfile(unittest.TestCase):
    def setUp(self):
        """Provide the rclpy.qos stand-in for the duration of each test."""
        patcher = patch.dict(sys.modules, {'rclpy.qos': _rclpy_qos_stub()})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_depth_and_existing_profiles_pass_through_unchanged(self):
        """Verify an integer depth or an rclpy profile is handed to rclpy as given."""
        profile = object()
        self.assertEqual(to_qos_profile(10), 10)
        self.assertIs(to_qos_profile(profile), profile)

    def test_sensor_spec_sets_only_depth_and_reliability(self):
        """Verify a spec with no optional fields leaves durability, history and liveliness at rclpy defaults."""
        self.assertEqual(vars(to_qos_profile(qos.SENSOR_QOS)),
                         {'depth': 1, 'reliability': _Reliability.BEST_EFFORT})

    def test_odom_spec_is_reliable_with_depth_ten(self):
        """Verify the odometry spec keeps depth 10 and reliable delivery."""
        self.assertEqual(vars(to_qos_profile(qos.ODOM_QOS)),
                         {'depth': 10, 'reliability': _Reliability.RELIABLE})

    def test_latched_spec_is_reliable_transient_local_keep_last(self):
        """Verify map and obstacle topics stay latched so a late subscriber gets the last message."""
        self.assertEqual(vars(to_qos_profile(qos.LATCHED_QOS)), {
            'depth': 1, 'reliability': _Reliability.RELIABLE,
            'durability': _Durability.TRANSIENT_LOCAL, 'history': _History.KEEP_LAST})

    def test_safety_spec_is_latched_with_automatic_liveliness_and_one_second_lease(self):
        """Verify the safety profile keeps the latched, liveliness-checked settings of the driver."""
        self.assertEqual(vars(to_qos_profile(qos.SAFETY_QOS)), {
            'depth': 1, 'reliability': _Reliability.RELIABLE,
            'durability': _Durability.TRANSIENT_LOCAL,
            'liveliness': _Liveliness.AUTOMATIC,
            'liveliness_lease_duration': ('duration', 1.0)})


class TestGatewayCreation(unittest.TestCase):
    def setUp(self):
        """Wrap a mock node and provide the rclpy.qos stand-in."""
        patcher = patch.dict(sys.modules, {'rclpy.qos': _rclpy_qos_stub()})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.node = Mock()
        self.gateway = RosGateway(self.node)

    def test_publisher_receives_converted_profile(self):
        """Verify a spec given to create_publisher reaches the node as a profile."""
        self.gateway.create_publisher('Bool', 'estop/soft', qos.SAFETY_QOS)
        message_type, topic, profile = self.node.create_publisher.call_args.args
        self.assertEqual((message_type, topic), ('Bool', 'estop/soft'))
        self.assertEqual(profile.liveliness, _Liveliness.AUTOMATIC)

    def test_subscription_receives_converted_profile_and_callback(self):
        """Verify a spec given to create_subscription reaches the node as a profile, with the callback."""
        callback = Mock()
        self.gateway.create_subscription('String', '/current_node', callback, qos.SENSOR_QOS)
        message_type, topic, received, profile = self.node.create_subscription.call_args.args
        self.assertEqual((message_type, topic), ('String', '/current_node'))
        self.assertIs(received, callback)
        self.assertEqual(profile.reliability, _Reliability.BEST_EFFORT)

    def test_integer_depth_reaches_node_unchanged(self):
        """Verify an integer queue depth is not wrapped."""
        self.gateway.create_publisher('Twist', 'cmd_vel', 1)
        self.assertEqual(self.node.create_publisher.call_args.args[2], 1)


if __name__ == '__main__':
    unittest.main()
