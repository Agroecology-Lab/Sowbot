"""MavlinkBridgeNode behaviour with ROS and the serial link replaced by stand-ins."""

import importlib
import math
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
        """Initialize an empty log record list."""
        self.records = []

    def _log(self, level, text, **_kwargs):
        """Record a message and its severity, ignoring ROS logging options."""
        self.records.append((level, text))

    def info(self, text, **kwargs):
        """Record an informational message."""
        self._log('info', text, **kwargs)

    def warn(self, text, **kwargs):
        """Record a warning message."""
        self._log('warn', text, **kwargs)

    def error(self, text, **kwargs):
        """Record an error message."""
        self._log('error', text, **kwargs)

    def levels(self):
        """Return recorded severities in emission order."""
        return [level for level, _ in self.records]


class FakeNode:
    """Minimal rclpy.node.Node: parameters with defaults, timers and subscriptions recorded."""

    overrides: ClassVar[dict] = {}

    def __init__(self, _name):
        """Initialize isolated parameter storage, a logger, and recorded timers."""
        self._parameters = {}
        self._logger = FakeLogger()
        self.timers = []
        self.publishers = {}

    def declare_parameter(self, name, default):
        """Store a parameter's test override or its declared default."""
        self._parameters[name] = self.overrides.get(name, default)

    def get_parameter(self, name):
        """Wrap a stored parameter value in the interface expected by the node."""
        return SimpleNamespace(value=self._parameters[name])

    def create_subscription(self, *_args):
        """Return a passive subscription double without connecting to ROS."""
        return Mock()

    def create_publisher(self, _message_type, topic, *_args):
        """Return a publisher double recorded under its topic name."""
        self.publishers[topic] = Mock()
        return self.publishers[topic]

    def get_clock(self):
        """Return a clock double whose time converts to a fixed stamp."""
        return SimpleNamespace(now=lambda: SimpleNamespace(to_msg=lambda: 'stamp'))

    def create_timer(self, period, callback):
        """Record a timer for inspection without scheduling its callback."""
        self.timers.append((period, callback))

    def get_logger(self):
        """Return this node double's recording logger."""
        return self._logger


class FakeMsg:
    """Stand-in for a ROS message: keyword fields, and nested fields appear on first use."""

    def __init__(self, **fields):
        """Store any keyword arguments as attributes."""
        self.__dict__.update(fields)

    def __getattr__(self, name):
        """Create a nested message for an unset field, as ROS messages have nested defaults."""
        if name.startswith('__'):
            raise AttributeError(name)
        child = FakeMsg()
        setattr(self, name, child)
        return child


class FakeDiagnosticStatus(FakeMsg):
    """DiagnosticStatus with the level constants, which ROS 2 defines as single bytes."""

    OK, WARN, ERROR, STALE = b'\x00', b'\x01', b'\x02', b'\x03'


def _fake_package(name, **members):
    """Build a fake ROS package and its .msg submodule holding the given message classes."""
    package, messages = ModuleType(name), ModuleType(f'{name}.msg')
    messages.__dict__.update(members)
    return {name: package, f'{name}.msg': messages}


rclpy = ModuleType('rclpy')
rclpy_node = ModuleType('rclpy.node')
rclpy_node.Node = FakeNode
with patch.dict(sys.modules, {
        'rclpy': rclpy, 'rclpy.node': rclpy_node,
        **_fake_package('geometry_msgs', Twist=type('Twist', (), {})),
        **_fake_package('nav_msgs', Odometry=FakeMsg),
        **_fake_package('sensor_msgs', NavSatFix=FakeMsg),
        **_fake_package('diagnostic_msgs', DiagnosticArray=FakeMsg, KeyValue=FakeMsg,
                        DiagnosticStatus=FakeDiagnosticStatus)}):
    from devkit_mavlink_bridge import mavlink_bridge_node


def _message(kind, source_system=1, **fields):
    """Build a MAVLink message double of the given type from the given system."""
    return SimpleNamespace(get_type=lambda: kind, get_srcSystem=lambda: source_system, **fields)


def _heartbeat(autopilot=MAV.MAV_AUTOPILOT_ARDUPILOTMEGA, kind=MAV.MAV_TYPE_GROUND_ROVER,
               base_mode=0, custom_mode=0, source_system=1):
    """Build a heartbeat double with configurable vehicle, autopilot, and mode fields."""
    return _message('HEARTBEAT', source_system, type=kind, autopilot=autopilot,
                    base_mode=base_mode, custom_mode=custom_mode)


GOOD_EKF_FLAGS = MAV.EKF_ATTITUDE | MAV.EKF_VELOCITY_HORIZ | MAV.EKF_POS_HORIZ_REL | MAV.EKF_POS_HORIZ_ABS


def _ekf_status(flags=GOOD_EKF_FLAGS, source_system=1):
    """Build an EKF_STATUS_REPORT double."""
    return _message('EKF_STATUS_REPORT', source_system, flags=flags, velocity_variance=0.1,
                    pos_horiz_variance=0.2, pos_vert_variance=0.3, compass_variance=0.4,
                    terrain_alt_variance=0.5, airspeed_variance=0.0)


def _attitude(w=1.0, x=0.0, y=0.0, z=0.0, rates=(0.0, 0.0, 0.0), source_system=1):
    """Build an ATTITUDE_QUATERNION double (body FRD to NED)."""
    return _message('ATTITUDE_QUATERNION', source_system, q1=w, q2=x, q3=y, q4=z,
                    rollspeed=rates[0], pitchspeed=rates[1], yawspeed=rates[2])


def _local_position(north=1.0, east=2.0, down=-3.0, vn=0.5, ve=0.0, vd=0.0, source_system=1):
    """Build a LOCAL_POSITION_NED double."""
    return _message('LOCAL_POSITION_NED', source_system, x=north, y=east, z=down, vx=vn, vy=ve, vz=vd)


def _gps(kind='GPS_RAW_INT', fix_type=6, lat=513000000, lon=-25000000, source_system=1):
    """Build a GPS_RAW_INT or GPS2_RAW double with 20 mm horizontal and 50 mm vertical accuracy."""
    return _message(kind, source_system, fix_type=fix_type, lat=lat, lon=lon, alt_ellipsoid=12500,
                    eph=80, epv=120, satellites_visible=20, h_acc=20, v_acc=50, yaw=9000)


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
        """Create a bridge with parameter overrides and inject the controllable test clock."""
        FakeNode.overrides = overrides
        node = mavlink_bridge_node.MavlinkBridgeNode()
        # Both helpers bind time.monotonic as a default argument, so inject the test clock.
        node._watchdog._clock = lambda: self.now
        node._monitor._clock = lambda: self.now
        node._latest._clock = lambda: self.now
        return node

    def published(self, node, topic):
        """Return the messages published on a topic."""
        return [call.args[0] for call in node.publishers[topic].publish.call_args_list]

    def feed(self, node, *messages):
        """Deliver messages as if they had arrived on the link, then poll once."""
        self.pending += list(messages)
        node._poll_link()

    def sent(self):
        """Return (vx, yaw_rate) of each SET_POSITION_TARGET message sent."""
        send = self.connection.mav.set_position_target_local_ned_send
        return [(c.kwargs['vx'], c.kwargs['yaw_rate']) for c in send.call_args_list]

    def command(self, node, linear_x, angular_z):
        """Deliver linear velocity in m/s and yaw rate in rad/s to the bridge callback."""
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

    def test_other_systems_do_not_enable_commands(self):
        """Verify another autopilot cannot satisfy the selected controller's heartbeat gate."""
        self.connection.target_system = 7
        node = self.make()
        self.pending.append(_heartbeat(source_system=42))
        self.command(node, 0.5, 0.2)
        node._republish()
        self.assertEqual(self.sent()[-1], (0.0, 0.0))

        self.pending.append(_heartbeat(source_system=7))
        node._republish()
        self.assertEqual(self.sent()[-1], (0.5, 0.2))

    def test_other_systems_do_not_keep_the_selected_controller_alive(self):
        """Verify other heartbeats cannot extend or restore the selected controller's link."""
        node = self.make()
        self.pending.append(_heartbeat())
        node._republish()
        for elapsed in (2.0, 1.5, 1.0):
            with self.subTest(elapsed=elapsed):
                self.now += elapsed
                self.pending.append(_heartbeat(source_system=42))
                self.command(node, 0.5, 0.2)
                node._republish()
                expected = (0.5, 0.2) if elapsed == 2.0 else (0.0, 0.0)
                self.assertEqual(self.sent()[-1], expected)

    def test_unset_target_accepts_only_eligible_discovery_heartbeats(self):
        """Verify discovery accepts an autopilot while retaining the GCS and echo filters."""
        for target_system in (0, None):
            with self.subTest(target_system=target_system):
                self.connection.target_system = target_system
                node = self.make()
                self.pending += [_heartbeat(kind=MAV.MAV_TYPE_GCS),
                                 _heartbeat(autopilot=MAV.MAV_AUTOPILOT_INVALID)]
                self.command(node, 0.5, 0.2)
                node._republish()
                self.assertEqual(self.sent()[-1], (0.0, 0.0))
                self.pending.append(_heartbeat(source_system=42))
                node._republish()
                self.assertEqual(self.sent()[-1], (0.5, 0.2))

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


class InboundTest(BridgeNodeTest):
    """EKF3 state, GPS status and message-rate requests flowing from the flight controller."""

    def level(self, node, name):
        """Publish diagnostics and return (level, message, values) of the named status."""
        node._publish_diagnostics()
        array = self.published(node, 'fc/diagnostics')[-1]
        status = next(s for s in array.status if s.name == f'devkit_mavlink_bridge: {name}')
        return status.level, status.message, {v.key: v.value for v in status.values}

    def good_state(self, node):
        """Bring the link up and deliver a valid EKF status and a level attitude facing north."""
        self.feed(node, _heartbeat(), _ekf_status(), _attitude())

    def test_publishers_and_timers_are_registered(self):
        """Verify the documented topics exist and the poll and diagnostics timers are running."""
        node = self.make()
        self.assertEqual(set(node.publishers),
                         {'fc/odom', 'fc/ekf_fix', 'fc/gps1/fix', 'fc/gps2/fix', 'fc/diagnostics'})
        callbacks = [callback for _, callback in node.timers]
        for expected in (node._poll_link, node._publish_diagnostics, node._rerequest_message_intervals):
            self.assertIn(expected, callbacks)

    def test_the_gps_input_stub_is_gone(self):
        """Verify nothing can feed the flight controller a position: the stub module is deleted."""
        with self.assertRaises(ImportError):
            importlib.import_module('devkit_mavlink_bridge.modules.gps_input_handler')
        node = self.make(require_fc_heartbeat=False)
        self.good_state(node)
        self.feed(node, _local_position())
        node._republish()
        self.connection.mav.gps_input_send.assert_not_called()

    def test_heartbeat_is_found_among_other_traffic(self):
        """Verify reading every message does not lose the heartbeat that gates motion."""
        node = self.make()
        self.pending += [_attitude(), _ekf_status(), _heartbeat()]
        self.command(node, 0.5, 0.2)
        node._republish()
        self.assertEqual(self.sent(), [(0.5, 0.2)])

    def test_odometry_is_published_in_ros_frames(self):
        """Verify position, orientation and body-frame twist are converted from NED and FRD."""
        node = self.make()
        self.good_state(node)
        self.feed(node, _local_position(north=1.0, east=2.0, down=-3.0, vn=0.5, ve=0.0, vd=0.0))
        (odometry,) = self.published(node, 'fc/odom')
        position = odometry.pose.pose.position
        self.assertEqual((position.x, position.y, position.z), (2.0, 1.0, 3.0))
        self.assertAlmostEqual(odometry.pose.pose.orientation.z, math.sqrt(0.5))  # north is +90 deg in ENU
        self.assertAlmostEqual(odometry.pose.pose.orientation.w, math.sqrt(0.5))
        self.assertAlmostEqual(odometry.twist.twist.linear.x, 0.5)  # driving forward
        self.assertAlmostEqual(odometry.twist.twist.linear.y, 0.0)
        self.assertEqual((odometry.header.frame_id, odometry.child_frame_id), ('odom', 'base_link'))
        self.assertEqual(odometry.header.stamp, 'stamp')

    def test_odometry_covariance_is_marked_unknown(self):
        """Verify no covariance is invented: the flight controller sends none."""
        node = self.make()
        self.good_state(node)
        self.feed(node, _local_position())
        (odometry,) = self.published(node, 'fc/odom')
        for covariance in (odometry.pose.covariance, odometry.twist.covariance):
            self.assertEqual(covariance[0], -1.0)
            self.assertEqual(len(covariance), 36)
            self.assertEqual(sum(abs(c) for c in covariance[1:]), 0.0)

    def test_frame_ids_are_parameters(self):
        """Verify odom and base frame names can be changed."""
        node = self.make(odom_frame_id='map', base_frame_id='base_footprint')
        self.good_state(node)
        self.feed(node, _local_position())
        (odometry,) = self.published(node, 'fc/odom')
        self.assertEqual((odometry.header.frame_id, odometry.child_frame_id), ('map', 'base_footprint'))

    def test_odometry_is_withheld_when_its_inputs_are_missing_stale_or_invalid(self):
        """Verify no odometry is published without a valid EKF status and a fresh attitude."""
        def no_status(node):
            self.feed(node, _heartbeat(), _attitude())

        def const_position_mode(node):
            self.feed(node, _heartbeat(), _attitude(), _ekf_status(GOOD_EKF_FLAGS | MAV.EKF_CONST_POS_MODE))

        def stale_attitude(node):
            self.good_state(node)
            self.now += 0.4

        def stale_status(node):
            self.feed(node, _heartbeat(), _ekf_status())
            self.now += 2.1
            self.feed(node, _attitude())

        def nonfinite_position(node):
            self.good_state(node)
            return _local_position(north=float('nan'))

        for name, setup in (('no status', no_status), ('const position mode', const_position_mode),
                            ('stale attitude', stale_attitude), ('stale status', stale_status),
                            ('nonfinite position', nonfinite_position)):
            with self.subTest(name):
                node = self.make()
                position = setup(node) or _local_position()
                self.feed(node, position)
                self.assertEqual(self.published(node, 'fc/odom'), [])
                self.assertIn('warn', node.get_logger().levels())

    def test_withheld_odometry_is_explained_once_and_recovery_is_logged(self):
        """Verify the reason is logged on change, not on every message."""
        node = self.make()
        self.feed(node, _heartbeat(), _local_position(), _local_position())
        reasons = [t for lvl, t in node.get_logger().records if t.startswith('not publishing fc/odom')]
        self.assertEqual(len(reasons), 1)
        self.assertIn('EKF_STATUS_REPORT', reasons[0])
        self.feed(node, _ekf_status(), _attitude(), _local_position())
        self.assertEqual(len(self.published(node, 'fc/odom')), 1)
        self.assertIn('EKF3 state valid: publishing fc/odom', [t for _, t in node.get_logger().records])

    def test_const_position_mode_is_named_in_the_reason(self):
        """Verify the log says which EKF flags were set."""
        node = self.make()
        self.feed(node, _heartbeat(), _attitude(), _ekf_status(GOOD_EKF_FLAGS | MAV.EKF_CONST_POS_MODE),
                  _local_position())
        reasons = [t for _, t in node.get_logger().records if t.startswith('not publishing fc/odom')]
        self.assertIn('const_pos_mode', reasons[-1])

    def test_state_messages_are_ignored_without_a_live_link_or_from_other_systems(self):
        """Verify only the selected flight controller's state is used, and only while it is alive."""
        node = self.make()
        self.feed(node, _ekf_status(), _attitude(), _local_position())  # no heartbeat yet
        self.assertEqual(self.published(node, 'fc/odom'), [])
        self.feed(node, _heartbeat(source_system=1))
        self.feed(node, _ekf_status(source_system=42), _attitude(source_system=42),
                  _local_position(source_system=42))
        self.assertEqual(self.published(node, 'fc/odom'), [])

    def test_losing_the_link_discards_stored_state(self):
        """Verify state from before a link loss cannot be combined with state after it."""
        node = self.make()
        self.good_state(node)
        self.now += 3.5
        node._poll_link()  # link lost, store cleared
        self.feed(node, _heartbeat(), _local_position())
        self.assertEqual(self.published(node, 'fc/odom'), [])

    def test_receiver_fixes_are_published_per_gps(self):
        """Verify GPS1 and GPS2 reports become NavSatFix on their own topics."""
        node = self.make()
        self.feed(node, _heartbeat(), _gps('GPS_RAW_INT', fix_type=6), _gps('GPS2_RAW', fix_type=1))
        (first,) = self.published(node, 'fc/gps1/fix')
        (second,) = self.published(node, 'fc/gps2/fix')
        self.assertEqual(first.status.status, 2)  # RTK fixed: GBAS
        self.assertEqual((first.latitude, first.longitude, first.altitude), (51.3, -2.5, 12.5))
        self.assertAlmostEqual(first.position_covariance[0], 0.0004)
        self.assertEqual(first.header.frame_id, 'gps_link')
        self.assertEqual(second.status.status, -1)
        self.assertTrue(math.isnan(second.latitude))

    def test_ekf_fix_needs_an_absolute_position(self):
        """Verify the EKF3 geodetic position is only marked as a fix when EKF3 says it is absolute."""
        relative_only = MAV.EKF_ATTITUDE | MAV.EKF_VELOCITY_HORIZ | MAV.EKF_POS_HORIZ_REL
        node = self.make()
        global_position = _message('GLOBAL_POSITION_INT', lat=513000000, lon=-25000000)
        self.feed(node, _heartbeat(), global_position)  # no EKF status yet
        self.feed(node, _ekf_status(relative_only), global_position)
        self.feed(node, _ekf_status(GOOD_EKF_FLAGS), global_position)
        no_status, relative, absolute = self.published(node, 'fc/ekf_fix')
        self.assertEqual((no_status.status.status, relative.status.status, absolute.status.status), (-1, -1, 0))
        self.assertTrue(math.isnan(relative.latitude))
        self.assertEqual((absolute.latitude, absolute.longitude), (51.3, -2.5))
        self.assertTrue(math.isnan(absolute.altitude))  # the message carries MSL, not ellipsoidal, height
        self.assertEqual(absolute.position_covariance_type, 0)

    def test_diagnostics_report_link_ekf_and_gps(self):
        """Verify levels and values on fc/diagnostics, with a missing GPS2 marked stale."""
        node = self.make()
        self.feed(node, _heartbeat(base_mode=MAV.MAV_MODE_FLAG_SAFETY_ARMED, custom_mode=4),
                  _ekf_status(), _gps('GPS_RAW_INT'))
        ok, stale = FakeDiagnosticStatus.OK, FakeDiagnosticStatus.STALE
        link = self.level(node, 'flight_controller')
        self.assertEqual((link[0], link[2]['armed'], link[2]['custom_mode']), (ok, 'True', '4'))
        ekf = self.level(node, 'ekf3')
        self.assertEqual(ekf[0], ok)
        self.assertEqual(ekf[2]['pos_horiz_variance'], '0.2')
        self.assertIn('pos_horiz_abs', ekf[2]['flags'])
        gps1 = self.level(node, 'gps1')
        self.assertEqual((gps1[0], gps1[1], gps1[2]['hdop']), (ok, 'RTK_FIXED', '0.8'))
        self.assertEqual(self.level(node, 'gps2')[0], stale)

    def test_diagnostics_go_bad_when_data_stops_or_the_pose_is_invalid(self):
        """Verify an invalid pose is an error and old data is stale, not silently still ok."""
        node = self.make()
        self.feed(node, _heartbeat(), _ekf_status(MAV.EKF_ATTITUDE), _gps('GPS_RAW_INT'))
        self.assertEqual(self.level(node, 'ekf3')[0], FakeDiagnosticStatus.ERROR)
        self.now += 2.5
        self.assertEqual(self.level(node, 'ekf3')[0], FakeDiagnosticStatus.STALE)
        self.now += 1.0
        self.assertEqual(self.level(node, 'gps1')[0], FakeDiagnosticStatus.STALE)

    def test_diagnostics_before_any_heartbeat_are_stale(self):
        """Verify a link that has never been seen is not reported as healthy."""
        node = self.make()
        self.assertEqual(self.level(node, 'flight_controller')[0], FakeDiagnosticStatus.STALE)
        self.feed(node, _heartbeat())
        self.now += 3.5
        node._poll_link()
        self.assertEqual(self.level(node, 'flight_controller')[0], FakeDiagnosticStatus.ERROR)

    def test_message_rates_are_requested_when_the_link_comes_up(self):
        """Verify one interval request per message, at the configured rates, and none before a heartbeat."""
        node = self.make()
        send = self.connection.mav.command_long_send
        self.feed(node, _attitude())
        send.assert_not_called()
        self.feed(node, _heartbeat(), _heartbeat())
        requests = {c.args[4]: c.args[5] for c in send.call_args_list}
        self.assertEqual(requests, {
            MAV.MAVLINK_MSG_ID_ATTITUDE_QUATERNION: 100_000, MAV.MAVLINK_MSG_ID_LOCAL_POSITION_NED: 100_000,
            MAV.MAVLINK_MSG_ID_GLOBAL_POSITION_INT: 100_000, MAV.MAVLINK_MSG_ID_GPS_RAW_INT: 500_000,
            MAV.MAVLINK_MSG_ID_GPS2_RAW: 500_000, MAV.MAVLINK_MSG_ID_EKF_STATUS_REPORT: 500_000})
        self.assertEqual(send.call_count, 6)  # a second heartbeat does not repeat the request
        first = send.call_args_list[0].args
        self.assertEqual(first[:4], (1, 1, MAV.MAV_CMD_SET_MESSAGE_INTERVAL, 0))
        self.assertEqual(first[6:], (0, 0, 0, 0, 0))

    def test_message_rates_follow_the_parameters(self):
        """Verify the configured rates set the interval in microseconds."""
        node = self.make(state_rate_hz=20.0, gps_rate_hz=1.0, ekf_status_rate_hz=4.0)
        self.feed(node, _heartbeat())
        requests = {c.args[4]: c.args[5] for c in self.connection.mav.command_long_send.call_args_list}
        self.assertEqual(requests[MAV.MAVLINK_MSG_ID_LOCAL_POSITION_NED], 50_000)
        self.assertEqual(requests[MAV.MAVLINK_MSG_ID_GPS_RAW_INT], 1_000_000)
        self.assertEqual(requests[MAV.MAVLINK_MSG_ID_EKF_STATUS_REPORT], 250_000)

    def test_message_rates_are_requested_again_after_a_link_loss_and_periodically(self):
        """Verify a rebooted flight controller is asked again, and not while the link is down."""
        node = self.make()
        send = self.connection.mav.command_long_send
        node._rerequest_message_intervals()
        send.assert_not_called()  # no link yet
        self.feed(node, _heartbeat())
        node._rerequest_message_intervals()
        self.assertEqual(send.call_count, 12)
        self.now += 3.5
        node._poll_link()
        node._rerequest_message_intervals()
        self.assertEqual(send.call_count, 12)  # link lost
        self.feed(node, _heartbeat())
        self.assertEqual(send.call_count, 18)

    def test_interval_request_send_errors_are_logged(self):
        """Verify a serial error while requesting rates does not escape the poll."""
        node = self.make()
        self.connection.mav.command_long_send.side_effect = OSError('port gone')
        self.feed(node, _heartbeat())
        self.assertIn('error', node.get_logger().levels())

    def test_refused_interval_request_is_warned(self):
        """Verify a rejected request is visible, and an accepted one is quiet."""
        node = self.make()
        ack = MAV.MAV_CMD_SET_MESSAGE_INTERVAL
        self.feed(node, _heartbeat(), _message('COMMAND_ACK', command=ack, result=MAV.MAV_RESULT_ACCEPTED))
        self.assertNotIn('warn', node.get_logger().levels())
        self.feed(node, _message('COMMAND_ACK', command=ack, result=MAV.MAV_RESULT_DENIED))
        self.feed(node, _message('COMMAND_ACK', command=999, result=MAV.MAV_RESULT_DENIED))
        self.assertEqual(node.get_logger().levels().count('warn'), 1)

    def test_one_poll_handles_a_bounded_number_of_messages(self):
        """Verify a flood of messages cannot hold the executor for ever."""
        node = self.make()
        self.pending += [_attitude() for _ in range(300)]
        node._poll_link()
        self.assertEqual(len(self.pending), 100)

    def test_unknown_message_types_are_ignored(self):
        """Verify traffic the bridge does not use, including corrupt frames, is skipped."""
        node = self.make()
        self.feed(node, _heartbeat(), _message('BAD_DATA'), _message('STATUSTEXT'))
        self.assertEqual(self.published(node, 'fc/odom'), [])

    def test_inbound_parameters_must_be_positive_and_finite(self):
        """Verify a bad rate, period or timeout stops the node from starting."""
        for name in ('poll_period_s', 'state_rate_hz', 'gps_rate_hz', 'ekf_status_rate_hz',
                     'attitude_timeout_s', 'ekf_status_timeout_s', 'gps_timeout_s', 'diagnostics_period_s'):
            for bad in (0.0, -1.0, float('nan'), float('inf')):
                with self.subTest(name=name, bad=bad), self.assertRaises(ValueError):
                    self.make(**{name: bad})

    def test_odometry_timeouts_are_inclusive_and_refresh_recovers_publication(self):
        """Verify each configured timeout gates odometry independently at its boundary."""
        for parameter, refresh in (('attitude_timeout_s', _attitude),
                                   ('ekf_status_timeout_s', _ekf_status)):
            with self.subTest(parameter=parameter):
                overrides = {'attitude_timeout_s': 5.0, 'ekf_status_timeout_s': 5.0}
                overrides[parameter] = 0.5
                node = self.make(**overrides)
                self.good_state(node)
                self.now += 0.5
                self.feed(node, _local_position())
                self.assertEqual(len(self.published(node, 'fc/odom')), 1)
                self.now += 0.001
                self.feed(node, _local_position())
                self.assertEqual(len(self.published(node, 'fc/odom')), 1)
                self.feed(node, refresh(), _local_position())
                self.assertEqual(len(self.published(node, 'fc/odom')), 2)

    def test_missing_and_invalid_attitude_withhold_odometry_until_replaced(self):
        """Verify absent, zero and nonfinite attitudes cannot produce or reuse a pose."""
        node = self.make()
        self.feed(node, _heartbeat(), _ekf_status(), _local_position())
        self.assertEqual(self.published(node, 'fc/odom'), [])
        self.feed(node, _attitude(), _local_position())
        self.assertEqual(len(self.published(node, 'fc/odom')), 1)
        for invalid in (_attitude(w=0.0), _attitude(y=math.nan),
                        _attitude(rates=(0.0, math.inf, 0.0))):
            with self.subTest(invalid=invalid):
                self.feed(node, invalid, _local_position())
                self.assertEqual(len(self.published(node, 'fc/odom')), 1)
        self.feed(node, _attitude(), _local_position())
        self.assertEqual(len(self.published(node, 'fc/odom')), 2)

    def test_latest_attitude_and_body_rates_are_used(self):
        """Verify a newer east-facing attitude replaces the earlier north-facing sample."""
        node = self.make()
        self.good_state(node)
        half = math.sqrt(0.5)
        self.feed(node, _attitude(w=half, z=half, rates=(0.1, 0.2, 0.3)),
                  _local_position(vn=0.0, ve=2.0))
        (odometry,) = self.published(node, 'fc/odom')
        orientation = odometry.pose.pose.orientation
        self.assertAlmostEqual(orientation.z, 0.0)
        self.assertAlmostEqual(orientation.w, 1.0)
        linear = odometry.twist.twist.linear
        angular = odometry.twist.twist.angular
        self.assertAlmostEqual(linear.x, 2.0)
        self.assertAlmostEqual(linear.y, 0.0)
        self.assertEqual((angular.x, angular.y, angular.z), (0.1, -0.2, -0.3))

    def test_disabling_outbound_heartbeat_gate_does_not_enable_unlinked_telemetry(self):
        """Verify inbound state still needs a live link when outbound commands do not."""
        node = self.make(require_fc_heartbeat=False)
        global_position = _message('GLOBAL_POSITION_INT', lat=513000000, lon=-25000000)
        self.feed(node, _ekf_status(), _attitude(), _local_position(), global_position,
                  _gps(), _gps('GPS2_RAW'))
        for topic in ('fc/odom', 'fc/ekf_fix', 'fc/gps1/fix', 'fc/gps2/fix'):
            self.assertEqual(self.published(node, topic), [])
        self.feed(node, _heartbeat(), _local_position())
        self.assertEqual(self.published(node, 'fc/odom'), [])
        self.feed(node, _ekf_status(), _attitude(), _local_position(), global_position,
                  _gps(), _gps('GPS2_RAW'))
        for topic in ('fc/odom', 'fc/ekf_fix', 'fc/gps1/fix', 'fc/gps2/fix'):
            self.assertEqual(len(self.published(node, topic)), 1)

    def test_foreign_telemetry_cannot_overwrite_selected_controller_state(self):
        """Verify foreign state is neither published nor mixed into the selected pose."""
        node = self.make()
        self.good_state(node)
        self.feed(node, _gps(), _gps('GPS2_RAW'))
        self.feed(node, _ekf_status(flags=0, source_system=42),
                  _attitude(w=math.nan, source_system=42), _local_position(source_system=42),
                  _message('GLOBAL_POSITION_INT', source_system=42, lat=0, lon=0),
                  _gps(source_system=42), _gps('GPS2_RAW', source_system=42))
        self.assertEqual(self.published(node, 'fc/odom'), [])
        self.assertEqual(self.published(node, 'fc/ekf_fix'), [])
        for topic in ('fc/gps1/fix', 'fc/gps2/fix'):
            self.assertEqual(len(self.published(node, topic)), 1)
        self.feed(node, _local_position(),
                  _message('GLOBAL_POSITION_INT', lat=513000000, lon=-25000000))
        self.assertEqual(len(self.published(node, 'fc/odom')), 1)
        self.assertEqual(self.published(node, 'fc/ekf_fix')[-1].status.status, 0)

    def test_foreign_reports_do_not_refresh_expired_state(self):
        """Verify another system cannot extend the arrival time of either pose input."""
        for parameter, foreign in (('attitude_timeout_s', _attitude),
                                   ('ekf_status_timeout_s', _ekf_status)):
            with self.subTest(parameter=parameter):
                overrides = {'attitude_timeout_s': 5.0, 'ekf_status_timeout_s': 5.0}
                overrides[parameter] = 0.5
                node = self.make(**overrides)
                self.good_state(node)
                self.now += 0.501
                self.feed(node, foreign(source_system=42), _local_position())
                self.assertEqual(self.published(node, 'fc/odom'), [])
                self.feed(node, foreign(), _local_position())
                self.assertEqual(len(self.published(node, 'fc/odom')), 1)

    def test_receiver_reports_without_extensions_have_unknown_altitude_and_accuracy(self):
        """Verify MAVLink 1 GPS reports never substitute MSL height for ellipsoid height."""
        node = self.make(gps_frame_id='antenna')
        self.feed(node, _heartbeat())
        for kind, topic in (('GPS_RAW_INT', 'fc/gps1/fix'), ('GPS2_RAW', 'fc/gps2/fix')):
            with self.subTest(kind=kind):
                self.feed(node, _message(kind, fix_type=3, lat=513000000, lon=-25000000,
                                         alt=100000, satellites_visible=12, eph=100, epv=200))
                (fix,) = self.published(node, topic)
                self.assertEqual((fix.latitude, fix.longitude), (51.3, -2.5))
                self.assertEqual((fix.status.status, fix.status.service), (0, 1))
                self.assertTrue(math.isnan(fix.altitude))
                self.assertEqual(fix.position_covariance_type, 0)
                self.assertEqual(fix.position_covariance, [0.0] * 9)
                self.assertEqual((fix.header.frame_id, fix.header.stamp), ('antenna', 'stamp'))

    def test_ekf_fix_loses_validity_when_status_expires_and_recovers(self):
        """Verify a stale EKF report cannot keep a geodetic fix valid."""
        node = self.make(ekf_status_timeout_s=0.5)
        position = _message('GLOBAL_POSITION_INT', lat=513000000, lon=-25000000)
        self.feed(node, _heartbeat(), _ekf_status())
        self.now += 0.5
        self.feed(node, position)
        self.now += 0.001
        self.feed(node, position)
        self.feed(node, _ekf_status(), position)
        first, expired, recovered = self.published(node, 'fc/ekf_fix')
        self.assertEqual((first.status.status, expired.status.status, recovered.status.status),
                         (0, -1, 0))
        for value in (expired.latitude, expired.longitude, expired.altitude):
            self.assertTrue(math.isnan(value))
        self.assertEqual((recovered.latitude, recovered.longitude), (51.3, -2.5))

    def test_gps_diagnostic_freshness_is_independent_per_receiver(self):
        """Verify refreshing GPS1 cannot mask a stale GPS2, and GPS2 can recover with no fix."""
        node = self.make(gps_timeout_s=0.5)
        self.feed(node, _heartbeat(), _gps(), _gps('GPS2_RAW'))
        self.now += 0.5
        self.feed(node, _gps())
        self.assertEqual(self.level(node, 'gps2')[0], FakeDiagnosticStatus.OK)
        self.now += 0.001
        self.assertEqual(self.level(node, 'gps1')[0], FakeDiagnosticStatus.OK)
        self.assertEqual(self.level(node, 'gps2'),
                         (FakeDiagnosticStatus.STALE, 'no GPS report', {}))
        self.feed(node, _gps('GPS2_RAW', fix_type=1))
        self.assertEqual(self.level(node, 'gps2')[0], FakeDiagnosticStatus.ERROR)

    def test_link_loss_clears_all_state_even_with_long_telemetry_timeouts(self):
        """Verify reconnection cannot reuse any pre-loss GPS, EKF or attitude sample."""
        node = self.make(fc_heartbeat_timeout_s=0.5, attitude_timeout_s=20.0,
                         ekf_status_timeout_s=20.0, gps_timeout_s=20.0)
        self.good_state(node)
        self.feed(node, _gps(), _gps('GPS2_RAW'))
        self.now += 0.501
        node._poll_link()
        self.feed(node, _heartbeat(), _local_position(),
                  _message('GLOBAL_POSITION_INT', lat=513000000, lon=-25000000))
        self.assertEqual(self.published(node, 'fc/odom'), [])
        self.assertEqual(self.published(node, 'fc/ekf_fix')[-1].status.status, -1)
        for name in ('ekf3', 'gps1', 'gps2'):
            self.assertEqual(self.level(node, name)[0], FakeDiagnosticStatus.STALE)
        self.feed(node, _ekf_status(), _attitude(), _local_position(), _gps(), _gps('GPS2_RAW'))
        self.assertEqual(len(self.published(node, 'fc/odom')), 1)
        for name in ('ekf3', 'gps1', 'gps2'):
            self.assertEqual(self.level(node, name)[0], FakeDiagnosticStatus.OK)

    def test_interval_request_failure_does_not_skip_other_requests_or_future_retries(self):
        """Verify a transient send error leaves the remaining requests and next retry usable."""
        node = self.make()
        send = self.connection.mav.command_long_send
        send.side_effect = [OSError('port busy'), *([None] * 5)]
        self.feed(node, _heartbeat())
        self.assertEqual(send.call_count, 6)
        self.assertEqual(node.get_logger().levels().count('error'), 1)
        send.side_effect = None
        node._rerequest_message_intervals()
        self.assertEqual(send.call_count, 12)
        self.assertEqual(send.call_args_list[:6], send.call_args_list[6:])

    def test_polling_resumes_queued_telemetry_after_the_message_budget(self):
        """Verify the bounded poll leaves useful queued messages for the next callback."""
        node = self.make()
        self.good_state(node)
        self.pending += [_message('STATUSTEXT') for _ in range(200)] + [_local_position()]
        node._poll_link()
        self.assertEqual(len(self.pending), 1)
        self.assertEqual(self.published(node, 'fc/odom'), [])
        node._poll_link()
        self.assertEqual(self.pending, [])
        self.assertEqual(len(self.published(node, 'fc/odom')), 1)


if __name__ == '__main__':
    unittest.main()
