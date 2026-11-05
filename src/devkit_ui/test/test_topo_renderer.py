import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree

from devkit_ui.models import TopoDoc, TopoNode

nicegui = ModuleType('nicegui')
nicegui.ui = SimpleNamespace()

with patch.dict(sys.modules, {'nicegui': nicegui}):
    from devkit_ui.utils.topo_renderer import build_svg


def texts(svg: str, css_class: str) -> list[str]:
    root = ElementTree.fromstring(svg)
    return [
        element.text or ''
        for element in root.iter()
        if element.tag.endswith('text') and element.attrib.get('class') == css_class
    ]


def all_texts(svg: str) -> list[str]:
    root = ElementTree.fromstring(svg)
    return [element.text or '' for element in root.iter() if element.tag.endswith('text')]


def row_doc() -> TopoDoc:
    return TopoDoc(
        name='field',
        nodes=[
            TopoNode(name='ROW_4_IN', x=0.0, y=0.0, meta={'row_id': 4, 'row_role': 'entry'}),
            TopoNode(name='ROW_4_W1', x=1.0, y=0.0, meta={'row_id': 4, 'row_role': 'waypoint'}),
            TopoNode(name='ROW_4_OUT', x=2.0, y=0.0, meta={'row_id': 4, 'row_role': 'exit'}),
            TopoNode(name='ROW_5_IN', x=2.0, y=1.0, meta={'row_id': 5, 'row_role': 'entry'}),
            TopoNode(name='ROW_5_OUT', x=0.0, y=1.0, meta={'row_id': 5, 'row_role': 'exit'}),
            TopoNode(name='HOME', x=1.0, y=2.0),
        ],
    )


class TestTopoRendererLabels(unittest.TestCase):
    def test_each_row_is_labelled_once(self) -> None:
        svg = build_svg(row_doc(), selected=None, current=None)

        self.assertEqual(texts(svg, 'topo-row-label'), ['Row 4', 'Row 5'])

    def test_no_node_names_unless_selected(self) -> None:
        svg = build_svg(row_doc(), selected=None, current='HOME')

        self.assertEqual(texts(svg, 'topo-node-label'), [])
        self.assertEqual(all_texts(svg), ['Row 4', 'Row 5'])

    def test_selected_node_shows_its_name(self) -> None:
        svg = build_svg(row_doc(), selected='ROW_4_W1', current=None)

        self.assertEqual(texts(svg, 'topo-node-label'), ['ROW_4_W1'])

    def test_row_label_is_beside_the_entry_node_and_clear_of_the_row(self) -> None:
        svg = build_svg(row_doc(), selected=None, current=None)
        root = ElementTree.fromstring(svg)
        entry = next(e for e in root.iter() if e.attrib.get('data-node') == 'ROW_4_IN')
        label = next(e for e in root.iter() if e.text == 'Row 4')

        # Row 4 runs left to right from the entry node, so its label sits to the left.
        self.assertLess(float(label.attrib['x']), float(entry.attrib['cx']))
        self.assertEqual(label.attrib['text-anchor'], 'end')

    def test_nodes_are_small(self) -> None:
        svg = build_svg(row_doc(), selected=None, current=None)
        root = ElementTree.fromstring(svg)
        radii = {float(e.attrib['r']) for e in root.iter() if e.attrib.get('class') == 'topo-node'}

        self.assertEqual(radii, {5.0})

    def test_row_label_escapes_non_numeric_identifier(self) -> None:
        doc = TopoDoc(
            name='field',
            nodes=[
                TopoNode(name='ROW_OUT', x=0.0, y=0.0, meta={'row_id': '<7&', 'row_role': 'exit'}),
            ],
        )

        svg = build_svg(doc, selected=None, current=None)

        self.assertIn('Row &lt;7&amp;', svg)
        self.assertEqual(texts(svg, 'topo-row-label'), ['Row <7&'])


if __name__ == '__main__':
    unittest.main()
