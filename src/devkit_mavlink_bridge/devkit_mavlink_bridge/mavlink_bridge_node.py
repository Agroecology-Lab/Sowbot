"""devkit_mavlink_bridge -- cmd_vel/localisation <-> ArduPilot Rover on LEVIA-H7.

See research/ardurover.md in Sowbot_Data for the full design and the
open items list. This node currently implements the outbound half
(cmd_vel -> SET_POSITION_TARGET_LOCAL_NED) only; the inbound half
(FusionCore pose -> GPS_INPUT, see modules/gps_input_handler.py) is
blocked on confirming FusionCore's output topic/type/rate first.

TODO (tracked in ardurover.md, not duplicated here -- update there,
not just in this comment, when one of these lands):
  1. Which LEVIA-H7 UART carries MAVLink (safety_roadmap.md O31).
  2. FusionCore output topic/type/rate -- needed for gps_input_handler.
  3. EKF-origin-on-first-boot behaviour when GPS_INPUT is the only GPS
     source -- may need an initial GPS_INPUT burst before GUIDED will
     accept velocity commands at all.
  5. Connection string / transport for pymavlink (serial device, baud)
     depends on item 1.
  6. Flight-controller GPS1_TYPE=14 parameter change -- not this node's job, but
     nothing here works until it's set.
"""

import os
import time

import rclpy
from geometry_msgs.msg import Twist
from pymavlink import mavutil
from rclpy.node import Node

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

_MAVLINK_ENDPOINT_ENV = 'MAVLINK_ENDPOINT'
_MAVLINK_ENDPOINT_PLACEHOLDER = 'TODO_SERIAL_DEVICE_HERE'
_MAVLINK_BAUD_ENV = 'MAVLINK_BAUD'
_DEFAULT_MAVLINK_BAUD = 115200  # must match SERIALn_BAUD on the flight controller


class MavlinkBridgeNode(Node):
    """Bridges cmd_vel to an ArduPilot Rover RTU over MAVLink (GUIDED mode)."""

    def __init__(self):
        """Connect to MAVLink and register the ROS subscription and timer.

        ROS parameters (seconds unless noted, default in brackets): republish_period_s [0.1],
        cmd_vel_timeout_s [0.5], max_linear_x_mps [1.6], max_angular_z_radps [1.0],
        heartbeat_period_s [1.0], fc_heartbeat_timeout_s [3.0], require_fc_heartbeat [true].
        With require_fc_heartbeat the bridge forwards zero velocity unless the flight controller's
        HEARTBEAT was seen within fc_heartbeat_timeout_s. MAVLINK_ENDPOINT selects the connection;
        MAVLINK_BAUD defaults to 115200. The bridge does not arm or change mode.
        Raise ValueError for a nonpositive period or limit, a nonpositive or nonfinite timeout,
        a missing or placeholder endpoint, or a baud value that cannot be parsed as an int.
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
        self._monitor = HeartbeatMonitor(float(self.get_parameter('fc_heartbeat_timeout_s').value))
        self._link_state = NEVER
        self._fc_armed: bool | None = None
        self._fc_mode: int | None = None
        # Raises ValueError for a non-positive or non-finite timeout.
        self._watchdog = CmdVelWatchdog(cmd_vel_timeout_s)
        self._was_stale = True

        mavlink_endpoint = os.environ.get(_MAVLINK_ENDPOINT_ENV, '').strip()
        if not mavlink_endpoint or mavlink_endpoint.upper() == _MAVLINK_ENDPOINT_PLACEHOLDER:
            raise ValueError(
                f'{_MAVLINK_ENDPOINT_ENV} must be set to a valid MAVLink connection endpoint')
        baud = int(os.environ.get(_MAVLINK_BAUD_ENV, _DEFAULT_MAVLINK_BAUD))

        self._mav = mavutil.mavlink_connection(mavlink_endpoint, baud=baud)

        self.create_subscription(Twist, 'cmd_vel', self._on_cmd_vel, 10)
        self.create_timer(republish_period_s, self._republish)
        self.create_timer(heartbeat_period_s, self._send_heartbeat)

        self.get_logger().info(
            f'devkit_mavlink_bridge up on {mavlink_endpoint} @ {baud} baud: sending every '
            f'{republish_period_s}s, zero velocity once cmd_vel is older than {cmd_vel_timeout_s}s')

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
        """Read pending messages, record the flight controller's HEARTBEATs, and log link changes."""
        while True:
            try:
                message = self._mav.recv_match(type='HEARTBEAT', blocking=False)
            except OSError as error:
                self.get_logger().error(
                    f'MAVLink read failed: {error}', throttle_duration_sec=_SEND_ERROR_LOG_PERIOD_S)
                break
            if message is None:
                break
            if (message.type == mavutil.mavlink.MAV_TYPE_GCS
                    or message.autopilot == mavutil.mavlink.MAV_AUTOPILOT_INVALID):
                continue  # another ground station, or our own echo
            self._monitor.beat()
            self._note_flight_controller(message)
        self._log_link_change()

    def _note_flight_controller(self, message) -> None:
        """Log arming and mode changes reported in the flight controller's HEARTBEAT."""
        armed = bool(message.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
        if armed != self._fc_armed or message.custom_mode != self._fc_mode:
            self.get_logger().info(
                f'flight controller {"armed" if armed else "disarmed"}, custom_mode {message.custom_mode}')
            self._fc_armed, self._fc_mode = armed, message.custom_mode

    def _log_link_change(self) -> None:
        """Log when the flight-controller link is first seen, lost or regained."""
        state = self._monitor.state()
        if state == self._link_state:
            return
        if state == LOST:
            self.get_logger().warn(
                f'no flight controller heartbeat for {self._monitor.timeout_s}s: link lost')
        elif state == OK:
            self.get_logger().info('flight controller heartbeat received')
        self._link_state = state


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
