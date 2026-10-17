"""Thin ROS adapter acting as a temporary bridge during the architectural transition
to independent services.

This class allows various services to consume core ROS functionalities without
creating hard dependencies or tight coupling directly to the NiceGuiNode.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rclpy.node import Node

from devkit_ui.qos import QosSpec

if TYPE_CHECKING:
    from rclpy.clock import Clock
    from rclpy.publisher import Publisher
    from rclpy.qos import QoSProfile
    from rclpy.subscription import Subscription


class _RosLogHandler(logging.Handler):
    """Send Python log records to the wrapped node's ROS logger."""

    def __init__(self, node: Node) -> None:
        """Keep the node whose logger receives the records."""
        super().__init__()
        self._node = node

    def emit(self, record: logging.LogRecord) -> None:
        """Log the formatted record at the matching ROS severity.

        ERROR and CRITICAL records use ROS error; levels below INFO use ROS debug.
        Formatting or ROS logging failures are passed to logging.Handler.handleError.
        Each severity has its own call site because rclpy refuses a call site that changes
        severity between calls.
        """
        try:
            text = self.format(record)
            logger = self._node.get_logger()
            if record.levelno >= logging.ERROR:
                logger.error(text)
            elif record.levelno >= logging.WARNING:
                logger.warn(text)
            elif record.levelno >= logging.INFO:
                logger.info(text)
            else:
                logger.debug(text)
        except Exception:  # pylint: disable=broad-exception-caught
            self.handleError(record)


def to_qos_profile(qos: int | QosSpec | QoSProfile) -> int | QoSProfile:
    """Return qos as something rclpy accepts: a QosSpec becomes a QoSProfile, anything else is returned unchanged."""
    if not isinstance(qos, QosSpec):
        return qos
    # Imported here so modules that only need topics and services load without rclpy.qos.
    # pylint: disable=import-outside-toplevel
    from rclpy.qos import (
        DurabilityPolicy,
        Duration,
        HistoryPolicy,
        LivelinessPolicy,
        QoSProfile,
        ReliabilityPolicy,
    )
    # pylint: enable=import-outside-toplevel
    kwargs = {'depth': qos.depth, 'reliability': ReliabilityPolicy[qos.reliability.upper()]}
    if qos.durability is not None:
        kwargs['durability'] = DurabilityPolicy[qos.durability.upper()]
    if qos.history is not None:
        kwargs['history'] = HistoryPolicy[qos.history.upper()]
    if qos.liveliness is not None:
        kwargs['liveliness'] = LivelinessPolicy[qos.liveliness.upper()]
    if qos.liveliness_lease_sec is not None:
        kwargs['liveliness_lease_duration'] = Duration(seconds=qos.liveliness_lease_sec)
    return QoSProfile(**kwargs)


class TransformUnavailable(Exception):
    """A TF lookup failed because the transform is missing, disconnected or out of range."""


class RosGateway:
    """Wrap a ROS node to create publishers/subscriptions without coupling to it."""

    def __init__(self, node: Node) -> None:
        """Store the node used for ROS communication, logging, and time."""
        self._node = node
        self._wall_clock: Clock | None = None
        self._tf_buffer = None

    def create_publisher(self, msg_type, topic: str, qos: int | QosSpec | QoSProfile) -> Publisher:
        """Create a publisher on the wrapped node with the supplied type, topic, and QoS."""
        return self._node.create_publisher(msg_type, topic, to_qos_profile(qos))

    def create_subscription(
            self, msg_type, topic: str, callback, qos: int | QosSpec | QoSProfile) -> Subscription:
        """Create a subscription on the wrapped node with the supplied callback and QoS."""
        return self._node.create_subscription(msg_type, topic, callback, to_qos_profile(qos))

    def create_client(self, srv_type, name: str):
        """Create a service client on the wrapped node."""
        return self._node.create_client(srv_type, name)

    def create_action_client(self, action_type, name: str):
        """Create an action client on the wrapped node."""
        # Imported here so modules that only need topics and services load without rclpy.action.
        from rclpy.action import ActionClient  # pylint: disable=import-outside-toplevel
        return ActionClient(self._node, action_type, name)

    def get_bool_parameter(self, name: str, default: bool = False) -> bool:
        """Declare the node parameter name with default and return its value as a bool."""
        self._node.declare_parameter(name, default)
        return bool(self._node.get_parameter(name).value)

    def get_logger(self):
        """Return the wrapped node's logger."""
        return self._node.get_logger()

    def forward_python_logging(self, name: str) -> None:
        """Route records from the Python logger called name, and its children, to the ROS log.

        Set the named logger's level to INFO. Child records reach the handler through
        normal logging propagation and remain subject to each child's level and filters.
        Calling again replaces existing ROS forwarding handlers; other handlers and
        propagation settings are preserved.
        """
        python_logger = logging.getLogger(name)
        for handler in list(python_logger.handlers):
            if isinstance(handler, _RosLogHandler):
                python_logger.removeHandler(handler)
        python_logger.addHandler(_RosLogHandler(self._node))
        python_logger.setLevel(logging.INFO)

    def get_clock(self) -> Clock:
        """Return the wrapped node's clock."""
        return self._node.get_clock()

    def now_wall_sec(self) -> float:
        """Return node clock time in seconds, using simulated time when enabled."""
        return self._node.get_clock().now().nanoseconds * 1e-9

    def wall_clock(self) -> Clock:
        """Return a system-time clock that keeps running before simulated /clock exists."""
        if self._wall_clock is None:
            # Imported here so modules that only need topics and services load without rclpy.clock.
            from rclpy.clock import Clock, ClockType  # pylint: disable=import-outside-toplevel
            self._wall_clock = Clock(clock_type=ClockType.SYSTEM_TIME)
        return self._wall_clock

    def wall_time_sec(self) -> float:
        """Return system-clock seconds since the Unix epoch, independent of simulated time."""
        return self.wall_clock().now().nanoseconds * 1e-9

    def create_timer(self, period_sec: float, callback, clock: Clock | None = None):
        """Create and return a repeating timer with a period in seconds.

        Use the node's clock, including simulated time when enabled, unless clock is supplied.
        """
        if clock is None:
            return self._node.create_timer(period_sec, callback)
        return self._node.create_timer(period_sec, callback, clock=clock)

    def start_tf_listener(self) -> None:
        """Start buffering TF messages for lookups; do nothing if buffering already started."""
        if self._tf_buffer is not None:
            return
        # Imported here so modules that only need topics and services load without tf2_ros.
        # pylint: disable=import-outside-toplevel
        from tf2_ros.buffer import Buffer
        from tf2_ros.transform_listener import TransformListener
        # pylint: enable=import-outside-toplevel
        self._tf_buffer = Buffer()
        TransformListener(self._tf_buffer, self._node)

    def lookup_transform(self, target_frame: str, source_frame: str):
        """Return the latest transform from source_frame into target_frame.

        Start TF buffering if needed. Return the stamped transform without an age check.
        Raises TransformUnavailable when the transform is missing, disconnected or out of range.
        Other errors propagate unchanged.
        """
        # pylint: disable=import-outside-toplevel
        from rclpy.time import Time
        from tf2_ros import ConnectivityException, ExtrapolationException, LookupException
        # pylint: enable=import-outside-toplevel
        self.start_tf_listener()
        try:
            return self._tf_buffer.lookup_transform(target_frame, source_frame, Time())
        except (LookupException, ConnectivityException, ExtrapolationException) as exc:
            raise TransformUnavailable(f'{type(exc).__name__}: {exc}') from exc
