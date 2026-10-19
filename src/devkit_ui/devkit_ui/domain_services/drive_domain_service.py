from __future__ import annotations

from collections.abc import Callable

from geometry_msgs.msg import Twist
from std_msgs.msg import Bool

from devkit_ui.qos import SAFETY_QOS
from devkit_ui.ros_gateway import RosGateway


class DriveDomainService:
    """Publish cmd_vel and the soft estop request, and report the driver's acknowledgement.

    estop/soft is the request; estop/soft/state is the state the driver confirms after applying it.
    """

    def __init__(self, ros: RosGateway) -> None:
        """Create the velocity and soft estop publishers through the ROS gateway."""
        self._ros = ros

        self._cmd_vel_pub = ros.create_publisher(Twist, 'cmd_vel', 1)
        self._estop_pub = ros.create_publisher(Bool, 'estop/soft', SAFETY_QOS)
        self._soft_estop_callbacks: list[Callable[[bool], None]] = []
        ros.create_subscription(Bool, 'estop/soft/state', self._on_soft_estop_state, SAFETY_QOS)

    def send_speed(self, x: float, y: float) -> None:
        """Publish a velocity command, propagating publisher errors.

        Parameters:
            x (float): Forward linear velocity in meters per second.
            y (float): Turn command in radians per second; published as angular.z = -y.
        """
        msg = Twist()
        msg.linear.x = x
        msg.angular.z = -y
        self._cmd_vel_pub.publish(msg)

    def register_soft_estop_state_callback(self, callback: Callable[[bool], None]) -> None:
        """Call callback with True or False each time the driver confirms the soft estop state.

        Callbacks run on the ROS executor thread.
        """
        self._soft_estop_callbacks.append(callback)

    def _on_soft_estop_state(self, msg: Bool) -> None:
        """Forward the driver's confirmed soft estop state to every registered callback."""
        for callback in self._soft_estop_callbacks:
            callback(bool(msg.data))

    def toggle_estop(self, current: bool) -> bool:
        """Publish the request for the opposite of current and return the requested state.

        The request is not confirmation: the confirmed state arrives through the callbacks.
        """
        new_state = not current
        msg = Bool()
        msg.data = new_state
        self._estop_pub.publish(msg)
        return new_state
