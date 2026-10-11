"""Verify ObstacleViewModel passes user intents to the manager and reads its state."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from devkit_ui.view_models.obstacle_view_model import ObstacleViewModel


class TestObstacleViewModel(unittest.TestCase):
    def setUp(self) -> None:
        """Bind a view model to a mock manager and a settable GPS fix."""
        self.manager = Mock()
        self.gps = SimpleNamespace(latitude=51.45, longitude=-2.58)
        self.vm = ObstacleViewModel(self.manager, lambda: self.gps)

    def test_state_reads_through_to_manager(self) -> None:
        """Verify obstacles, version and status come from the manager."""
        self.manager.obstacles = ({'name': 'A'},)
        self.manager.version = 3
        self.manager.status = 'ok'

        self.assertEqual(self.vm.obstacles, ({'name': 'A'},))
        self.assertEqual((self.vm.version, self.vm.status), (3, 'ok'))

    def test_mark_here_uses_gps_and_default_radius(self) -> None:
        """Verify mark_here adds a circle at the fix with the current default radius."""
        self.vm.default_radius = 1.5
        self.manager.add.return_value = 'OBS_1'

        self.assertEqual(self.vm.mark_here('gate'), 'OBS_1')

        self.manager.add.assert_called_once_with(
            'circle', lat=51.45, lon=-2.58, radius_m=1.5, name='gate')

    def test_mark_here_without_gps_reports_error(self) -> None:
        """Verify a missing GPS message sets an error status and adds nothing."""
        self.gps = None

        self.assertIsNone(self.vm.mark_here())

        self.manager.set_status.assert_called_once_with('ERROR: no GPS message yet')
        self.manager.add.assert_not_called()

    def test_add_circle_uses_default_radius(self) -> None:
        """Verify a map click adds a circle with the default radius."""
        self.vm.add_circle(51.0, -1.0)

        self.manager.add.assert_called_once_with(
            'circle', lat=51.0, lon=-1.0, radius_m=0.5)

    def test_add_polygon_requires_three_corners(self) -> None:
        """Verify short polygons are rejected with a status and not sent to the manager."""
        self.assertIsNone(self.vm.add_polygon([(51.0, -1.0), (51.1, -1.0)]))

        self.manager.set_status.assert_called_once_with('ERROR: polygon needs 3+ corners')
        self.manager.add.assert_not_called()

    def test_add_polygon_delegates_valid_polygon(self) -> None:
        """Verify a valid polygon is added and its name returned."""
        corners = [(51.0, -1.0), (51.1, -1.0), (51.1, -1.1)]
        self.manager.add.return_value = 'OBS_2'

        self.assertEqual(self.vm.add_polygon(corners), 'OBS_2')

        self.manager.add.assert_called_once_with('polygon', points_ll=corners)

    def test_delete_delegates(self) -> None:
        """Verify delete returns the manager's result."""
        self.manager.delete.return_value = True

        self.assertTrue(self.vm.delete('OBS_1'))

        self.manager.delete.assert_called_once_with('OBS_1')


if __name__ == '__main__':
    unittest.main()
