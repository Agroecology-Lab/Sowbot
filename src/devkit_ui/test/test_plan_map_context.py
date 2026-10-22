"""Plan intents read the live MapContext supplied at construction."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call

from devkit_ui.models import TopoDoc, TopoNode
from devkit_ui.plan_types import PlannedRows
from devkit_ui.view_models.map_context import MapContext
from devkit_ui.view_models.plan_view_model import PlanViewModel, RepairOffer


class TestPlanMapContext(unittest.TestCase):
    def setUp(self) -> None:
        """Use a real map context with mutable source state and a mock plan service."""
        doc = TopoDoc(name='field.yaml')
        doc.insert_node(TopoNode(name='HOME'))
        self.topo = SimpleNamespace(topo_doc=doc, current_node='HOME', selected_node=None)
        self.run = SimpleNamespace(drop_node=SimpleNamespace(row_action='row_traversal'))
        self.app = Mock(spec=['save_rows', 'repair_connectivity'])
        self.status = Mock()
        self.vm = PlanViewModel(self.app, MapContext(self.topo, self.run))

    def test_each_save_uses_the_latest_action_current_node_and_selection(self) -> None:
        """Saving must not cache any of the three values taken from the context."""
        planned = PlannedRows([[(51.0, -2.0), (51.001, -2.0)]], (51.0, -2.0))
        self.topo.selected_node = 'SELECTED'
        self.vm.save_rows(planned, 'crop', 0, False, self.status)

        self.run.drop_node = SimpleNamespace(row_action='limbic_row_follow')
        self.topo.current_node, self.topo.selected_node = None, 'NEW_SELECTION'
        self.vm.save_rows(planned, 'new crop', 7, True, self.status)

        self.assertEqual(self.app.save_rows.call_args_list, [
            call(planned, 'crop', 0, False, row_action='row_traversal',
                 current_node='HOME', selected_node='SELECTED', on_status=self.status),
            call(planned, 'new crop', 7, True, row_action='limbic_row_follow',
                 current_node=None, selected_node='NEW_SELECTION', on_status=self.status),
        ])

    def test_save_without_a_draft_or_map_still_forwards_current_context(self) -> None:
        """The service receives missing inputs so its existing validation can report them."""
        self.topo.topo_doc = None
        self.topo.current_node, self.topo.selected_node = 'none', None

        self.vm.save_rows(None, '', 0, False, self.status)

        self.app.save_rows.assert_called_once_with(
            None, '', 0, False, row_action='row_traversal',
            current_node='none', selected_node=None, on_status=self.status)

    def test_repair_uses_a_replacement_map_and_the_latest_row_action(self) -> None:
        """Map switching must refresh both base-node validation and the row action."""
        self.vm.repair('HOME', self.status)
        replacement = TopoDoc(name='other.yaml')
        replacement.insert_node(TopoNode(name='NEW_BASE'))
        self.topo.topo_doc = replacement
        self.run.drop_node.row_action = 'limbic_row_follow'

        self.vm.repair('NEW_BASE', self.status)

        self.assertEqual(self.app.repair_connectivity.call_args_list, [
            call('row_traversal', 'HOME', on_status=self.status),
            call('limbic_row_follow', 'NEW_BASE', on_status=self.status),
        ])
        self.status.assert_not_called()

    def test_repair_rejects_a_base_that_only_exists_in_the_previous_map(self) -> None:
        """A stale node name cannot reach the repair service after a map switch."""
        self.topo.topo_doc = TopoDoc(name='other.yaml')

        self.vm.repair('HOME', self.status)

        self.status.assert_called_once_with("ERROR: base node 'HOME' not in map")
        self.app.repair_connectivity.assert_not_called()

    def test_repair_without_a_map_delegates_to_service_validation(self) -> None:
        """Unloading a map must not leave the view model validating against the old map."""
        self.topo.topo_doc = None
        self.run.drop_node.row_action = 'limbic_row_follow'

        self.vm.repair('NEW_BASE', self.status)

        self.app.repair_connectivity.assert_called_once_with(
            'limbic_row_follow', 'NEW_BASE', on_status=self.status)
        self.status.assert_not_called()

    def test_replacing_map_refreshes_rows_snapshot_and_repair_offer(self) -> None:
        """All map-derived helpers use the new document and current selection."""
        self.assertEqual(self.vm.available_rows(), {})
        self.assertEqual(self.vm.topo_snapshot(), frozenset({('HOME', None, None)}))
        self.assertEqual(self.vm.repair_offer(), RepairOffer(0, 'HOME'))
        replacement = TopoDoc(name='other.yaml')
        replacement.insert_node(TopoNode(name='IN', meta={'row_id': 0, 'row_role': 'entry'}))
        replacement.insert_node(TopoNode(name='OUT', meta={'row_id': 0, 'row_role': 'exit'}))
        self.topo.topo_doc = replacement
        self.topo.current_node, self.topo.selected_node = '—', 'IN'

        self.assertEqual(self.vm.available_rows(), {0: 'IN'})
        self.assertEqual(self.vm.topo_snapshot(), frozenset({
            ('IN', 0, 'entry'), ('OUT', 0, 'exit'),
        }))
        self.assertEqual(self.vm.repair_offer(), RepairOffer(1, 'IN'))

    def test_unloading_map_clears_previously_read_map_helpers(self) -> None:
        """Helpers distinguish an unloaded map from the previously loaded document."""
        self.assertIsNotNone(self.vm.topo_snapshot())
        self.assertIsNotNone(self.vm.repair_offer())
        self.topo.topo_doc = None

        self.assertEqual(self.vm.available_rows(), {})
        self.assertIsNone(self.vm.topo_snapshot())
        self.assertIsNone(self.vm.repair_offer())


if __name__ == '__main__':
    unittest.main()
