import math
import time
from collections.abc import Callable

from geometry_msgs.msg import Twist
from rclpy.node import Node
from rosys.hardware import Wheels

from ..loop import run_on_rosys_loop

DEFAULT_CMD_VEL_TIMEOUT_S = 0.5
_CHECK_PERIOD_S = 0.1


class TwistHandler:
    """Relate ROS cmd_vel messages to RoSys wheel commands.

    A nonzero command is only held while cmd_vel keeps arriving: once the newest
    message is older than cmd_vel_timeout_s (ROS parameter, seconds, default 0.5)
    the wheels are stopped. Time comes from a monotonic clock, never ROS time.
    """

    def __init__(self, node: Node, wheels: Wheels, clock: Callable[[], float] = time.monotonic):
        """Validate the command timeout and register velocity input and watchdog callbacks."""
        self.log = node.get_logger()
        self._wheels = wheels
        self._clock = clock

        node.declare_parameter('cmd_vel_timeout_s', DEFAULT_CMD_VEL_TIMEOUT_S)
        self._timeout_s = float(node.get_parameter('cmd_vel_timeout_s').value)
        if not math.isfinite(self._timeout_s) or self._timeout_s <= 0.0:
            raise ValueError(f'cmd_vel_timeout_s must be a positive finite number, got {self._timeout_s!r}')

        self._last_command_time: float | None = None
        self._moving = False
        self._cmd_subscription = node.create_subscription(Twist, 'cmd_vel', self.handle_twist, 10)
        self._watchdog_timer = node.create_timer(_CHECK_PERIOD_S, self.check_stale)

    def handle_twist(self, cmd_msg: Twist) -> None:
        """Implement callback for cmd_vel message; ignore nonfinite components."""
        linear, angular = cmd_msg.linear.x, cmd_msg.angular.z
        if not (math.isfinite(linear) and math.isfinite(angular)):
            self.log.warn('Ignoring cmd_vel with a nonfinite component')
            return
        self._last_command_time = self._clock()
        self._moving = linear != 0.0 or angular != 0.0
        run_on_rosys_loop(self._wheels.drive(linear, angular))

    def check_stale(self) -> None:
        """Stop the wheels once if a nonzero command has not been refreshed within the timeout."""
        if not self._moving or self._last_command_time is None:
            return
        if self._clock() - self._last_command_time <= self._timeout_s:
            return
        self._moving = False
        self.log.warn(f'cmd_vel older than {self._timeout_s}s: stopping wheels')
        run_on_rosys_loop(self._wheels.stop())
