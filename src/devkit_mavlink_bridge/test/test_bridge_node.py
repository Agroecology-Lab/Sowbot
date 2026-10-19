"""MavlinkBridgeNode behaviour with ROS and the serial link replaced by stand-ins."""

import os
import sys
import unittest
from types import ModuleType, SimpleNamespace
from typing import ClassVar
from unittest.mock import Mock, patch

from pymavlink import mavutil

MAV = mavutil.mavlink


class FakeLogger:
    """Records log calls by severity."""

    def __init__(self):
        self.records = []

    def _log(self, level, text, **_kwargs):
        self.records.append((level, text))

    def info(self, text, **kwargs):
        self._log('info', text, **kwargs)

    def warn(self, text, **kwargs):
        self._log('warn', text, **kwargs)

    def error(self, text, **kwargs):
        self._log('error', text, **kwargs)

    def levels(self):
        return [level for level, _ in self.records]


class FakeNode:
    """Minimal rclpy.node.Node: parameters with defaults, timers and subscriptions recorded."""

    overrides: ClassVar[dict] = {}

    def __init__(self, _name):
        self._parameters = {}
        self._logger = FakeLogger()
        self.timers = []

    def declare_parameter(self, name, default):
        self._parameters[name] = self.overrides.get(name, default)

    def get_parameter(self, name):
        return SimpleNamespace(value=self._parameters[name])

    def create_subscription(self, *_args):
        return Mock()

    def create_timer(self, period, callback):
        self.timers.append((period, callback))

    def get_logger(self):
        return self._logger


rclpy = ModuleType('rclpy')
rclpy_node = ModuleType('rclpy.node')
rclpy_node.Node = FakeNode
geometry_msgs = ModuleType('geometry_msgs')
geometry_msgs_msg = ModuleType('geometry_msgs.msg')
geometry_msgs_msg.Twist = type('Twist', (), {})
with patch.dict(sys.modules, {'rclpy': rclpy, 'rclpy.node': rclpy_node,
                              'geometry_msgs': geometry_msgs, 'geometry_msgs.msg': geometry_msgs_msg}):
    from devkit_mavlink_bridge import mavlink_bridge_node


def _heartbeat(autopilot=MAV.MAV_AUTOPILOT_ARDUPILOTMEGA, kind=MAV.MAV_TYPE_GROUND_ROVER,
               base_mode=0, custom_mode=0):
    return SimpleNamespace(type=kind, autopilot=autopilot, base_mode=base_mode, custom_mode=custom_mode)


class BridgeNodeTest(unittest.TestCase):
    def setUp(self):
        """Build a node over a mock MAVLink connection and a controllable clock."""
        self.now = 1000.0
        self.connection = Mock()
        self.connection.target_system = 1
        self.connection.target_component = 1
        self.pending = []
        self.connection.recv_match.side_effect = lambda **_: self.pending.pop(0) if self.pending else None
        FakeNode.overrides = {}
        self.enterContext(patch.dict(os.environ, {'MAVLINK_ENDPOINT': '/dev/null'}))
        self.enterContext(patch.object(mavlink_bridge_node.mavutil, 'mavlink_connection',
                                       return_value=self.connection))
        self.clock = self.enterContext(patch.object(
            mavlink_bridge_node.time, 'monotonic', side_effect=lambda: self.now))

    def make(self, **overrides):
        FakeNode.overrides = overrides
        node = mavlink_bridge_node.MavlinkBridgeNode()
        # Both helpers bind time.monotonic as a default argument, so inject the test clock.
        node._watchdog._clock = lambda: self.now
        node._monitor._clock = lambda: self.now
        return node

    def sent(self):
        """Return (vx, yaw_rate) of each SET_POSITION_TARGET message sent."""
        send = self.connection.mav.set_position_target_local_ned_send
        return [(c.kwargs['vx'], c.kwargs['yaw_rate']) for c in send.call_args_list]

    def command(self, node, linear_x, angular_z):
        node._on_cmd_vel(SimpleNamespace(linear=SimpleNamespace(x=linear_x), angular=SimpleNamespace(z=angular_z)))

    def test_commands_are_forwarded_only_while_the_flight_controller_heartbeat_is_fresh(self):
        """Verify motion needs a recent flight controller HEARTBEAT and stops when it goes."""
        node = self.make()
        self.command(node, 0.5, 0.2)
        node._republish()
        self.assertEqual(self.sent(), [(0.0, 0.0)])  # no heartbeat seen yet

        self.pending.append(_heartbeat())
        node._republish()
        self.assertEqual(self.sent()[-1], (0.5, 0.2))

        self.now += 3.5
        self.command(node, 0.5, 0.2)
        node._republish()
        self.assertEqual(self.sent()[-1], (0.0, 0.0))  # heartbeat overdue
        self.assertIn('warn', node.get_logger().levels())

    def test_heartbeat_requirement_can_be_disabled(self):
        """Verify require_fc_heartbeat=False forwards commands without a heartbeat."""
        node = self.make(require_fc_heartbeat=False)
        self.command(node, 0.5, 0.2)
        node._republish()
        self.assertEqual(self.sent(), [(0.5, 0.2)])

    def test_other_ground_stations_and_our_own_echo_do_not_count(self):
        """Verify only a real autopilot's heartbeat marks the link ok."""
        node = self.make()
        self.pending += [_heartbeat(kind=MAV.MAV_TYPE_GCS), _heartbeat(autopilot=MAV.MAV_AUTOPILOT_INVALID)]
        self.command(node, 0.5, 0.0)
        node._republish()
        self.assertEqual(self.sent(), [(0.0, 0.0)])

    def test_velocity_is_clamped_to_the_limits(self):
        """Verify an excessive command is limited before it reaches the flight controller."""
        node = self.make(require_fc_heartbeat=False, max_linear_x_mps=1.0, max_angular_z_radps=0.5)
        self.command(node, 9.0, -9.0)
        node._republish()
        self.assertEqual(self.sent(), [(1.0, -0.5)])

    def test_stale_command_is_zeroed(self):
        """Verify the command watchdog still forces zero."""
        node = self.make(require_fc_heartbeat=False)
        self.command(node, 0.5, 0.0)
        self.now += 0.6
        node._republish()
        self.assertEqual(self.sent(), [(0.0, 0.0)])

    def test_flight_controller_ids_are_used_as_targets(self):
        """Verify messages address the system and component learned from the link."""
        node = self.make(require_fc_heartbeat=False)
        node._republish()
        kwargs = self.connection.mav.set_position_target_local_ned_send.call_args.kwargs
        self.assertEqual((kwargs['target_system'], kwargs['target_component']), (1, 1))

    def test_send_errors_are_logged_and_do_not_escape(self):
        """Verify a serial error does not kill the timer callback."""
        node = self.make(require_fc_heartbeat=False)
        self.connection.mav.set_position_target_local_ned_send.side_effect = OSError('port gone')
        node._republish()
        self.connection.mav.heartbeat_send.side_effect = OSError('port gone')
        node._send_heartbeat()
        self.assertEqual(node.get_logger().levels().count('error'), 2)

    def test_non_oserror_failures_still_propagate(self):
        """Verify programming errors are not swallowed."""
        node = self.make(require_fc_heartbeat=False)
        self.connection.mav.set_position_target_local_ned_send.side_effect = TypeError('bad field')
        with self.assertRaises(TypeError):
            node._republish()

    def test_gcs_heartbeat_is_sent(self):
        """Verify the bridge announces itself as a ground station."""
        node = self.make()
        node._send_heartbeat()
        self.connection.mav.heartbeat_send.assert_called_once_with(
            MAV.MAV_TYPE_GCS, MAV.MAV_AUTOPILOT_INVALID, 0, 0, MAV.MAV_STATE_ACTIVE)
        self.assertIn(node._send_heartbeat, [callback for _, callback in node.timers])

    def test_arming_and_mode_changes_are_logged_once(self):
        """Verify the flight controller state is logged on change only."""
        node = self.make()
        armed = _heartbeat(base_mode=MAV.MAV_MODE_FLAG_SAFETY_ARMED, custom_mode=15)
        self.pending += [armed, armed, armed]
        node._republish()
        info = [text for level, text in node.get_logger().records if level == 'info' and 'armed' in text]
        self.assertEqual(len(info), 1)
        self.assertIn('custom_mode 15', info[0])

    def test_read_errors_are_logged_and_do_not_escape(self):
        """Verify a failing read leaves the node running and commanding zero."""
        node = self.make()
        self.connection.recv_match.side_effect = OSError('read failed')
        self.command(node, 0.5, 0.0)
        node._republish()
        self.assertEqual(self.sent(), [(0.0, 0.0)])
        self.assertIn('error', node.get_logger().levels())

    def test_invalid_parameters_are_rejected(self):
        """Verify bad periods and limits stop the node from starting."""
        for overrides in ({'heartbeat_period_s': 0.0}, {'max_linear_x_mps': 0.0},
                          {'max_angular_z_radps': float('nan')}, {'fc_heartbeat_timeout_s': -1.0},
                          {'republish_period_s': 0.0}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.make(**overrides)

    def test_placeholder_endpoint_is_rejected(self):
        """Verify the placeholder connection string is not accepted."""
        with patch.dict(os.environ, {'MAVLINK_ENDPOINT': 'TODO_SERIAL_DEVICE_HERE'}), \
                self.assertRaises(ValueError):
            self.make()


if __name__ == '__main__':
    unittest.main()
