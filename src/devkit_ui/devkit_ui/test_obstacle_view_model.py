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
            'circle', lat=51.45, lon=-2.58, radius_m=1.5, name='gate'
        )

    def test_mark_here_without_gps_reports_error(self) -> None:
        """Verify a missing GPS message sets an error status and adds nothing."""
        self.gps = None

        self.assertIsNone(self.vm.mark_here())

        self.manager.set_status.assert_called_once_with('ERROR: no GPS message yet')
        self.manager.add.assert_not_called()

    def test_add_circle_uses_default_radius(self) -> None:
        """Verify a map click adds a circle with the default radius."""
        self.vm.add_circle(51.0, -1.0)

        self.manager.add.assert_called_once_with('circle', lat=51.0, lon=-1.0, radius_m=0.5)

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

    def test_state_reflects_later_manager_updates(self) -> None:
        """Polling must see new snapshots and statuses after the view model is created."""
        for version, status, snapshot in [
            (0, '', ()),
            (1, 'saved', ({'name': 'A'},)),
            (2, 'ERROR: disk full', ()),
        ]:
            with self.subTest(version=version):
                self.manager.obstacles = snapshot
                self.manager.version = version
                self.manager.status = status
                self.assertIs(self.vm.obstacles, snapshot)
                self.assertEqual((self.vm.version, self.vm.status), (version, status))

    def test_mark_here_reads_latest_fix_on_every_intent(self) -> None:
        """Telemetry may arrive or change after construction."""
        self.gps = None
        self.assertIsNone(self.vm.mark_here())
        self.gps = SimpleNamespace(latitude=-33.0, longitude=18.0)
        self.vm.mark_here()
        self.manager.add.assert_called_once_with(
            'circle', lat=-33.0, lon=18.0, radius_m=0.5, name=''
        )
        self.manager.add.reset_mock()
        self.gps = SimpleNamespace(latitude=52.0, longitude=-3.0)
        self.vm.mark_here('new fix')
        self.manager.add.assert_called_once_with(
            'circle', lat=52.0, lon=-3.0, radius_m=0.5, name='new fix'
        )

    def test_circle_intents_convert_bound_radius_and_return_name(self) -> None:
        """Both entry points accept the numeric text supplied by a bound input."""
        self.vm.default_radius = '2.25'
        self.manager.add.return_value = 'GATE'
        for intent in [self.vm.mark_here, lambda: self.vm.add_circle(51.45, -2.58)]:
            with self.subTest(intent=intent):
                self.manager.add.reset_mock()
                self.assertEqual(intent(), 'GATE')
                self.assertEqual(self.manager.add.call_args.kwargs['radius_m'], 2.25)

    def test_invalid_radius_does_not_reach_manager(self) -> None:
        """Invalid bound values propagate conversion errors without adding an obstacle."""
        for radius, error in [('invalid', ValueError), (None, TypeError)]:
            for intent in [self.vm.mark_here, lambda: self.vm.add_circle(51.0, -1.0)]:
                with self.subTest(radius=radius, intent=intent):
                    self.vm.default_radius = radius
                    with self.assertRaises(error):
                        intent()
        self.manager.add.assert_not_called()

    def test_all_short_polygons_are_rejected(self) -> None:
        """Zero, one and two corners are all below the polygon boundary."""
        for corners in [[], [(51.0, -1.0)], [(51.0, -1.0), (51.1, -1.0)]]:
            with self.subTest(corners=corners):
                self.manager.set_status.reset_mock()
                self.assertIsNone(self.vm.add_polygon(corners))
                self.manager.set_status.assert_called_once_with('ERROR: polygon needs 3+ corners')
        self.manager.add.assert_not_called()

    def test_rejected_intents_preserve_manager_error(self) -> None:
        """Manager rejection must not be converted to success or overwrite its reason."""
        self.manager.add.return_value = None
        self.manager.status = 'ERROR: invalid coordinates'
        for intent in [
            self.vm.mark_here,
            lambda: self.vm.add_circle(0.0, 0.0),
            lambda: self.vm.add_polygon([(0.0, 0.0)] * 3),
        ]:
            with self.subTest(intent=intent):
                self.assertIsNone(intent())
                self.assertEqual(self.vm.status, 'ERROR: invalid coordinates')
        self.manager.set_status.assert_not_called()

    def test_missing_obstacle_delete_returns_false(self) -> None:
        """A failed delete remains a failed user intent."""
        self.manager.delete.return_value = False
        self.assertFalse(self.vm.delete('missing'))
        self.manager.delete.assert_called_once_with('missing')

    def test_status_and_planner_rings_delegate_to_manager(self) -> None:
        """View errors and planner snapshots cross the view model boundary intact."""
        rings = [[(51.0, -1.0), (51.1, -1.0), (51.1, -1.1)]]
        self.manager.rings_ll.return_value = rings
        self.vm.set_status('ERROR: drawing failed')
        self.manager.set_status.assert_called_once_with('ERROR: drawing failed')
        self.assertEqual(self.vm.rings_ll(), rings)
        self.manager.rings_ll.assert_called_once_with()

    def test_radius_is_local_to_each_view_model(self) -> None:
        """Separate views do not share mutable defaults accidentally."""
        other = ObstacleViewModel(self.manager, lambda: self.gps)
        self.vm.default_radius = 3.0
        self.assertEqual(other.default_radius, 0.5)


if __name__ == '__main__':
    unittest.main()
