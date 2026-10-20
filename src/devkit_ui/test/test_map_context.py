"""MapContext reads live map state from the view models it joins."""
import unittest
from types import SimpleNamespace

from devkit_ui.view_models.map_context import MapContext


class TestMapContext(unittest.TestCase):
    def test_every_property_reads_live_state(self) -> None:
        """Verify changes to the underlying view models show up without rebuilding the context."""
        topo = SimpleNamespace(topo_doc=None, current_node='—', selected_node=None)
        run = SimpleNamespace(drop_node=SimpleNamespace(row_action='row_traversal'))
        context = MapContext(topo, run)
        self.assertEqual((context.topo_doc, context.current_node, context.selected_node,
                          context.row_action), (None, '—', None, 'row_traversal'))

        doc = object()
        topo.topo_doc, topo.current_node, topo.selected_node = doc, 'A_1', 'B_2'
        run.drop_node.row_action = 'limbic_row_follow'

        self.assertEqual((context.topo_doc, context.current_node, context.selected_node,
                          context.row_action), (doc, 'A_1', 'B_2', 'limbic_row_follow'))


if __name__ == '__main__':
    unittest.main()
