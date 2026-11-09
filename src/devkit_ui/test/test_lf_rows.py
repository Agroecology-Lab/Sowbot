"""Tests for finding a LiteFarm (field, row) in the topological map."""
import unittest

from devkit_ui.missions.lf_rows import LfRowError, lf_row_entries, resolve_lf_row
from devkit_ui.models import TopoDoc, TopoNode


def row_nodes(row_id, field, row, frag=0, *, names=None):
    """Return the entry, a waypoint and the exit node of one swath, all carrying its LiteFarm identity."""
    entry, exit_ = names or (f'R{row_id}_IN', f'R{row_id}_OUT')
    lf = {'row_id': row_id, 'lf_field': field, 'lf_row': row, 'lf_frag': frag}
    return [TopoNode(name=entry, x=0.0, y=0.0, meta={**lf, 'row_role': 'entry'}),
            TopoNode(name=f'R{row_id}_W1', x=1.0, y=0.0, meta={**lf, 'row_role': 'waypoint'}),
            TopoNode(name=exit_, x=2.0, y=0.0, meta={**lf, 'row_role': 'exit'})]


def doc_of(*swaths):
    """Return a map holding the given swaths and one navigation node."""
    nodes = [TopoNode(name='HOME', x=-5.0, y=0.0)]
    for swath in swaths:
        nodes += swath
    return TopoDoc(name='farm', nodes=nodes)


class ResolveLfRowTest(unittest.TestCase):
    """A row is found by the metadata the farm map import wrote, never by a derived id."""

    def test_a_whole_row_is_its_entry_node(self):
        """One swath gives one entry node, not its waypoints or exit."""
        doc = doc_of(row_nodes(10, 1, 0), row_nodes(11, 1, 1))
        self.assertEqual(resolve_lf_row(doc, 1, 1), ['R11_IN'])

    def test_fields_do_not_share_row_numbers(self):
        """Row 0 of field 1 and row 0 of field 2 are different rows."""
        doc = doc_of(row_nodes(10, 1, 0), row_nodes(20, 2, 0))
        self.assertEqual(resolve_lf_row(doc, 1, 0), ['R10_IN'])
        self.assertEqual(resolve_lf_row(doc, 2, 0), ['R20_IN'])

    def test_fragments_come_in_row_id_order_whatever_the_node_order(self):
        """A row split by an obstacle gives its entries in the order the robot must drive them."""
        doc = doc_of(row_nodes(12, 1, 1, 1), row_nodes(11, 1, 1, 0))
        self.assertEqual(resolve_lf_row(doc, 1, 1), ['R11_IN', 'R12_IN'])

    def test_the_names_do_not_matter(self):
        """Hand-renamed nodes are still found, because the lookup reads metadata only."""
        doc = doc_of(row_nodes(10, 1, 0, names=('GATE_A', 'GATE_B')))
        self.assertEqual(resolve_lf_row(doc, 1, 0), ['GATE_A'])

    def test_a_missing_frag_is_fragment_zero(self):
        """A swath without lf_frag is the only fragment of its row."""
        swath = row_nodes(10, 1, 0)
        for node in swath:
            del node.meta['lf_frag']
        self.assertEqual(resolve_lf_row(doc_of(swath), 1, 0), ['R10_IN'])

    def test_unknown_row_is_refused(self):
        """A row the map does not have, or a map with no LiteFarm rows at all, raises LfRowError."""
        with self.assertRaisesRegex(LfRowError, 'field 1 row 9 is not in the map'):
            resolve_lf_row(doc_of(row_nodes(10, 1, 0)), 1, 9)
        with self.assertRaises(LfRowError):
            resolve_lf_row(doc_of(), 1, 0)

    def test_two_swaths_claiming_one_fragment_are_refused(self):
        """Ambiguous maps are refused rather than guessed at (for example two imports in one map)."""
        doc = doc_of(row_nodes(10, 1, 0), row_nodes(30, 1, 0))
        with self.assertRaisesRegex(LfRowError, 'same fragment'):
            resolve_lf_row(doc, 1, 0)

    def test_fragments_that_disagree_with_row_ids_are_refused(self):
        """Fragment 1 before fragment 0 in row id order means the map was edited or built wrongly."""
        doc = doc_of(row_nodes(10, 1, 1, 1), row_nodes(11, 1, 1, 0))
        with self.assertRaisesRegex(LfRowError, 'out of order'):
            resolve_lf_row(doc, 1, 1)

    def test_nodes_without_an_integer_identity_are_ignored(self):
        """A bool, a string or a missing key never makes a row."""
        swath = row_nodes(10, 1, 0)
        swath[0].meta['lf_field'] = True
        swath[2].meta['row_id'] = '10'
        self.assertEqual(lf_row_entries(doc_of(swath)), {})

    def test_a_map_with_no_lf_metadata_has_no_rows(self):
        """A plan imported from a single-field file cannot be targeted by a station mission."""
        plain = [TopoNode(name='R1_IN', x=0.0, y=0.0, meta={'row_id': 1, 'row_role': 'entry'})]
        self.assertEqual(lf_row_entries(doc_of(plain)), {})
