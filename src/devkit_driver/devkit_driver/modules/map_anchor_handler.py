import math
import time
from collections.abc import Callable

from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import NavSatFix, NavSatStatus

from ..map_anchor import AnchorEstimator

_WAIT_WARNING_PERIOD_S = 10.0


class MapAnchorHandler:
    """Publish a static map -> odom transform anchored to a surveyed geodetic origin.

    Waits for fixes on fix_topic and odom positions on odom_topic, then broadcasts map -> odom
    once the estimates agree (see AnchorEstimator). Until then no map -> odom exists, so Nav2
    waits instead of planning from a wrong pose.

    Parameters: origin_lat, origin_lon (required, degrees), map_yaw_deg (default 0, map x axis
    counter-clockwise from east), fix_topic (default fc/ekf_fix), odom_topic (default fc/odom),
    samples, max_skew_s, max_spread_m, map_frame, odom_frame.
    """

    def __init__(self, node: Node, broadcaster, clock: Callable[[], float] = time.monotonic):
        """Read parameters and subscribe; raise ValueError if the origin is not set."""
        self._node = node
        self._broadcaster = broadcaster
        self._clock = clock
        for name, default in (('origin_lat', math.nan), ('origin_lon', math.nan), ('map_yaw_deg', 0.0),
                              ('fix_topic', 'fc/ekf_fix'), ('odom_topic', 'fc/odom'), ('samples', 10),
                              ('max_skew_s', 0.5), ('max_spread_m', 0.5),
                              ('map_frame', 'map'), ('odom_frame', 'odom')):
            node.declare_parameter(name, default)

        def parameter(name):
            return node.get_parameter(name).value

        origin_lat, origin_lon = float(parameter('origin_lat')), float(parameter('origin_lon'))
        if not (math.isfinite(origin_lat) and math.isfinite(origin_lon)):
            raise ValueError('origin_lat and origin_lon must be set')
        self._estimator = AnchorEstimator(
            origin_lat, origin_lon, math.radians(float(parameter('map_yaw_deg'))),
            int(parameter('samples')), float(parameter('max_skew_s')), float(parameter('max_spread_m')))
        self._map_frame = str(parameter('map_frame'))
        self._odom_frame = str(parameter('odom_frame'))
        self.anchored = False
        self._fix_topic = str(parameter('fix_topic'))
        self._odom_topic = str(parameter('odom_topic'))
        self._fixes = 0
        self._odoms = 0
        # Best effort matches both reliable and best-effort publishers.
        qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)
        node.create_subscription(NavSatFix, self._fix_topic, self.handle_fix, qos)
        node.create_subscription(Odometry, self._odom_topic, self.handle_odom, qos)
        node.create_timer(_WAIT_WARNING_PERIOD_S, self._warn_if_waiting)

    def handle_fix(self, msg: NavSatFix) -> None:
        """Feed a valid fix to the estimator; ignore no-fix status and nonfinite positions."""
        if self.anchored or msg.status.status < NavSatStatus.STATUS_FIX:
            return
        if math.isfinite(msg.latitude) and math.isfinite(msg.longitude):
            self._fixes += 1
            self._publish(self._estimator.add_fix(self._clock(), msg.latitude, msg.longitude))

    def handle_odom(self, msg: Odometry) -> None:
        """Feed an odom position to the estimator; ignore nonfinite positions."""
        if self.anchored:
            return
        position = msg.pose.pose.position
        if math.isfinite(position.x) and math.isfinite(position.y):
            self._odoms += 1
            self._publish(self._estimator.add_odom(self._clock(), position.x, position.y))

    def _warn_if_waiting(self) -> None:
        """Name the missing input every period while no fix or no odom position has arrived."""
        if self.anchored:
            return
        missing = [f'{kind} on {topic}' for kind, topic, count in
                   (('a valid fix', self._fix_topic, self._fixes), ('odometry', self._odom_topic, self._odoms))
                   if count == 0]
        if missing:
            self._node.get_logger().warn(f'map -> odom is waiting for {" and ".join(missing)}: nothing has arrived')

    def _publish(self, result: tuple[float, float, float] | None) -> None:
        """Broadcast map -> odom once, or warn if the window is full but disagrees."""
        log = self._node.get_logger()
        if result is None:
            if self._estimator.spread_m > 0.0:
                log.warn(f'map -> odom not anchored: recent estimates spread {self._estimator.spread_m:.2f} m',
                         throttle_duration_sec=10.0)
            return
        tx, ty, yaw = result
        transform = TransformStamped()
        transform.header.stamp = self._node.get_clock().now().to_msg()
        transform.header.frame_id = self._map_frame
        transform.child_frame_id = self._odom_frame
        transform.transform.translation.x = tx
        transform.transform.translation.y = ty
        transform.transform.rotation.z = math.sin(yaw / 2.0)
        transform.transform.rotation.w = math.cos(yaw / 2.0)
        self._broadcaster.sendTransform(transform)
        self.anchored = True
        log.info(f'map -> odom anchored: x={tx:.3f} m, y={ty:.3f} m, yaw={math.degrees(yaw):.1f} deg '
                 f'(spread {self._estimator.spread_m:.3f} m)')
