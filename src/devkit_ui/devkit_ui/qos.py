"""QoS profiles shared by the UI services, described without importing rclpy.

RosGateway turns a QosSpec into an rclpy QoSProfile when it creates a publisher or subscription.
"""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class QosSpec:
    """Transport settings for one topic. Field values are the lower-case rclpy policy names."""

    depth: int
    reliability: Literal['reliable', 'best_effort']
    durability: Literal['volatile', 'transient_local'] | None = None
    history: Literal['keep_last', 'keep_all'] | None = None
    liveliness: Literal['automatic', 'manual_by_topic'] | None = None
    liveliness_lease_sec: float | None = None


# Safety-relevant state (estop, bumpers). Must match devkit_driver.qos.SAFETY_QOS: reliable, latched so a
# late or restarted subscriber still gets the last state, and a 1 s liveliness lease.
SAFETY_QOS = QosSpec(
    depth=1,
    reliability='reliable',
    durability='transient_local',
    liveliness='automatic',
    liveliness_lease_sec=1.0,
)

# Latest-value sensor streams: a dropped sample is replaced by the next one.
SENSOR_QOS = QosSpec(depth=1, reliability='best_effort')

# Odometry: reliable with a short queue.
ODOM_QOS = QosSpec(depth=10, reliability='reliable')

# Latched, reliable: a late subscriber still receives the last topology map or obstacle set.
LATCHED_QOS = QosSpec(
    depth=1,
    reliability='reliable',
    durability='transient_local',
    history='keep_last',
)
