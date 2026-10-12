"""QoS profiles shared by the UI services."""
from rclpy.qos import DurabilityPolicy, Duration, LivelinessPolicy, QoSProfile, ReliabilityPolicy

# Safety-relevant state (estop, bumpers). Must match devkit_driver.qos.SAFETY_QOS: reliable, latched so a
# late or restarted subscriber still gets the last state, and a 1 s liveliness lease.
SAFETY_QOS = QoSProfile(
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
    liveliness=LivelinessPolicy.AUTOMATIC,
    liveliness_lease_duration=Duration(seconds=1),
)
