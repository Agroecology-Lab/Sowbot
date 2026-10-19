from rclpy.node import Node
from rosys.hardware import RobotBrain
from std_msgs.msg import Empty

from ..loop import run_on_rosys_loop


class RobotBrainHandler:
    """Handler for the Zauberzeug Robot Brain."""

    def __init__(self, node: Node, robot_brain: RobotBrain):
        self.log = node.get_logger()
        self._robot_brain = robot_brain

        self._enable_sub = node.create_subscription(Empty, 'esp/enable', self._handle_enable, 10)
        self._disable_sub = node.create_subscription(Empty, 'esp/disable', self._handle_disable, 10)
        self._reset_sub = node.create_subscription(Empty, 'esp/reset', self._handle_reset, 10)
        self._restart_sub = node.create_subscription(Empty, 'esp/restart', self._handle_restart, 10)
        self._configure_sub = node.create_subscription(
            Empty, 'esp/configure', self._handle_configure, 10)

    def _handle_enable(self, _: Empty) -> None:
        """Schedule ESP enablement on the RoSys event loop."""
        run_on_rosys_loop(self._robot_brain.enable_esp())

    def _handle_disable(self, _: Empty) -> None:
        """Schedule ESP disablement on the RoSys event loop."""
        run_on_rosys_loop(self._robot_brain.disable_esp())

    def _handle_reset(self, _: Empty) -> None:
        """Schedule an ESP reset on the RoSys event loop."""
        run_on_rosys_loop(self._robot_brain.reset_esp())

    def _handle_restart(self, _: Empty) -> None:
        """Schedule a Robot Brain restart on the RoSys event loop."""
        run_on_rosys_loop(self._robot_brain.restart())

    def _handle_configure(self, _: Empty) -> None:
        """Schedule Robot Brain configuration on the RoSys event loop."""
        run_on_rosys_loop(self._robot_brain.configure())
