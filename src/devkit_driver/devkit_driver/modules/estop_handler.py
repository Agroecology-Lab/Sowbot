import rosys
from rclpy.node import Node
from rosys.hardware import EStop, EStopHardware
from std_msgs.msg import Bool

from ..loop import run_on_rosys_loop
from ..qos import SAFETY_QOS


class EStopHandler:
    """Handle the estop.

    estop/soft is the request. estop/soft/state is the acknowledgement: it is published
    only after the soft e-stop has been applied, and latched, so a late subscriber such as
    the dashboard sees the real state. A request that fails publishes no acknowledgement.
    """
    FRONT_ID = 'front'
    BACK_ID = 'back'

    def __init__(self, node: Node, estop: EStop):
        self.log = node.get_logger()
        self._estop = estop

        # SAFETY_QOS is latched: a driver that starts after the last request still receives it.
        self.subscription = node.create_subscription(
            Bool, 'estop/soft', self.soft_estop_callback, SAFETY_QOS)
        self.estop_front_publisher = node.create_publisher(Bool, 'estop/front', SAFETY_QOS)
        self.estop_back_publisher = node.create_publisher(Bool, 'estop/back', SAFETY_QOS)
        self.estop_soft_state_publisher = node.create_publisher(Bool, 'estop/soft/state', SAFETY_QOS)

        self._estop.ESTOP_TRIGGERED.subscribe(self._handle_estop_triggered)
        self._estop.ESTOP_RELEASED.subscribe(self._handle_estop_released)
        rosys.on_startup(self._check_on_startup)

    def _check_on_startup(self) -> None:
        self._publish_soft_state()
        if not isinstance(self._estop, EStopHardware):
            return
        for name in self._estop.pins.keys():
            if name in self._estop.active_estops:
                self._handle_estop_triggered(name)
            else:
                self._handle_estop_released(name)

    def _handle_estop_triggered(self, name: str) -> None:
        if name == self.FRONT_ID:
            self.estop_front_publisher.publish(Bool(data=True))
        elif name == self.BACK_ID:
            self.estop_back_publisher.publish(Bool(data=True))

    def _handle_estop_released(self, name: str) -> None:
        if name == self.FRONT_ID:
            self.estop_front_publisher.publish(Bool(data=False))
        elif name == self.BACK_ID:
            self.estop_back_publisher.publish(Bool(data=False))

    def soft_estop_callback(self, msg: Bool):
        """Implement a callback for the estop."""
        run_on_rosys_loop(self._apply_soft_estop(msg.data))

    async def _apply_soft_estop(self, active: bool) -> None:
        """Apply the soft e-stop, then acknowledge the state it actually reached."""
        await self._estop.set_soft_estop(active)
        self._publish_soft_state()

    def _publish_soft_state(self) -> None:
        self.estop_soft_state_publisher.publish(Bool(data=self._estop.is_soft_estop_active))
