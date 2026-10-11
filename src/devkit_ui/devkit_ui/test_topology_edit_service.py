"""Tests for TopologyEditService staging rules that need no ROS node."""
import unittest
from types import SimpleNamespace

from devkit_ui.application_services.topology_edit_service import (
    TopologyEditService,
    clean_name,
)
from devkit_ui.constants import NAV_ACTION
from devkit_ui.models import TopoDoc, TopoNode
from devkit_ui.topo_results import TopologyEditError


def make_service(doc: TopoDoc | None) -> TopologyEditService:
    """Return a service whose live map is doc."""
    return TopologyEditService(SimpleNamespace(get_doc=lambda: doc))


def row_node(name: str, row_id: int) -> TopoNode:
    """Return a node tagged as belonging to row_id."""
    return TopoNode(name=name, x=0.0, y=0.0, meta={'row_id': row_id})


class TestCleanName(unittest.TestCase):
    """Names are uppercased, spaces become underscores and other characters are dropped."""

    def test_normalises_name(self) -> None:
        """Verify case, spaces and punctuation are normalised."""
        self.assertEqual(clean_name('  gate 1-a! '), 'GATE_1A')

    def test_none_gives_empty_name(self) -> None:
        """Verify a missing name becomes empty rather than raising."""
        self.assertEqual(clean_name(None), '')


class TestDeleteStaging(unittest.TestCase):
    """Deleting nodes and rows validates against the live map and reports what it removes."""

    def setUp(self) -> None:
        """Build a map with two nodes in row 4 and one standalone node."""
        self.doc = TopoDoc(name='field', nodes=[
            row_node('A_IN', 4), row_node('A_OUT', 4), TopoNode(name='HOME', x=1.0, y=1.0)])
        self.service = make_service(self.doc)

    def test_delete_node_reports_affected_and_statuses(self) -> None:
        """Verify a node deletion names the node and removes it from a saved copy."""
        edit = self.service.stage_delete_node('HOME')

        self.assertEqual((edit.pending, edit.success), ('deleting HOME…', 'deleted HOME'))
        self.assertEqual(edit.affected, frozenset({'HOME'}))
        edit.modify(self.doc)
        self.assertFalse(self.doc.has_node('HOME'))

    def test_delete_unknown_node_is_rejected(self) -> None:
        """Verify an unknown node name is rejected and leaves the map alone."""
        with self.assertRaisesRegex(TopologyEditError, "'NOPE' not in map"):
            self.service.stage_delete_node('NOPE')
        self.assertEqual(len(self.doc.nodes), 3)

    def test_delete_row_collects_all_row_nodes(self) -> None:
        """Verify a row deletion covers every node of that row and no others."""
        edit = self.service.stage_delete_row(4)

        self.assertEqual(edit.affected, frozenset({'A_IN', 'A_OUT'}))
        self.assertEqual(edit.pending, 'deleting row 4 (2 nodes)…')
        self.assertEqual(edit.success, 'deleted row 4 (2 nodes)')
        edit.modify(self.doc)
        self.assertEqual([node.name for node in self.doc.nodes], ['HOME'])

    def test_delete_empty_row_is_rejected(self) -> None:
        """Verify a row with no nodes is rejected."""
        with self.assertRaisesRegex(TopologyEditError, 'no nodes for row 9'):
            self.service.stage_delete_row(9)

    def test_deletes_without_a_map_report_it(self) -> None:
        """Verify both deletions report a missing map instead of raising AttributeError."""
        service = make_service(None)
        for stage, target in ((service.stage_delete_node, 'HOME'), (service.stage_delete_row, 4)):
            with self.subTest(stage=stage.__name__):
                with self.assertRaisesRegex(TopologyEditError, 'map not loaded'):
                    stage(target)


class TestDropStaging(unittest.TestCase):
    """Dropping a node validates its name and picks the node it connects to."""

    def stage(self, doc, name='NEW', **overrides):
        """Stage a navigation drop on doc with simulation defaults."""
        args = {'pose': (1.0, 2.0), 'is_sim': True, 'gps': None, 'row_action': 'row_follow',
                'current_node': None, 'selected_node': None} | overrides
        return make_service(doc).stage_drop(name, None, 'entry', **args)

    def test_current_node_wins_over_selected(self) -> None:
        """Verify the current node is the connection target when both exist."""
        doc = TopoDoc(name='field', nodes=[
            TopoNode(name='CUR', x=0.0, y=0.0), TopoNode(name='SEL', x=0.0, y=0.0)])

        drop = self.stage(doc, current_node='CUR', selected_node='SEL')

        self.assertEqual([(e.node, e.action) for e in drop.node.edges], [('CUR', NAV_ACTION)])
        self.assertTrue(doc.has_node('NEW'))

    def test_placeholder_current_node_falls_back_to_selected(self) -> None:
        """Verify the UI placeholder for no current node is ignored."""
        doc = TopoDoc(name='field', nodes=[TopoNode(name='SEL', x=0.0, y=0.0)])

        drop = self.stage(doc, current_node='—', selected_node='SEL')

        self.assertEqual([e.node for e in drop.node.edges], ['SEL'])

    def test_rejections_leave_map_unchanged(self) -> None:
        """Verify each rejection raises with its status and adds no node."""
        doc = TopoDoc(name='field', nodes=[TopoNode(name='TAKEN', x=0.0, y=0.0)])
        cases = [
            ({'name': '!!!'}, 'node name required'),
            ({'name': 'taken'}, 'TAKEN already exists'),
            ({'pose': None, 'is_sim': False}, 'no odometry'),
        ]
        for overrides, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(TopologyEditError, message):
                    self.stage(doc, **overrides)
        self.assertEqual([node.name for node in doc.nodes], ['TAKEN'])


class TestF2CSaveStaging(unittest.TestCase):
    """F2C saves reject unusable GPS fixes before building any rows."""

    def stage(self, gps, **overrides):
        """Stage a simulated F2C save with one planned swath and the given GPS fix."""
        args = {'swaths': [[(51.0, -2.0), (51.0, -1.9)]], 'origin_ll': (51.0, -2.0),
                'contour_used': False, 'break_after': frozenset(), 'is_sim': True,
                'odom_xy': None, 'gps': gps, 'row_action': 'row_follow',
                'current_node': None, 'selected_node': None} | overrides
        return make_service(TopoDoc(name='field')).stage_f2c_save('row', 1, False, **args)

    def test_rejects_unusable_gps(self) -> None:
        """Verify missing, non-finite and zero GPS fixes are rejected with their reasons."""
        def fix(lat, lon):
            return SimpleNamespace(latitude=lat, longitude=lon, status=SimpleNamespace(status=0))

        cases = [
            (None, 'no GPS fix yet'),
            (fix(float('nan'), 0.0), 'not finite'),
            (fix(0.0, 0.0), r'are 0,0'),
        ]
        for gps, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(TopologyEditError, message):
                    self.stage(gps)

    def test_sim_without_origin_is_rejected(self) -> None:
        """Verify a simulated save refuses to anchor rows to the sim GPS datum."""
        gps = SimpleNamespace(latitude=51.0, longitude=-2.0, status=SimpleNamespace(status=0))
        with self.assertRaisesRegex(TopologyEditError, 'no F2C field origin'):
            self.stage(gps, origin_ll=None)

    def test_negative_status_warns_but_proceeds(self) -> None:
        """Verify a negative fix status only adds a warning."""
        gps = SimpleNamespace(latitude=51.0, longitude=-2.0, status=SimpleNamespace(status=-1))

        edit = self.stage(gps)

        self.assertEqual(len(edit.warnings), 1)
        self.assertIn('status=-1', edit.warnings[0])


if __name__ == '__main__':
    unittest.main()
