#!/usr/bin/env python3
"""Run MapAnchorHandler as a node: static map -> odom from a surveyed geodetic origin."""
import contextlib

import rclpy
from rclpy.node import Node
from tf2_ros import StaticTransformBroadcaster

from .modules.map_anchor_handler import MapAnchorHandler


class MapOdomAnchor(Node):
    """Node wrapper; the logic and its parameters are documented on MapAnchorHandler."""

    def __init__(self):
        """Create the node and its handler."""
        super().__init__('map_odom_anchor')
        self._handler = MapAnchorHandler(self, StaticTransformBroadcaster(self))


def main(args=None):  # pragma: no cover
    """Run the anchor node until shutdown."""
    rclpy.init(args=args)
    node = MapOdomAnchor()
    try:
        with contextlib.suppress(KeyboardInterrupt):
            rclpy.spin(node)
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':  # pragma: no cover
    main()
