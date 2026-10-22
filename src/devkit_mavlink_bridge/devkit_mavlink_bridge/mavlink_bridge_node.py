"""devkit_mavlink_bridge -- cmd_vel <-> ArduPilot Rover on LEVIA-H7.

The LEVIA-H7 EKF3 is the sole estimator (see mavlink.md). FusionCore, if run, is shadow
only and nothing here feeds the flight controller a position.

Outbound: cmd_vel -> SET_POSITION_TARGET_LOCAL_NED, plus a ground-station HEARTBEAT.
Inbound: EKF3 state and GPS status, published as:

  fc/odom         nav_msgs/Odometry     LOCAL_POSITION_NED + ATTITUDE_QUATERNION, ENU/FLU,
                                        covariances unknown (-1 in element 0)
  fc/ekf_fix      sensor_msgs/NavSatFix GLOBAL_POSITION_INT, EKF3 geodetic position
  fc/gps1/fix     sensor_msgs/NavSatFix GPS_RAW_INT, receiver output, not EKF3
  fc/gps2/fix     sensor_msgs/NavSatFix GPS2_RAW, receiver output, not EKF3
  fc/diagnostics  diagnostic_msgs/DiagnosticArray  link, EKF3 flags and variance ratios, GPS

Nothing is published from stale or invalid data, so a consumer sees a gap rather than a
confident old value. No TF is broadcast: FusionCore may still own odom -> base_link.

Open item (tracked in safety_roadmap.md, not duplicated here): which LEVIA-H7 UART carries
MAVLink (O31). MAVLINK_ENDPOINT and MAVLINK_BAUD depend on it.
"""

import math
import os
import time

import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from pymavlink import mavutil
from rclpy.node import Node
from sensor_msgs.msg import NavSatFix

from devkit_mavlink_bridge.modules import gps_status_handler as gps
from devkit_mavlink_bridge.modules.ekf_state_handler import (
    build_odometry,
    ekf_absolute_position_valid,
    ekf_flag_names,
    ekf_pose_valid,
)
from devkit_mavlink_bridge.modules.latest_messages import LatestMessages
from devkit_mavlink_bridge.modules.link_monitor import LOST, NEVER, OK, HeartbeatMonitor
from devkit_mavlink_bridge.modules.position_target_handler import (
    twist_to_position_target,
)
from devkit_mavlink_bridge.modules.velocity_limits import limit_velocity
from devkit_mavlink_bridge.modules.velocity_watchdog import CmdVelWatchdog

# ArduPilot Rover's GUID_TIMEOUT (default 3.0 s) only stops the rover if the
# bridge stops sending. The bridge therefore must not keep re-sending a command
# its upstream has stopped refreshing: after CMD_VEL_TIMEOUT_S without a new
# cmd_vel it sends an explicit zero velocity instead (see CmdVelWatchdog).
# At the 1.6 m/s top speed the 3 s default alone is ~4.8 m of travel. Lowering
# GUID_TIMEOUT on the flight controller (try ~1 s) is a second layer; measure
# the real stopping distance, these are starting values only.
_DEFAULT_REPUBLISH_PERIOD_S = 0.1
_DEFAULT_CMD_VEL_TIMEOUT_S = 0.5

# Starting values to tune on the vehicle. 1.6 m/s is the platform top speed named above;
# the angular limit is a placeholder until the real turn-rate limit is measured.
_DEFAULT_MAX_LINEAR_X_MPS = 1.6
_DEFAULT_MAX_ANGULAR_Z_RADPS = 1.0

# The bridge announces itself as a ground station once per heartbeat period, as ArduPilot's
# GCS failsafe expects, and treats the flight controller as gone after the timeout.
_DEFAULT_HEARTBEAT_PERIOD_S = 1.0
_DEFAULT_FC_HEARTBEAT_TIMEOUT_S = 3.0
_SEND_ERROR_LOG_PERIOD_S = 1.0

# Inbound state. ATTITUDE_QUATERNION arrives fast and is paired with each LOCAL_POSITION_NED,
# so it must be recent; EKF_STATUS_REPORT is slow, so its timeout is longer.
_DEFAULT_POLL_PERIOD_S = 0.02
_DEFAULT_STATE_RATE_HZ = 10.0
_DEFAULT_GPS_RATE_HZ = 2.0
_DEFAULT_EKF_STATUS_RATE_HZ = 2.0
_DEFAULT_ATTITUDE_TIMEOUT_S = 0.3
_DEFAULT_EKF_STATUS_TIMEOUT_S = 2.0
_DEFAULT_GPS_TIMEOUT_S = 3.0
_DEFAULT_DIAGNOSTICS_PERIOD_S = 1.0
_INTERVAL_REREQUEST_PERIOD_S = 10.0
_MAX_MESSAGES_PER_POLL = 200

# Message ids for MAV_CMD_SET_MESSAGE_INTERVAL. Checked against pymavlink in the tests.
_MSG_GPS_RAW_INT = 24
_MSG_ATTITUDE_QUATERNION = 31
_MSG_LOCAL_POSITION_NED = 32
_MSG_GLOBAL_POSITION_INT = 33
_MSG_GPS2_RAW = 124
_MSG_EKF_STATUS_REPORT = 193

# Unknown covariance: -1 in the first element, as for sensor_msgs/Imu.
_UNKNOWN_COVARIANCE = [-1.0] + [0.0] * 35

_MAVLINK_ENDPOINT_ENV = 'MAVLINK_ENDPOINT'
_MAVLINK_ENDPOINT_PLACEHOLDER = 'TODO_SERIAL_DEVICE_HERE'
_MAVLINK_BAUD_ENV = 'MAVLINK_BAUD'
_DEFAULT_MAVLINK_BAUD = 115200  # must match SERIALn_BAUD on the flight controller


class MavlinkBridgeNode(Node):
    """Bridges cmd_vel out to, and EKF3 state and GPS status in from, ArduPilot Rover over MAVLink."""

    def __init__(self):
        """Connect to MAVLink and register the ROS subscription and timer.

        ROS parameters (seconds unless noted, default in brackets): republish_period_s [0.1],
        cmd_vel_timeout_s [0.5], max_linear_x_mps [1.6], max_angular_z_radps [1.0],
        heartbeat_period_s [1.0], fc_heartbeat_timeout_s [3.0], require_fc_heartbeat [true].
        Inbound: poll_period_s [0.02], state_rate_hz [10] (attitude, local and global position),
        gps_rate_hz [2], ekf_status_rate_hz [2] (rates requested from the flight controller),
        attitude_timeout_s [0.3], ekf_status_timeout_s [2.0], gps_timeout_s [3.0],
        diagnostics_period_s [1.0], odom_frame_id ['odom'], base_frame_id ['base_link'],
        gps_frame_id ['gps_link'].
        With require_fc_heartbeat the bridge forwards zero velocity unless the flight controller's
        HEARTBEAT was seen within fc_heartbeat_timeout_s. MAVLINK_ENDPOINT selects the connection;
        MAVLINK_BAUD defaults to 115200. The bridge does not arm or change mode.
        Raise ValueError for a nonpositive period or limit, a nonpositive or nonfinite timeout,
        a nonpositive or nonfinite rate, a missing or placeholder endpoint, or a baud value that
        cannot be parsed as an int.
        MAVLink connection errors propagate.
        """
        super().__init__('devkit_mavlink_bridge_node')

        self.declare_parameter('republish_period_s', _DEFAULT_REPUBLISH_PERIOD_S)
        self.declare_parameter('cmd_vel_timeout_s', _DEFAULT_CMD_VEL_TIMEOUT_S)
        self.declare_parameter('max_linear_x_mps', _DEFAULT_MAX_LINEAR_X_MPS)
        self.declare_parameter('max_angular_z_radps', _DEFAULT_MAX_ANGULAR_Z_RADPS)
        self.declare_parameter('heartbeat_period_s', _DEFAULT_HEARTBEAT_PERIOD_S)
        self.declare_parameter('fc_heartbeat_timeout_s', _DEFAULT_FC_HEARTBEAT_TIMEOUT_S)
        self.declare_parameter('require_fc_heartbeat', True)
        self.declare_parameter('poll_period_s', _DEFAULT_POLL_PERIOD_S)
        self.declare_parameter('state_rate_hz', _DEFAULT_STATE_RATE_HZ)
        self.declare_parameter('gps_rate_hz', _DEFAULT_GPS_RATE_HZ)
        self.declare_parameter('ekf_status_rate_hz', _DEFAULT_EKF_STATUS_RATE_HZ)
        self.declare_parameter('attitude_timeout_s', _DEFAULT_ATTITUDE_TIMEOUT_S)
        self.declare_parameter('ekf_status_timeout_s', _DEFAULT_EKF_STATUS_TIMEOUT_S)
        self.declare_parameter('gps_timeout_s', _DEFAULT_GPS_TIMEOUT_S)
        self.declare_parameter('diagnostics_period_s', _DEFAULT_DIAGNOSTICS_PERIOD_S)
        self.declare_parameter('odom_frame_id', 'odom')
        self.declare_parameter('base_frame_id', 'base_link')
        self.declare_parameter('gps_frame_id', 'gps_link')
        republish_period_s = float(self.get_parameter('republish_period_s').value)
        cmd_vel_timeout_s = float(self.get_parameter('cmd_vel_timeout_s').value)
        heartbeat_period_s = float(self.get_parameter('heartbeat_period_s').value)
        self._max_linear_x = float(self.get_parameter('max_linear_x_mps').value)
        self._max_angular_z = float(self.get_parameter('max_angular_z_radps').value)
        self._require_fc_heartbeat = bool(self.get_parameter('require_fc_heartbeat').value)
        if republish_period_s <= 0.0:
            raise ValueError('republish_period_s must be positive')
        if heartbeat_period_s <= 0.0:
            raise ValueError('heartbeat_period_s must be positive')
        # Raises ValueError for a non-positive or non-finite limit.
        limit_velocity(0.0, 0.0, self._max_linear_x, self._max_angular_z)
        poll_period_s = self._positive('poll_period_s')
        diagnostics_period_s = self._positive('diagnostics_period_s')
        self._attitude_timeout_s = self._positive('attitude_timeout_s')
        self._ekf_status_timeout_s = self._positive('ekf_status_timeout_s')
        self._gps_timeout_s = self._positive('gps_timeout_s')
        self._message_rates_hz = {
            _MSG_ATTITUDE_QUATERNION: self._positive('state_rate_hz'),
            _MSG_LOCAL_POSITION_NED: self._positive('state_rate_hz'),
            _MSG_GLOBAL_POSITION_INT: self._positive('state_rate_hz'),
            _MSG_GPS_RAW_INT: self._positive('gps_rate_hz'),
            _MSG_GPS2_RAW: self._positive('gps_rate_hz'),
            _MSG_EKF_STATUS_REPORT: self._positive('ekf_status_rate_hz'),
        }
        self._odom_frame_id = str(self.get_parameter('odom_frame_id').value)
        self._base_frame_id = str(self.get_parameter('base_frame_id').value)
        self._gps_frame_id = str(self.get_parameter('gps_frame_id').value)
        self._monitor = HeartbeatMonitor(float(self.get_parameter('fc_heartbeat_timeout_s').value))
        self._link_state = NEVER
        self._fc_armed: bool | None = None
        self._fc_mode: int | None = None
        # Raises ValueError for a non-positive or non-finite timeout.
        self._watchdog = CmdVelWatchdog(cmd_vel_timeout_s)
        self._was_stale = True
        self._latest = LatestMessages()
        self._odom_blocked: str | None = 'no data yet'

        mavlink_endpoint = os.environ.get(_MAVLINK_ENDPOINT_ENV, '').strip()
        if not mavlink_endpoint or mavlink_endpoint.upper() == _MAVLINK_ENDPOINT_PLACEHOLDER:
            raise ValueError(
                f'{_MAVLINK_ENDPOINT_ENV} must be set to a valid MAVLink connection endpoint')
        baud = int(os.environ.get(_MAVLINK_BAUD_ENV, _DEFAULT_MAVLINK_BAUD))

        self._mav = mavutil.mavlink_connection(mavlink_endpoint, baud=baud)

        self.create_subscription(Twist, 'cmd_vel', self._on_cmd_vel, 10)
        self._odom_pub = self.create_publisher(Odometry, 'fc/odom', 10)
        self._ekf_fix_pub = self.create_publisher(NavSatFix, 'fc/ekf_fix', 10)
        self._gps_pubs = {
            'GPS_RAW_INT': self.create_publisher(NavSatFix, 'fc/gps1/fix', 10),
            'GPS2_RAW': self.create_publisher(NavSatFix, 'fc/gps2/fix', 10),
        }
        self._diagnostics_pub = self.create_publisher(DiagnosticArray, 'fc/diagnostics', 10)
        self._handlers = {
            'ATTITUDE_QUATERNION': self._on_attitude,
            'LOCAL_POSITION_NED': self._on_local_position,
            'GLOBAL_POSITION_INT': self._on_global_position,
            'GPS_RAW_INT': self._on_gps,
            'GPS2_RAW': self._on_gps,
            'EKF_STATUS_REPORT': self._on_ekf_status,
            'COMMAND_ACK': self._on_command_ack,
        }
        self.create_timer(republish_period_s, self._republish)
        self.create_timer(heartbeat_period_s, self._send_heartbeat)
        self.create_timer(poll_period_s, self._poll_link)
        self.create_timer(diagnostics_period_s, self._publish_diagnostics)
        self.create_timer(_INTERVAL_REREQUEST_PERIOD_S, self._rerequest_message_intervals)

        self.get_logger().info(
            f'devkit_mavlink_bridge up on {mavlink_endpoint} @ {baud} baud: sending every '
            f'{republish_period_s}s, zero velocity once cmd_vel is older than {cmd_vel_timeout_s}s')

    def _positive(self, name: str) -> float:
        """Return a ROS parameter as a float; raise ValueError unless it is positive and finite."""
        value = float(self.get_parameter(name).value)
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(f'{name} must be a positive finite number, got {value!r}')
        return value

    def _on_cmd_vel(self, msg: Twist) -> None:
        """Record linear.x (m/s), angular.z (rad/s), and their monotonic arrival time.

        If either component is nonfinite, leave the previous command and arrival time unchanged.
        """
        self._watchdog.update(msg.linear.x, msg.angular.z)

    def _republish(self) -> None:
        """Send the current velocity target, or zero if no command exists or it has gone stale.

        Zero is also sent while the flight controller's heartbeat is missing and
        require_fc_heartbeat is set. Velocity is clamped to the configured limits.
        Send errors that are OSError are logged and the next period tries again.
        """
        self._poll_link()
        linear_x, angular_z, stale = self._watchdog.current()
        if stale != self._was_stale:
            if stale:
                self.get_logger().warn(
                    f'cmd_vel older than {self._watchdog.timeout_s}s or absent: commanding zero velocity')
            else:
                self.get_logger().info('cmd_vel fresh again: forwarding commands')
            self._was_stale = stale
        if self._require_fc_heartbeat and self._monitor.state() != OK:
            linear_x, angular_z = 0.0, 0.0
        linear_x, angular_z = limit_velocity(linear_x, angular_z, self._max_linear_x, self._max_angular_z)
        fields = twist_to_position_target(
            linear_x=linear_x,
            angular_z=angular_z,
            time_boot_ms=(time.monotonic_ns() // 1_000_000) & 0xFFFFFFFF,
            target_system=self._mav.target_system,
            target_component=self._mav.target_component,
        )
        self._send(self._mav.mav.set_position_target_local_ned_send, **fields)

    def _send_heartbeat(self) -> None:
        """Announce the bridge as a ground station so ArduPilot's GCS failsafe sees a live link."""
        self._send(self._mav.mav.heartbeat_send,
                   mavutil.mavlink.MAV_TYPE_GCS, mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                   0, 0, mavutil.mavlink.MAV_STATE_ACTIVE)

    def _send(self, send, *args, **fields) -> None:
        """Call a pymavlink send function, logging OSError (including serial errors) instead of raising."""
        try:
            send(*args, **fields)
        except OSError as error:
            self.get_logger().error(
                f'MAVLink send failed: {error}', throttle_duration_sec=_SEND_ERROR_LOG_PERIOD_S)

    def _poll_link(self) -> None:
        """Read pending messages, dispatch them, and log link changes.

        HEARTBEATs from the flight controller drive the link monitor. State messages are used
        only while the link is ok and only from the flight controller's system id. At most
        _MAX_MESSAGES_PER_POLL messages are handled per call so a flood cannot starve the timers.
        """
        for _ in range(_MAX_MESSAGES_PER_POLL):
            try:
                message = self._mav.recv_match(blocking=False)
            except OSError as error:
                self.get_logger().error(
                    f'MAVLink read failed: {error}', throttle_duration_sec=_SEND_ERROR_LOG_PERIOD_S)
                break
            if message is None:
                break
            kind = message.get_type()
            if kind == 'HEARTBEAT':
                self._on_heartbeat(message)
            elif kind in self._handlers and self._from_flight_controller(message):
                self._handlers[kind](message)
        self._log_link_change()

    def _from_flight_controller(self, message) -> bool:
        """Return True for a message from the selected flight controller while its link is ok."""
        if self._monitor.state() != OK:
            return False
        target = self._mav.target_system
        return not target or message.get_srcSystem() == target

    def _on_heartbeat(self, message) -> None:
        """Record a HEARTBEAT from the flight controller; ignore other ground stations and echoes."""
        if (message.type == mavutil.mavlink.MAV_TYPE_GCS
                or message.autopilot == mavutil.mavlink.MAV_AUTOPILOT_INVALID):
            return  # another ground station, or our own echo
        if self._mav.target_system and message.get_srcSystem() != self._mav.target_system:
            return
        self._monitor.beat()
        self._note_flight_controller(message)

    def _note_flight_controller(self, message) -> None:
        """Log arming and mode changes reported in the flight controller's HEARTBEAT."""
        armed = bool(message.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
        if armed != self._fc_armed or message.custom_mode != self._fc_mode:
            self.get_logger().info(
                f'flight controller {"armed" if armed else "disarmed"}, custom_mode {message.custom_mode}')
            self._fc_armed, self._fc_mode = armed, message.custom_mode

    def _log_link_change(self) -> None:
        """Log when the flight-controller link is first seen, lost or regained.

        On loss the stored state is discarded, so nothing from before the gap can be reused.
        When the link becomes ok the message rates are requested again, as a rebooted flight
        controller forgets them.
        """
        state = self._monitor.state()
        if state == self._link_state:
            return
        if state == LOST:
            self.get_logger().warn(
                f'no flight controller heartbeat for {self._monitor.timeout_s}s: link lost')
            self._latest.clear()
        elif state == OK:
            self.get_logger().info('flight controller heartbeat received')
            self._request_message_intervals()
        self._link_state = state

    def _request_message_intervals(self) -> None:
        """Ask the flight controller for the state and GPS messages at the configured rates.

        Uses MAV_CMD_SET_MESSAGE_INTERVAL, so SRn_* parameters need not be set.
        """
        for message_id, rate_hz in self._message_rates_hz.items():
            self._send(
                self._mav.mav.command_long_send,
                self._mav.target_system, self._mav.target_component,
                mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, 0,
                message_id, int(1e6 / rate_hz), 0, 0, 0, 0, 0)

    def _rerequest_message_intervals(self) -> None:
        """Repeat the interval requests while the link is ok, in case a reboot cleared them."""
        if self._monitor.state() == OK:
            self._request_message_intervals()

    def _on_command_ack(self, message) -> None:
        """Warn when the flight controller refuses a message-interval request."""
        if (message.command == mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL
                and message.result != mavutil.mavlink.MAV_RESULT_ACCEPTED):
            self.get_logger().warn(
                f'flight controller refused a message-interval request (result {message.result})',
                throttle_duration_sec=_SEND_ERROR_LOG_PERIOD_S)

    def _on_attitude(self, message) -> None:
        """Keep the latest ATTITUDE_QUATERNION to pair with the next LOCAL_POSITION_NED."""
        self._latest.put('attitude', message)

    def _on_ekf_status(self, message) -> None:
        """Keep the latest EKF_STATUS_REPORT, which gates every EKF3-derived publication."""
        self._latest.put('ekf_status', message)

    def _on_local_position(self, message) -> None:
        """Publish fc/odom from a LOCAL_POSITION_NED and the latest attitude, if both are usable.

        Nothing is published unless EKF3 reports a valid pose (see ekf_pose_valid), the
        EKF_STATUS_REPORT is recent and the attitude is recent. The reason is logged on change.
        """
        attitude = self._latest.fresh('attitude', self._attitude_timeout_s)
        ekf_status = self._latest.fresh('ekf_status', self._ekf_status_timeout_s)
        odometry = None
        if ekf_status is None:
            blocked = f'no EKF_STATUS_REPORT within {self._ekf_status_timeout_s}s'
        elif not ekf_pose_valid(ekf_status.flags):
            blocked = f'EKF3 pose not valid (flags {", ".join(ekf_flag_names(ekf_status.flags)) or "none"})'
        elif attitude is None:
            blocked = f'no ATTITUDE_QUATERNION within {self._attitude_timeout_s}s'
        else:
            odometry = build_odometry(
                message.x, message.y, message.z, message.vx, message.vy, message.vz,
                attitude.q1, attitude.q2, attitude.q3, attitude.q4,
                attitude.rollspeed, attitude.pitchspeed, attitude.yawspeed)
            blocked = None if odometry else 'nonfinite position, velocity or attitude'
        self._note_odom_state(blocked)
        if odometry is None:
            return
        msg = Odometry()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self._odom_frame_id
        msg.child_frame_id = self._base_frame_id
        msg.pose.pose.position.x, msg.pose.pose.position.y, msg.pose.pose.position.z = odometry.position
        (msg.pose.pose.orientation.x, msg.pose.pose.orientation.y,
         msg.pose.pose.orientation.z, msg.pose.pose.orientation.w) = odometry.orientation
        msg.twist.twist.linear.x, msg.twist.twist.linear.y, msg.twist.twist.linear.z = odometry.linear_velocity
        msg.twist.twist.angular.x, msg.twist.twist.angular.y, msg.twist.twist.angular.z = odometry.angular_velocity
        msg.pose.covariance = list(_UNKNOWN_COVARIANCE)
        msg.twist.covariance = list(_UNKNOWN_COVARIANCE)
        self._odom_pub.publish(msg)

    def _note_odom_state(self, blocked: str | None) -> None:
        """Log once when fc/odom stops being published, with the reason, and once when it resumes."""
        if blocked == self._odom_blocked:
            return
        if blocked is None:
            self.get_logger().info('EKF3 state valid: publishing fc/odom')
        else:
            self.get_logger().warn(f'not publishing fc/odom: {blocked}')
        self._odom_blocked = blocked

    def _on_global_position(self, message) -> None:
        """Publish fc/ekf_fix from GLOBAL_POSITION_INT when EKF3 reports an absolute position.

        The altitude is NaN: the message carries height above mean sea level, and NavSatFix
        wants height above the WGS84 ellipsoid. The covariance is unknown.
        """
        ekf_status = self._latest.fresh('ekf_status', self._ekf_status_timeout_s)
        valid = ekf_status is not None and ekf_absolute_position_valid(ekf_status.flags)
        status = gps.STATUS_FIX if valid else gps.STATUS_NO_FIX
        self._publish_navsat(self._ekf_fix_pub, gps.navsat_fields(
            status, message.lat, message.lon, float('nan')))

    def _on_gps(self, message) -> None:
        """Publish the receiver's own fix for GPS_RAW_INT (gps1) or GPS2_RAW (gps2), and keep it."""
        kind = message.get_type()
        self._latest.put(kind, message)
        self._publish_navsat(self._gps_pubs[kind], gps.gps_raw_to_navsat(
            message.fix_type, message.lat, message.lon, getattr(message, 'alt_ellipsoid', 0),
            getattr(message, 'h_acc', 0), getattr(message, 'v_acc', 0)))

    def _publish_navsat(self, publisher, fields: dict) -> None:
        """Publish NavSatFix fields stamped now in the GPS frame."""
        msg = NavSatFix()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self._gps_frame_id
        msg.status.status = fields['status']
        msg.status.service = fields['service']
        msg.latitude = fields['latitude']
        msg.longitude = fields['longitude']
        msg.altitude = fields['altitude']
        msg.position_covariance = fields['position_covariance']
        msg.position_covariance_type = fields['position_covariance_type']
        publisher.publish(msg)

    def _publish_diagnostics(self) -> None:
        """Publish link, EKF3 and GPS health on fc/diagnostics, marking missing data as stale."""
        array = DiagnosticArray()
        array.header.stamp = self.get_clock().now().to_msg()
        array.status = [
            self._diagnostic('flight_controller', *self._link_diagnostic()),
            self._diagnostic('ekf3', *self._ekf_diagnostic()),
            self._diagnostic('gps1', *gps.gps_diagnostic(
                self._latest.fresh('GPS_RAW_INT', self._gps_timeout_s))),
            self._diagnostic('gps2', *gps.gps_diagnostic(
                self._latest.fresh('GPS2_RAW', self._gps_timeout_s))),
        ]
        self._diagnostics_pub.publish(array)

    def _diagnostic(self, name: str, level: int, summary: str, values: dict) -> DiagnosticStatus:
        """Build a DiagnosticStatus from a level constant from gps_status_handler."""
        status = DiagnosticStatus()
        status.name = f'devkit_mavlink_bridge: {name}'
        status.hardware_id = 'levia_h7'
        status.level = {gps.LEVEL_OK: DiagnosticStatus.OK, gps.LEVEL_WARN: DiagnosticStatus.WARN,
                        gps.LEVEL_ERROR: DiagnosticStatus.ERROR,
                        gps.LEVEL_STALE: DiagnosticStatus.STALE}[level]
        status.message = summary
        status.values = [KeyValue(key=key, value=value) for key, value in values.items()]
        return status

    def _link_diagnostic(self):
        """Return (level, summary, values) for the flight controller link."""
        state = self._monitor.state()
        level = {OK: gps.LEVEL_OK, LOST: gps.LEVEL_ERROR}.get(state, gps.LEVEL_STALE)
        values = {'link': state}
        if self._fc_armed is not None:
            values['armed'] = str(self._fc_armed)
            values['custom_mode'] = str(self._fc_mode)
        return level, f'heartbeat {state}', values

    def _ekf_diagnostic(self):
        """Return (level, summary, values) for EKF3 from the latest EKF_STATUS_REPORT.

        The variances are EKF3's normalised innovation test ratios, reported as sent. The level
        follows ekf_pose_valid only; failsafe thresholds stay on the flight controller.
        """
        report = self._latest.fresh('ekf_status', self._ekf_status_timeout_s)
        if report is None:
            return gps.LEVEL_STALE, 'no EKF_STATUS_REPORT', {}
        values = {
            'flags': ', '.join(ekf_flag_names(report.flags)) or 'none',
            'velocity_variance': f'{report.velocity_variance:g}',
            'pos_horiz_variance': f'{report.pos_horiz_variance:g}',
            'pos_vert_variance': f'{report.pos_vert_variance:g}',
            'compass_variance': f'{report.compass_variance:g}',
            'terrain_alt_variance': f'{report.terrain_alt_variance:g}',
        }
        if ekf_pose_valid(report.flags):
            return gps.LEVEL_OK, 'pose valid', values
        return gps.LEVEL_ERROR, 'pose not valid', values


def main(args=None):
    """Run the MAVLink bridge node until ROS shuts down."""
    rclpy.init(args=args)
    node = MavlinkBridgeNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
