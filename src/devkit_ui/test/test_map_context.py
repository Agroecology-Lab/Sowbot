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

    def test_unloading_a_map_exposes_the_cleared_state(self) -> None:
        """A context must not retain a document or selection after the map is cleared."""
        doc = object()
        topo = SimpleNamespace(topo_doc=doc, current_node='A_1', selected_node='B_2')
        run = SimpleNamespace(drop_node=SimpleNamespace(row_action='row_traversal'))
        context = MapContext(topo, run)
        self.assertIs(context.topo_doc, doc)

        topo.topo_doc, topo.current_node, topo.selected_node = None, 'none', None

        self.assertIsNone(context.topo_doc)
        self.assertEqual(context.current_node, 'none')
        self.assertIsNone(context.selected_node)

    def test_replacing_drop_state_uses_the_new_row_action(self) -> None:
        """Replacing the nested Run state must be visible through an existing context."""
        run = SimpleNamespace(drop_node=SimpleNamespace(row_action='row_traversal'))
        context = MapContext(SimpleNamespace(), run)
        self.assertEqual(context.row_action, 'row_traversal')

        run.drop_node = SimpleNamespace(row_action='limbic_row_follow')

        self.assertEqual(context.row_action, 'limbic_row_follow')

    def test_properties_cannot_be_assigned_through_the_context(self) -> None:
        """Consumers can read shared state but cannot overwrite it through the context."""
        topo = SimpleNamespace(topo_doc=None, current_node='—', selected_node=None)
        run = SimpleNamespace(drop_node=SimpleNamespace(row_action='row_traversal'))
        context = MapContext(topo, run)

        for name in ('topo_doc', 'current_node', 'selected_node', 'row_action'):
            with self.subTest(property=name), self.assertRaises(AttributeError):
                setattr(context, name, 'replacement')

        self.assertEqual(vars(topo), {
            'topo_doc': None, 'current_node': '—', 'selected_node': None,
        })
        self.assertEqual(run.drop_node.row_action, 'row_traversal')


if __name__ == '__main__':
    unittest.main()
