import math
import unittest
from dataclasses import replace

from devkit_ui.constants import YAW_GOAL_TOLERANCE
from devkit_ui.farm_map_import import FarmDock
from devkit_ui.topo_dock_builder import DockBuildSettings, build_dock_nodes
from devkit_ui.topo_row_builder import RowAnchor

ANCHOR = RowAnchor(lat=51.0, lon=-2.0)
SETTINGS = DockBuildSettings(prefix='FARM', nav_frame='map', map_name='farm',
                             timestamp='01-01-2026_00-00-00', fix_type=2)


def dock_node(heading_deg: float | None, map_yaw_deg: float = 0.0):
    """Build one dock with the given compass heading and return its node."""
    build = build_dock_nodes([FarmDock('DOCK', 51.0, -2.0, heading_deg)], [], ANCHOR,
                             replace(SETTINGS, map_yaw_deg=map_yaw_deg), {})
    return build.nodes['FARM_D_DOCK']


def orientation(node) -> dict:
    return node.to_dict()['node']['pose']['orientation']


class TestDockOrientation(unittest.TestCase):
    def test_compass_headings_become_map_yaw(self) -> None:
        """Verify north is map +y, east is map +x, and west turns half a circle."""
        half = round(math.sqrt(0.5), 6)
        cases = {
            90.0: {'x': 0.0, 'y': 0.0, 'z': 0.0, 'w': 1.0},
            0.0: {'x': 0.0, 'y': 0.0, 'z': half, 'w': half},
            180.0: {'x': 0.0, 'y': 0.0, 'z': -half, 'w': half},
            270.0: {'x': 0.0, 'y': 0.0, 'z': -1.0, 'w': 0.0},  # yaw -pi, the same turn as +pi
        }
        for heading, expected in cases.items():
            with self.subTest(heading=heading):
                self.assertEqual(orientation(dock_node(heading)), expected)

    def test_a_rotated_map_turns_the_goal_to_keep_the_compass_heading(self) -> None:
        """Verify with the map x axis north, a north-facing dock faces map +x and an east one faces -y."""
        half = round(math.sqrt(0.5), 6)

        self.assertEqual(orientation(dock_node(0.0, map_yaw_deg=90.0)),
                         {'x': 0.0, 'y': 0.0, 'z': 0.0, 'w': 1.0})
        self.assertEqual(orientation(dock_node(90.0, map_yaw_deg=90.0)),
                         {'x': 0.0, 'y': 0.0, 'z': -half, 'w': half})

    def test_a_dock_with_a_heading_uses_the_shared_yaw_tolerance(self) -> None:
        self.assertEqual(dock_node(0.0).properties.yaw_goal_tolerance, YAW_GOAL_TOLERANCE)

    def test_a_dock_without_a_heading_accepts_any_yaw(self) -> None:
        """Verify no heading means identity pose and no demanded turn, not a hidden +x requirement."""
        node = dock_node(None)

        self.assertEqual(orientation(node), {'x': 0.0, 'y': 0.0, 'z': 0.0, 'w': 1.0})
        self.assertEqual(node.properties.yaw_goal_tolerance, math.pi)

    def test_the_heading_stays_in_the_metadata(self) -> None:
        self.assertEqual(dock_node(45.0).meta['heading_deg'], 45.0)


if __name__ == '__main__':
    unittest.main()
