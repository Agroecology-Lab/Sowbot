"""The delete confirmation dialogs over fake UI elements."""
import asyncio
import sys
import unittest
from unittest.mock import Mock, patch

from devkit_ui import process_registry  # noqa: F401  # pylint: disable=unused-import
from devkit_ui.models import TopoDoc, TopoNode
from devkit_ui.view_models.topology_view_model import TopologyViewModel
from test_page_composition import page_nicegui
from test_run_ui import fake_ui

_modules_before = set(sys.modules)
with patch.dict(sys.modules, {'nicegui': page_nicegui}):
    from devkit_ui.pages.delete_dialogs import confirm_delete_node, confirm_delete_row
    _imported_here = set(sys.modules) - _modules_before

# See test_nav_page for why the parent packages are cleaned up.
for _name in _imported_here:
    _parent, _, _child = _name.rpartition('.')
    if _parent in sys.modules and hasattr(sys.modules[_parent], _child):
        delattr(sys.modules[_parent], _child)


def labels() -> list[str]:
    """Return the text of every label rendered so far."""
    return [e.text for e in fake_ui.elements if e.kind == 'label']


class TestDeleteDialogs(unittest.TestCase):
    def setUp(self):
        """Load a map with one standalone node and a two-node row."""
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        self.topo_vm = TopologyViewModel(Mock())
        self.topo_vm.update_doc(TopoDoc(name='field', nodes=[
            TopoNode(name='A', x=0.0, y=0.0),
            TopoNode(name='R_IN', x=1.0, y=0.0, meta={'row_id': 3}),
            TopoNode(name='R_OUT', x=2.0, y=0.0, meta={'row_id': 3}),
        ]))
        self.on_confirm = Mock()

    def test_confirmed_node_deletion_calls_back_with_the_name(self):
        """Verify choosing Delete passes the node name on."""
        fake_ui.dialog_results.append('ok')
        asyncio.run(confirm_delete_node(self.topo_vm, 'A', self.on_confirm))
        self.on_confirm.assert_called_once_with('A')
        self.assertIn('Delete topo node "A"?', labels())

    def test_cancelled_node_deletion_changes_nothing(self):
        """Verify cancelling leaves the callback uncalled."""
        fake_ui.dialog_results.append('cancel')
        asyncio.run(confirm_delete_node(self.topo_vm, 'A', self.on_confirm))
        self.on_confirm.assert_not_called()

    def test_row_node_warns_to_delete_the_whole_row_from_the_plan_tab(self):
        """Verify a row member's dialog points at the Plan tab."""
        fake_ui.dialog_results.append('cancel')
        asyncio.run(confirm_delete_node(self.topo_vm, 'R_IN', self.on_confirm))
        self.assertTrue(any('part of row 3' in text for text in labels()))

    def test_missing_names_and_maps_open_no_dialog(self):
        """Verify an empty, unknown or unloaded target is ignored."""
        for name in (None, '', 'missing'):
            asyncio.run(confirm_delete_node(self.topo_vm, name, self.on_confirm))
        self.topo_vm.update_doc(None)
        asyncio.run(confirm_delete_node(self.topo_vm, 'A', self.on_confirm))
        asyncio.run(confirm_delete_row(self.topo_vm, 3, self.on_confirm))
        self.assertEqual([e for e in fake_ui.elements if e.kind == 'dialog'], [])
        self.on_confirm.assert_not_called()

    def test_confirmed_row_deletion_lists_its_nodes_and_calls_back(self):
        """Verify the row dialog names the nodes to be removed and passes the row id on."""
        fake_ui.dialog_results.append('ok')
        asyncio.run(confirm_delete_row(self.topo_vm, 3, self.on_confirm))
        self.on_confirm.assert_called_once_with(3)
        self.assertEqual(labels()[:3], ['Delete row 3?', '2 nodes will be removed:', 'R_IN, R_OUT'])

    def test_row_without_nodes_opens_no_dialog(self):
        """Verify a row with no nodes in the map is ignored."""
        asyncio.run(confirm_delete_row(self.topo_vm, 99, self.on_confirm))
        self.assertEqual([e for e in fake_ui.elements if e.kind == 'dialog'], [])
        self.on_confirm.assert_not_called()
