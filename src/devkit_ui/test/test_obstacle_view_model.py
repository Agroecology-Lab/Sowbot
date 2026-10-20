"""Verify ObstacleViewModel owns the radius default and passes user intents to the service."""

import unittest
from unittest.mock import Mock

from devkit_ui.view_models.obstacle_view_model import ObstacleViewModel


class TestObstacleViewModel(unittest.TestCase):
    def setUp(self) -> None:
        """Bind a view model to a mock service."""
        self.service = Mock()
        self.vm = ObstacleViewModel(self.service)

    def test_state_reads_through_to_service(self) -> None:
        """Verify obstacles, version and status come from the service."""
        self.service.obstacles = ({'name': 'A'},)
        self.service.version = 3
        self.service.status = 'ok'

        self.assertEqual(self.vm.obstacles, ({'name': 'A'},))
        self.assertEqual((self.vm.version, self.vm.status), (3, 'ok'))

    def test_state_reflects_later_service_updates(self) -> None:
        """Polling must see new snapshots and statuses after the view model is created."""
        for version, status, snapshot in [
            (0, '', ()),
            (1, 'saved', ({'name': 'A'},)),
            (2, 'ERROR: disk full', ()),
        ]:
            with self.subTest(version=version):
                self.service.obstacles = snapshot
                self.service.version = version
                self.service.status = status
                self.assertIs(self.vm.obstacles, snapshot)
                self.assertEqual((self.vm.version, self.vm.status), (version, status))

    def test_mark_here_passes_default_radius_and_name(self) -> None:
        """Verify mark_here uses the current default radius."""
        self.vm.default_radius = 1.5
        self.service.mark_here.return_value = 'OBS_1'

        self.assertEqual(self.vm.mark_here('gate'), 'OBS_1')

        self.service.mark_here.assert_called_once_with(1.5, 'gate')

    def test_add_circle_uses_default_radius(self) -> None:
        """Verify a map click adds a circle with the default radius."""
        self.service.add_circle.return_value = 'OBS_2'

        self.assertEqual(self.vm.add_circle(51.0, -1.0), 'OBS_2')

        self.service.add_circle.assert_called_once_with(51.0, -1.0, 0.5)

    def test_circle_intents_convert_bound_radius(self) -> None:
        """Both entry points accept the numeric text supplied by a bound input."""
        self.vm.default_radius = '2.25'

        self.vm.mark_here()
        self.vm.add_circle(51.45, -2.58)

        self.service.mark_here.assert_called_once_with(2.25, '')
        self.service.add_circle.assert_called_once_with(51.45, -2.58, 2.25)

    def test_invalid_radius_does_not_reach_service(self) -> None:
        """Invalid bound values propagate conversion errors without adding an obstacle."""
        for radius, error in [('invalid', ValueError), (None, TypeError)]:
            for intent in [self.vm.mark_here, lambda: self.vm.add_circle(51.0, -1.0)]:
                with self.subTest(radius=radius, intent=intent):
                    self.vm.default_radius = radius
                    with self.assertRaises(error):
                        intent()
        self.service.mark_here.assert_not_called()
        self.service.add_circle.assert_not_called()

    def test_add_polygon_delegates(self) -> None:
        """Verify corners are passed to the service and its result returned."""
        corners = [(51.0, -1.0), (51.1, -1.0), (51.1, -1.1)]
        self.service.add_polygon.return_value = 'OBS_3'

        self.assertEqual(self.vm.add_polygon(corners), 'OBS_3')

        self.service.add_polygon.assert_called_once_with(corners)

    def test_rejected_intents_return_none(self) -> None:
        """A service rejection reaches the view as None, with the reason left in status."""
        self.service.mark_here.return_value = None
        self.service.add_circle.return_value = None
        self.service.add_polygon.return_value = None
        self.service.status = 'ERROR: invalid coordinates'
        for intent in [
            self.vm.mark_here,
            lambda: self.vm.add_circle(0.0, 0.0),
            lambda: self.vm.add_polygon([(0.0, 0.0)] * 3),
        ]:
            with self.subTest(intent=intent):
                self.assertIsNone(intent())
                self.assertEqual(self.vm.status, 'ERROR: invalid coordinates')
        self.service.set_status.assert_not_called()

    def test_delete_returns_the_service_result(self) -> None:
        """A failed delete remains a failed user intent."""
        for existed in (True, False):
            with self.subTest(existed=existed):
                self.service.delete.reset_mock()
                self.service.delete.return_value = existed
                self.assertIs(self.vm.delete('OBS_1'), existed)
                self.service.delete.assert_called_once_with('OBS_1')

    def test_status_and_planner_rings_delegate_to_service(self) -> None:
        """View errors and planner snapshots cross the view model boundary intact."""
        rings = [[(51.0, -1.0), (51.1, -1.0), (51.1, -1.1)]]
        self.service.rings_ll.return_value = rings
        self.vm.set_status('ERROR: drawing failed')
        self.service.set_status.assert_called_once_with('ERROR: drawing failed')
        self.assertEqual(self.vm.rings_ll(), rings)
        self.service.rings_ll.assert_called_once_with()

    def test_radius_is_local_to_each_view_model(self) -> None:
        """Separate views do not share mutable defaults accidentally."""
        other = ObstacleViewModel(self.service)
        self.vm.default_radius = 3.0
        self.assertEqual(other.default_radius, 0.5)


if __name__ == '__main__':
    unittest.main()
