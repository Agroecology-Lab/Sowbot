"""Tests for the map save and archive use-cases on TopologyViewModel."""
import unittest
from unittest.mock import Mock

from devkit_ui.models import TopoDoc
from devkit_ui.view_models.topology_view_model import TopologyViewModel


class TestMapArchive(unittest.TestCase):
    def setUp(self):
        """Build the view model on a mock application service with a map loaded."""
        self.app = Mock()
        self.app.archive_and_clear.return_value = 'live_1'
        self.vm = TopologyViewModel(self.app)
        self.vm.update_doc(TopoDoc(name='live'))
        self.archived = Mock()
        self.vm.register_map_archived_callback(self.archived)

    def test_map_name_is_the_live_map_or_a_placeholder(self):
        """Verify the name shown in the confirmation follows the loaded map."""
        self.assertEqual(self.vm.map_name, 'live')
        self.vm.update_doc(None)
        self.assertEqual(self.vm.map_name, '?')

    def test_archive_reports_the_archive_name_and_notifies_callbacks(self):
        """Verify success returns the archive status and runs the registered callbacks once."""
        self.assertEqual(self.vm.archive_and_clear_map(), 'archived → live_1')
        self.archived.assert_called_once_with()

    def test_archive_without_a_map_does_nothing(self):
        """Verify no archive is attempted and no callback runs when no map is loaded."""
        self.vm.update_doc(None)
        self.assertEqual(self.vm.archive_and_clear_map(), 'ERROR: no map loaded')
        self.app.archive_and_clear.assert_not_called()
        self.archived.assert_not_called()

    def test_archive_failure_is_reported_and_skips_callbacks(self):
        """Verify an application service error becomes an ERROR status without callbacks."""
        self.app.archive_and_clear.side_effect = OSError('read only')
        with self.assertLogs('devkit_ui.view_models.topology_view_model', 'ERROR'):
            self.assertEqual(self.vm.archive_and_clear_map(), 'ERROR: read only')
        self.archived.assert_not_called()

    def test_save_is_delegated_to_the_application_service(self):
        """Verify the named copy request and its status pass straight through."""
        self.app.save_map_as.return_value = 'saved → copy'
        self.assertEqual(self.vm.save_map_as('copy'), 'saved → copy')
        self.app.save_map_as.assert_called_once_with('copy')

    def test_callbacks_observe_updated_map_in_registration_order(self):
        """Verify archive callbacks run in registration order after the map is updated."""
        events = []
        self.vm.register_map_archived_callback(lambda: events.append(('first', self.vm.map_name)))
        self.vm.register_map_archived_callback(lambda: events.append(('second', self.vm.map_name)))

        def archive():
            """Record the old map and simulate the service publishing its replacement."""
            events.append(('archive', self.vm.map_name))
            on_changed = self.app.register_doc_changed_callback.call_args.args[0]
            on_changed(TopoDoc(name='default'))
            return 'live_2'

        self.app.archive_and_clear.side_effect = archive
        self.assertEqual(self.vm.archive_and_clear_map(), 'archived → live_2')
        self.assertEqual(events, [('archive', 'live'), ('first', 'default'), ('second', 'default')])
        self.app.archive_and_clear.assert_called_once_with()

    def test_failed_callback_propagates_and_does_not_run_later_callbacks(self):
        """Verify a callback error propagates and prevents subsequent callbacks from running."""
        error = RuntimeError('refresh failed')
        self.archived.side_effect = error
        later = Mock()
        self.vm.register_map_archived_callback(later)
        with self.assertRaises(RuntimeError) as caught:
            self.vm.archive_and_clear_map()
        self.assertIs(caught.exception, error)
        self.app.archive_and_clear.assert_called_once_with()
        later.assert_not_called()

    def test_saving_copy_keeps_live_map_and_does_not_notify_archive_callbacks(self):
        """Verify a failed copy request preserves the live map without archive notifications."""
        self.app.save_map_as.return_value = 'ERROR: name exists'
        document = self.vm.topo_doc
        self.assertEqual(self.vm.save_map_as('existing'), 'ERROR: name exists')
        self.assertIs(self.vm.topo_doc, document)
        self.assertEqual(self.vm.map_name, 'live')
        self.archived.assert_not_called()


if __name__ == '__main__':
    unittest.main()
