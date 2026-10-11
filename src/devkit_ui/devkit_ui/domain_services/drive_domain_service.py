from __future__ import annotations

from geometry_msgs.msg import Twist
from std_msgs.msg import Bool

from devkit_ui.qos import SAFETY_QOS
from devkit_ui.ros_gateway import RosGateway


class DriveDomainService:
    """Publish cmd_vel and soft estop."""

    def __init__(self, ros: RosGateway) -> None:
        """Create the velocity and soft estop publishers through the ROS gateway."""
        self._ros = ros

        self._cmd_vel_pub = ros.create_publisher(Twist, 'cmd_vel', 1)
        self._estop_pub = ros.create_publisher(Bool, 'estop/soft', SAFETY_QOS)

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

    def toggle_estop(self, current: bool) -> bool:
        """Publish the new soft estop state and return it."""
        new_state = not current
        msg = Bool()
        msg.data = new_state
        self._estop_pub.publish(msg)
        return new_state
