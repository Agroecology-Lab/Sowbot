"""Verify ObstacleManager reaches ROS only through the injected RosGateway."""

# pylint: disable=protected-access
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


class FakePolygon:
    """Stand in for geometry_msgs/PolygonStamped."""

    def __init__(self) -> None:
        self.header = SimpleNamespace(stamp=None, frame_id='')
        self.polygon = SimpleNamespace(points=[])


_geometry = ModuleType('geometry_msgs.msg')
_geometry.Point32 = lambda: SimpleNamespace(x=0.0, y=0.0, z=0.0)
_geometry.PolygonStamped = FakePolygon
_qos = ModuleType('rclpy.qos')
_qos.QoSProfile = SimpleNamespace
_qos.DurabilityPolicy = SimpleNamespace(TRANSIENT_LOCAL='transient_local')
_qos.HistoryPolicy = SimpleNamespace(KEEP_LAST='keep_last')
_qos.ReliabilityPolicy = SimpleNamespace(RELIABLE='reliable')

with patch.dict(
    sys.modules,
    {
        'geometry_msgs': ModuleType('geometry_msgs'),
        'geometry_msgs.msg': _geometry,
        'rclpy': ModuleType('rclpy'),
        'rclpy.qos': _qos,
    },
):
    from devkit_ui.obstacles import ObstacleManager

# Use the globals the class actually runs with: another test may have imported its own copy of
# devkit_ui.obstacles under different stubs, so the package attribute is not reliable.
obstacles = SimpleNamespace(**ObstacleManager.start.__globals__)


class TestObstacleManagerGateway(unittest.TestCase):
    def setUp(self) -> None:
        """Create a manager on a mock gateway with settable odometry and GPS getters."""
        self.ros = Mock()
        self.odom = None
        self.gps = None
        tmp = self.enterContext(tempfile.TemporaryDirectory())  # pylint: disable=consider-using-with
        self.mgr = ObstacleManager(
            self.ros, lambda: self.odom, lambda: self.gps, path=str(Path(tmp) / 'obstacles.yaml')
        )

    def test_start_creates_publisher_and_timer_on_gateway(self) -> None:
        """Verify start() creates the /obstacles publisher and 0.5 s cold-start timer via the gateway."""
        with patch.object(obstacles.threading, 'Thread'):
            self.mgr.start()

        self.ros.create_publisher.assert_called_once_with(
            obstacles.PolygonStamped, '/obstacles', obstacles._OBS_QOS
        )
        self.ros.create_timer.assert_called_once_with(0.5, self.mgr._cold_start_publish)
        self.assertIs(self.mgr._publisher, self.ros.create_publisher.return_value)
        self.assertEqual(self.mgr.obstacles, ())
        self.assertEqual((self.mgr.version, self.mgr.status), (0, ''))

    def test_publish_failure_is_logged_through_gateway(self) -> None:
        """Verify a publish error is logged through the gateway."""
        with patch.object(obstacles.threading, 'Thread'):
            self.mgr.start()
        self.mgr._publisher.publish.side_effect = RuntimeError('boom')
        self.odom = SimpleNamespace(
            pose=SimpleNamespace(pose=SimpleNamespace(position=SimpleNamespace(x=0.0, y=0.0)))
        )
        self.gps = SimpleNamespace(latitude=51.0, longitude=-1.0)
        self.mgr._obstacles = (
            {'name': 'A', 'type': 'circle', 'center': {'lat': 51.0, 'lon': -1.0}, 'radius_m': 0.5},
        )

        self.mgr._publish()

        self.ros.get_clock.assert_called()
        self.ros.get_logger.return_value.warn.assert_called()

    def test_construction_defers_loading_and_ros_resources(self) -> None:
        """Construction exposes empty state without starting threads or touching ROS."""
        with patch.object(obstacles.threading, 'Thread') as thread:
            manager = ObstacleManager(self.ros, Mock(), Mock(), self.mgr._path)
        self.assertEqual((manager.obstacles, manager.version, manager.status), ((), 0, ''))
        thread.assert_not_called()
        self.assertEqual(self.ros.mock_calls, [])

    def test_start_schedules_background_load(self) -> None:
        """The saved snapshot is loaded by the daemon worker started by start()."""
        with patch.object(obstacles.threading, 'Thread') as thread:
            self.mgr.start()
        thread.assert_called_once_with(target=self.mgr._load, daemon=True)
        thread.return_value.start.assert_called_once_with()

    def test_add_and_delete_expose_new_snapshots_and_versions(self) -> None:
        """Polling readers see mutations immediately, before the save worker runs."""
        with patch.object(obstacles.threading, 'Thread') as thread:
            self.assertEqual(self.mgr.add('circle', lat=51.0, lon=-1.0, name='gate'), 'GATE')
            snapshot = self.mgr.obstacles
            self.assertEqual([o['name'] for o in snapshot], ['GATE'])
            self.assertEqual(self.mgr.version, 1)
            self.assertIn('writing', self.mgr.status)
            self.assertTrue(self.mgr.delete('GATE'))
        self.assertEqual(self.mgr.obstacles, ())
        self.assertEqual(self.mgr.version, 2)
        self.assertIn('deleting GATE', self.mgr.status)
        self.assertEqual([o['name'] for o in snapshot], ['GATE'])
        self.assertEqual(thread.return_value.start.call_count, 2)

    def test_rejected_mutations_only_change_status(self) -> None:
        """Validation and missing-name errors never bump the version or save state."""
        with patch.object(obstacles.threading, 'Thread') as thread:
            self.mgr.add('circle', lat=51.0, lon=-1.0, name='GATE')
            snapshot = self.mgr.obstacles
            thread.reset_mock()
            for mutation, error in [
                (lambda: self.mgr.add('circle', lat=0.0, lon=0.0), '0,0'),
                (lambda: self.mgr.add('circle', lat=51.0, lon=-1.0, name='gate'), 'already exists'),
                (lambda: self.mgr.delete('MISSING'), 'not found'),
            ]:
                with self.subTest(error=error):
                    self.assertFalse(mutation())
                    self.assertIs(self.mgr.obstacles, snapshot)
                    self.assertEqual(self.mgr.version, 1)
                    self.assertTrue(self.mgr.status.startswith('ERROR:'))
                    self.assertIn(error, self.mgr.status)
            thread.assert_not_called()

    def test_status_updates_do_not_change_snapshot_version(self) -> None:
        """Status polling is independent of obstacle list refreshes."""
        for status in ['writing', 'ERROR: disk full', '']:
            self.mgr.set_status(status)
            self.assertEqual(self.mgr.status, status)
            self.assertEqual((self.mgr.obstacles, self.mgr.version), ((), 0))

    def test_load_exposes_saved_snapshot_without_attached_node(self) -> None:
        """Loading updates the manager properties and logs through the gateway."""
        saved = [
            {
                'name': 'GATE',
                'type': 'circle',
                'center': {'lat': 51.0, 'lon': -1.0},
                'radius_m': 0.5,
            }
        ]
        Path(self.mgr._path).write_text(
            obstacles.yaml.safe_dump({'obstacles': saved}), encoding='utf-8'
        )
        with patch.object(self.mgr, '_publish') as publish:
            self.mgr._load()
        self.assertEqual(self.mgr.obstacles, tuple(saved))
        self.assertEqual(self.mgr.version, 1)
        publish.assert_called_once_with()
        self.ros.get_logger.return_value.info.assert_called_once()

    def test_missing_or_malformed_file_preserves_state(self) -> None:
        """Failed loads keep the current snapshot and report malformed YAML."""
        with patch.object(obstacles.threading, 'Thread'):
            self.mgr.add('circle', lat=51.0, lon=-1.0)
        snapshot = self.mgr.obstacles
        with patch.object(self.mgr, '_publish') as publish:
            self.mgr._load()
            self.ros.get_logger.assert_not_called()
            Path(self.mgr._path).write_text('obstacles: [', encoding='utf-8')
            self.mgr._load()
        self.assertIs(self.mgr.obstacles, snapshot)
        self.assertEqual(self.mgr.version, 1)
        publish.assert_not_called()
        self.ros.get_logger.return_value.warn.assert_called_once()

    def test_persistence_reports_success_and_writes_current_snapshot(self) -> None:
        """The new manager-owned status reports the completed save."""
        with patch.object(obstacles.threading, 'Thread'):
            self.mgr.add('circle', lat=51.0, lon=-1.0)
        with patch.object(self.mgr, '_publish') as publish:
            self.mgr._persist_and_publish('saved')
        saved = obstacles.yaml.safe_load(Path(self.mgr._path).read_text(encoding='utf-8'))
        self.assertEqual(saved, {'obstacles': list(self.mgr.obstacles)})
        self.assertEqual((self.mgr.version, self.mgr.status), (1, 'saved'))
        publish.assert_called_once_with()
        self.ros.get_logger.return_value.info.assert_called_once_with('Obstacles saved: 1 total')

    def test_persistence_failure_exposes_error_without_undoing_addition(self) -> None:
        """A save failure is visible to the view model while memory keeps the new obstacle."""
        with patch.object(obstacles.threading, 'Thread'):
            self.mgr.add('circle', lat=51.0, lon=-1.0)
        snapshot = self.mgr.obstacles
        with (
            patch.object(obstacles.os, 'replace', side_effect=OSError('disk full')),
            patch.object(self.mgr, '_publish') as publish,
        ):
            self.mgr._persist_and_publish('saved')
        self.assertEqual(self.mgr.status, 'ERROR: disk full')
        self.assertIs(self.mgr.obstacles, snapshot)
        self.assertEqual(self.mgr.version, 1)
        publish.assert_not_called()
        self.ros.get_logger.return_value.error.assert_called_once_with('persist failed: disk full')

    def test_anchor_uses_latest_getter_values_and_rejects_invalid_fixes(self) -> None:
        """Missing and nonfinite fixes cannot become metric projection anchors."""
        self.assertIsNone(self.mgr._current_anchor())
        self.gps = SimpleNamespace(latitude=51.0, longitude=-1.0)
        self.assertIsNone(self.mgr._current_anchor())
        self.odom = SimpleNamespace(
            pose=SimpleNamespace(pose=SimpleNamespace(position=SimpleNamespace(x=12.0, y=-4.0)))
        )
        self.gps = None
        self.assertIsNone(self.mgr._current_anchor())
        for lat, lon in [(float('nan'), -1.0), (51.0, float('inf')), (0.0, 0.0), (1e-10, -1e-10)]:
            with self.subTest(lat=lat, lon=lon):
                self.gps = SimpleNamespace(latitude=lat, longitude=lon)
                self.assertIsNone(self.mgr._current_anchor())
        for lat, lon in [(51.0, -1.0), (0.0, -1.0), (51.0, 0.0), (1e-9, 0.0)]:
            with self.subTest(lat=lat, lon=lon):
                self.gps = SimpleNamespace(latitude=lat, longitude=lon)
                self.assertEqual(self.mgr._current_anchor(), (12.0, -4.0, lat, lon))

    def test_cold_start_waits_for_getters_then_publishes_in_map_frame(self) -> None:
        """Late telemetry triggers publication once, using the current odometry offset."""
        with patch.object(obstacles.threading, 'Thread'):
            self.mgr.start()
            self.mgr.add('polygon', points_ll=[(51.0, -1.0), (51.001, -1.0), (51.0, -0.999)])
        timer = self.ros.create_timer.return_value
        publisher = self.ros.create_publisher.return_value
        self.mgr._cold_start_publish()
        publisher.publish.assert_not_called()
        timer.cancel.assert_not_called()

        self.odom = SimpleNamespace(
            pose=SimpleNamespace(pose=SimpleNamespace(position=SimpleNamespace(x=12.0, y=-4.0)))
        )
        self.gps = SimpleNamespace(latitude=51.0, longitude=-1.0)
        self.mgr._cold_start_publish()
        publisher.publish.assert_called_once()
        message = publisher.publish.call_args.args[0]
        self.assertEqual(message.header.frame_id, 'map')
        self.assertIs(
            message.header.stamp,
            self.ros.get_clock.return_value.now.return_value.to_msg.return_value,
        )
        self.assertEqual(len(message.polygon.points), 3)
        first, north, east = message.polygon.points
        self.assertEqual((first.x, first.y, first.z), (12.0, -4.0, 0.0))
        self.assertAlmostEqual(north.x, first.x)
        self.assertGreater(north.y, first.y)
        self.assertGreater(east.x, first.x)
        self.assertAlmostEqual(east.y, first.y)
        timer.cancel.assert_called_once_with()
        self.mgr._cold_start_publish()
        publisher.publish.assert_called_once()


if __name__ == '__main__':
    unittest.main()
