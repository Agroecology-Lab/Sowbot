#!/usr/bin/env python3
"""Publish a static map -> odom transform anchored to a surveyed geodetic origin.

Waits for fixes on fix_topic and odom positions on odom_topic, then publishes map -> odom
once the estimates agree (see map_anchor.AnchorEstimator). Until then no map -> odom
exists, so Nav2 waits instead of planning from a wrong pose.

Parameters: origin_lat, origin_lon (required, degrees), map_yaw_deg (default 0, map x axis
counter-clockwise from east), fix_topic (default fc/ekf_fix), odom_topic (default fc/odom),
samples, max_skew_s, max_spread_m, map_frame, odom_frame.
"""
import math
import time

import rclpy
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import NavSatFix, NavSatStatus
from tf2_ros import StaticTransformBroadcaster

from .map_anchor import AnchorEstimator


class MapOdomAnchor(Node):
    """Estimate map -> odom from a surveyed origin and publish it once."""

    def __init__(self):
        """Read parameters, subscribe to the fix and odom topics."""
        super().__init__('map_odom_anchor')
        self.declare_parameter('origin_lat', math.nan)
        self.declare_parameter('origin_lon', math.nan)
        self.declare_parameter('map_yaw_deg', 0.0)
        self.declare_parameter('fix_topic', 'fc/ekf_fix')
        self.declare_parameter('odom_topic', 'fc/odom')
        self.declare_parameter('samples', 10)
        self.declare_parameter('max_skew_s', 0.5)
        self.declare_parameter('max_spread_m', 0.5)
        self.declare_parameter('map_frame', 'map')
        self.declare_parameter('odom_frame', 'odom')
        origin_lat = float(self.get_parameter('origin_lat').value)
        origin_lon = float(self.get_parameter('origin_lon').value)
        if not (math.isfinite(origin_lat) and math.isfinite(origin_lon)):
            raise ValueError('origin_lat and origin_lon must be set')
        self._estimator = AnchorEstimator(
            origin_lat, origin_lon, math.radians(float(self.get_parameter('map_yaw_deg').value)),
            int(self.get_parameter('samples').value), float(self.get_parameter('max_skew_s').value),
            float(self.get_parameter('max_spread_m').value))
        self._map_frame = str(self.get_parameter('map_frame').value)
        self._odom_frame = str(self.get_parameter('odom_frame').value)
        self._broadcaster = StaticTransformBroadcaster(self)
        self._done = False
        # Best effort matches both reliable and best-effort publishers.
        qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.create_subscription(NavSatFix, str(self.get_parameter('fix_topic').value), self._on_fix, qos)
        self.create_subscription(Odometry, str(self.get_parameter('odom_topic').value), self._on_odom, qos)

    def _on_fix(self, msg: NavSatFix) -> None:
        """Feed a valid fix to the estimator."""
        if self._done or msg.status.status < NavSatStatus.STATUS_FIX:
            return
        if math.isfinite(msg.latitude) and math.isfinite(msg.longitude):
            self._publish(self._estimator.add_fix(time.monotonic(), msg.latitude, msg.longitude))

    def _on_odom(self, msg: Odometry) -> None:
        """Feed an odom position to the estimator."""
        if self._done:
            return
        position = msg.pose.pose.position
        if math.isfinite(position.x) and math.isfinite(position.y):
            self._publish(self._estimator.add_odom(time.monotonic(), position.x, position.y))

    def _publish(self, result: tuple[float, float, float] | None) -> None:
        """Broadcast map -> odom once, or warn if the window is full but disagrees."""
        if result is None:
            if self._estimator.spread_m > 0.0:
                self.get_logger().warn(
                    f'map -> odom not anchored: recent estimates spread {self._estimator.spread_m:.2f} m',
                    throttle_duration_sec=10.0)
            return
        tx, ty, yaw = result
        transform = TransformStamped()
        transform.header.stamp = self.get_clock().now().to_msg()
        transform.header.frame_id = self._map_frame
        transform.child_frame_id = self._odom_frame
        transform.transform.translation.x = tx
        transform.transform.translation.y = ty
        transform.transform.rotation.z = math.sin(yaw / 2.0)
        transform.transform.rotation.w = math.cos(yaw / 2.0)
        self._broadcaster.sendTransform(transform)
        self._done = True
        self.get_logger().info(
            f'map -> odom anchored: x={tx:.3f} m, y={ty:.3f} m, yaw={math.degrees(yaw):.1f} deg '
            f'(spread {self._estimator.spread_m:.3f} m)')


def main(args=None):
    """Run the anchor node until shutdown."""
    rclpy.init(args=args)
    node = MapOdomAnchor()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
