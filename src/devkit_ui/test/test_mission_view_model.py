"""Test MissionApplicationService and MissionViewModel over a real MissionStore."""
# pylint: disable=protected-access
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import test_mission_domain_service  # noqa: F401  # pylint: disable=unused-import
from devkit_ui.application_services.mission_application_service import MissionApplicationService
from devkit_ui.missions import MissionStore
from devkit_ui.view_models.mission_view_model import MissionViewModel


def row_node(name, row_id, role):
    """Return a topology node stand-in with row metadata."""
    return SimpleNamespace(name=name, meta={'row_id': row_id, 'row_role': role})


class TestMissionViewModel(unittest.TestCase):
    def setUp(self) -> None:
        """Build the VM over a SQLite-backed store, a mock executor and a one-row map."""
        tmp = self.enterContext(tempfile.TemporaryDirectory())  # pylint: disable=consider-using-with
        self.store = MissionStore(str(Path(tmp) / 'missions.db'))
        self.addCleanup(self.store.close)
        self.store.attach(Mock())
        self.executor = SimpleNamespace(running=False)
        self.doc = SimpleNamespace(nodes=[row_node('A_1', 1, 'entry'), row_node('A_2', 1, 'exit')])
        self.on_nav_update = Mock()
        self.vm = MissionViewModel(MissionApplicationService(
            self.store, self.executor, SimpleNamespace(get_doc=lambda: self.doc),
            Mock(), Mock()), self.on_nav_update)

    def test_save_queue_stores_entry_node_names_and_shared_action(self) -> None:
        """Verify entry names, fallback names, duplicate rows and shared action settings."""
        queue = [(1, 'drive', {'a': 1}), (2, 'drive', {'a': 1}), (1, 'drive', {'a': 1})]

        mid = self.vm.save_queue(queue, 'my run', 4)

        mission = self.store.find(mid)
        self.assertEqual(mission['rows'], ['A_1', 'ROW_2_IN', 'A_1'])
        self.assertEqual(mission['action'], 'drive')
        self.assertEqual(mission['action_params'], {'a': 1})
        self.assertEqual(mission['name'], 'MY_RUN')
        self.assertEqual(mission['repeat_every_hours'], 4)
        self.assertEqual(self.vm.missions, self.store.missions)

    def test_mixed_queue_reports_error_without_saving(self) -> None:
        """Verify any later step with a different action or parameters prevents persistence."""
        for action, params in (('weed', {'a': 1}), ('drive', {'a': 2}), ('drive', None)):
            with self.subTest(action=action, params=params):
                queue = [
                    (1, 'drive', {'a': 1}), (2, 'drive', {'a': 1}), (3, action, params)]
                version = self.vm.version

                self.assertIsNone(self.vm.save_queue(queue, 'mixed', None))

                self.assertEqual(
                    self.vm.status,
                    'ERROR: all queued steps must have the same action and parameters')
                self.assertEqual(self.vm.version, version)
                self.assertEqual(self.vm.missions, ())

    def test_save_queue_without_a_map_uses_fallback_names(self) -> None:
        """Verify a missing map still saves, using ROW_<id>_IN names."""
        self.doc = None

        mid = self.vm.save_queue([(3, 'drive', None)], '', None)

        self.assertEqual(self.store.find(mid)['rows'], ['ROW_3_IN'])

    def test_equivalent_parameter_dicts_are_accepted_regardless_of_key_order(self) -> None:
        """Compare parameter values rather than object identity or insertion order."""
        queue = [(1, 'drive', {'speed': 0.2, 'offset': 0}),
                 (2, 'drive', {'offset': 0, 'speed': 0.2})]

        mid = self.vm.save_queue(queue, 'same settings', None)

        self.assertIsNotNone(mid)
        self.assertEqual(self.store.find(mid)['rows'], ['A_1', 'ROW_2_IN'])
        self.assertEqual(self.store.find(mid)['action_params'], {'speed': 0.2, 'offset': 0})

    def test_rejected_mixed_queue_preserves_existing_missions(self) -> None:
        """A failed save must leave prior missions and the store version intact."""
        self.vm.save_queue([(1, 'drive', {'speed': 0.2})], 'existing', 4)
        before = self.vm.missions
        version = self.vm.version

        result = self.vm.save_queue(
            [(1, 'drive', {'speed': 0.2}), (2, 'drive', {})], 'rejected', None)

        self.assertIsNone(result)
        self.assertEqual(self.vm.missions, before)
        self.assertEqual(self.vm.version, version)
        self.assertEqual(
            self.vm.status, 'ERROR: all queued steps must have the same action and parameters')

    def test_empty_queue_reports_error_without_saving(self) -> None:
        """Verify an empty queue returns None, reports it in status and leaves the version alone."""
        version = self.vm.version

        self.assertIsNone(self.vm.save_queue([], 'x', None))

        self.assertEqual(self.vm.status, 'ERROR: queue is empty')
        self.assertEqual(self.vm.version, version)
        self.assertEqual(self.vm.missions, ())

    def test_version_bumps_on_change_and_status_is_exposed(self) -> None:
        """Verify add/toggle/delete bump the version and update the status the view polls."""
        version = self.vm.version
        mid = self.vm.save_queue([(1, 'drive', None)], 'a', None)
        self.assertGreater(self.vm.version, version)

        version = self.vm.version
        self.vm.set_active(mid, False)
        self.assertGreater(self.vm.version, version)
        self.assertIs(self.vm.missions[0]['active'], False)

        self.vm.delete(mid)
        self.assertEqual(self.vm.missions, ())

        self.vm.delete('MISSION_99')
        self.assertIn('not found', self.vm.status)

    def test_next_due_and_running_delegate(self) -> None:
        """Verify due time comes from the store and running from the executor service."""
        mid = self.vm.save_queue([(1, 'drive', None)], 'a', 2)
        self.assertEqual(self.vm.next_due_in_hours(mid), 0.0)

        self.assertFalse(self.vm.running)
        self.executor.running = True
        self.assertTrue(self.vm.running)


class TestMissionViewModelRun(unittest.TestCase):
    def setUp(self) -> None:
        """Bind the VM to a mock application service and a navigation-update callback."""
        self.app, self.on_nav_update = Mock(), Mock()
        self.vm = MissionViewModel(self.app, self.on_nav_update)

    def test_run_starts_the_queue_and_routes_navigation_updates_to_the_callback(self) -> None:
        """Verify the injected callback receives the navigation progress of a mission."""
        queue, on_status = [(1, 'drive', None)], Mock()

        self.vm.run(queue, on_status)

        self.app.start.assert_called_once_with(queue, on_status, self.on_nav_update)

    def test_cancel_cancels_through_the_application_service(self) -> None:
        """Verify cancelling reaches the application service once."""
        self.vm.cancel()

        self.app.cancel.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
