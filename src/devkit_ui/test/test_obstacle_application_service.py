"""Verify ObstacleApplicationService turns obstacle use cases into manager calls."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from devkit_ui.application_services.obstacle_application_service import (
    MIN_POLYGON_CORNERS,
    ObstacleApplicationService,
)


class TestObstacleApplicationService(unittest.TestCase):
    def setUp(self) -> None:
        """Bind a service to a mock manager and a telemetry stand-in with a settable fix."""
        self.manager = Mock()
        self.telemetry = SimpleNamespace(
            latest_gps=SimpleNamespace(latitude=51.45, longitude=-2.58))
        self.service = ObstacleApplicationService(self.manager, self.telemetry)

    def test_state_reads_through_to_manager(self) -> None:
        """Verify obstacles, version and status come from the manager."""
        self.manager.obstacles = ({'name': 'A'},)
        self.manager.version = 3
        self.manager.status = 'ok'

        self.assertEqual(self.service.obstacles, ({'name': 'A'},))
        self.assertEqual((self.service.version, self.service.status), (3, 'ok'))

    def test_state_reflects_later_manager_updates(self) -> None:
        """Polling must see new snapshots and statuses after the service is created."""
        for version, status, snapshot in [
            (0, '', ()),
            (1, 'saved', ({'name': 'A'},)),
            (2, 'ERROR: disk full', ()),
        ]:
            with self.subTest(version=version):
                self.manager.obstacles = snapshot
                self.manager.version = version
                self.manager.status = status
                self.assertIs(self.service.obstacles, snapshot)
                self.assertEqual((self.service.version, self.service.status), (version, status))

    def test_mark_here_adds_a_circle_at_the_fix(self) -> None:
        """Verify mark_here adds a circle at the latest fix with the given radius and name."""
        self.manager.add.return_value = 'OBS_1'

        self.assertEqual(self.service.mark_here(1.5, 'gate'), 'OBS_1')

        self.manager.add.assert_called_once_with(
            'circle', lat=51.45, lon=-2.58, radius_m=1.5, name='gate')

    def test_mark_here_without_gps_reports_error(self) -> None:
        """Verify a missing GPS message sets an error status and adds nothing."""
        self.telemetry.latest_gps = None

        self.assertIsNone(self.service.mark_here(0.5))

        self.manager.set_status.assert_called_once_with('ERROR: no GPS message yet')
        self.manager.add.assert_not_called()

    def test_mark_here_reads_the_latest_fix_on_every_call(self) -> None:
        """Telemetry may arrive or change after construction."""
        self.telemetry.latest_gps = None
        self.assertIsNone(self.service.mark_here(0.5))
        self.telemetry.latest_gps = SimpleNamespace(latitude=-33.0, longitude=18.0)
        self.service.mark_here(0.5)
        self.manager.add.assert_called_once_with(
            'circle', lat=-33.0, lon=18.0, radius_m=0.5, name='')
        self.manager.add.reset_mock()
        self.telemetry.latest_gps = SimpleNamespace(latitude=52.0, longitude=-3.0)
        self.service.mark_here(0.5, 'new fix')
        self.manager.add.assert_called_once_with(
            'circle', lat=52.0, lon=-3.0, radius_m=0.5, name='new fix')

    def test_add_circle_passes_coordinates_and_radius(self) -> None:
        """Verify a map click adds a circle with the given radius and returns its name."""
        self.manager.add.return_value = 'OBS_2'

        self.assertEqual(self.service.add_circle(51.0, -1.0, 2.25), 'OBS_2')

        self.manager.add.assert_called_once_with('circle', lat=51.0, lon=-1.0, radius_m=2.25)

    def test_add_polygon_delegates_valid_polygon(self) -> None:
        """Verify a valid polygon is added and its name returned."""
        corners = [(51.0, -1.0), (51.1, -1.0), (51.1, -1.1)]
        self.manager.add.return_value = 'OBS_3'

        self.assertEqual(self.service.add_polygon(corners), 'OBS_3')

        self.manager.add.assert_called_once_with('polygon', points_ll=corners)

    def test_short_polygons_are_rejected_without_reaching_the_manager(self) -> None:
        """Zero, one and two corners are all below the polygon boundary."""
        self.assertEqual(MIN_POLYGON_CORNERS, 3)
        for corners in [[], [(51.0, -1.0)], [(51.0, -1.0), (51.1, -1.0)]]:
            with self.subTest(corners=corners):
                self.manager.set_status.reset_mock()
                self.assertIsNone(self.service.add_polygon(corners))
                self.manager.set_status.assert_called_once_with('ERROR: polygon needs 3+ corners')
        self.manager.add.assert_not_called()

    def test_rejected_adds_preserve_the_manager_error(self) -> None:
        """Manager rejection must not be converted to success or overwrite its reason."""
        self.manager.add.return_value = None
        self.manager.status = 'ERROR: invalid coordinates'
        for intent in [
            lambda: self.service.mark_here(0.5),
            lambda: self.service.add_circle(0.0, 0.0, 0.5),
            lambda: self.service.add_polygon([(0.0, 0.0)] * 3),
        ]:
            with self.subTest(intent=intent):
                self.assertIsNone(intent())
                self.assertEqual(self.service.status, 'ERROR: invalid coordinates')
        self.manager.set_status.assert_not_called()

    def test_manager_errors_propagate(self) -> None:
        """Errors from the manager are not swallowed by the service."""
        self.manager.add.side_effect = ValueError('bad corner')
        with self.assertRaisesRegex(ValueError, 'bad corner'):
            self.service.add_polygon([(0.0, 0.0), ('x', 1.0), (2.0, 2.0)])

    def test_delete_returns_the_manager_result(self) -> None:
        """A failed delete remains a failed user intent."""
        for existed in (True, False):
            with self.subTest(existed=existed):
                self.manager.delete.reset_mock()
                self.manager.delete.return_value = existed
                self.assertIs(self.service.delete('OBS_1'), existed)
                self.manager.delete.assert_called_once_with('OBS_1')

    def test_status_and_planner_rings_delegate_to_manager(self) -> None:
        """View errors and planner snapshots cross the service boundary intact."""
        rings = [[(51.0, -1.0), (51.1, -1.0), (51.1, -1.1)]]
        self.manager.rings_ll.return_value = rings

        self.service.set_status('ERROR: drawing failed')

        self.manager.set_status.assert_called_once_with('ERROR: drawing failed')
        self.assertEqual(self.service.rings_ll(), rings)
        self.manager.rings_ll.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
