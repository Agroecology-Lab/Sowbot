"""Regression cases for row batches and repair plans at the extracted boundary."""

import copy
import unittest

from devkit_ui.constants import NAV_ACTION, ROW_ACTION
from devkit_ui.models import TopoDoc, TopoNode
from devkit_ui.test_topo_row_builder import ANCHOR, settings, straight_rows, swath, targets
from devkit_ui.topo_row_builder import (
    RowBuild,
    add_missing_edges,
    apply_row_nodes,
    build_row_nodes,
    plan_row_repair,
)


class TestRowBatchRegressions(unittest.TestCase):
    def test_fragment_break_keeps_original_index_after_skipped_swaths(self) -> None:
        """Keep a gap after the original swath even when earlier swaths are unusable."""
        rows = [[], *straight_rows(1), [], *straight_rows(2)]

        build = build_row_nodes(rows, ANCHOR, settings(
            row_id_start=10, break_after=frozenset({1})))

        self.assertEqual(build.added, [11, 13, 14])
        self.assertEqual(targets(build.nodes['FIELD_R11_OUT']), {})
        self.assertEqual(targets(build.nodes['FIELD_R13_IN']), {'FIELD_R13_OUT': ROW_ACTION})
        self.assertEqual(targets(build.nodes['FIELD_R13_OUT']), {'FIELD_R14_IN': NAV_ACTION})
        self.assertEqual(targets(build.nodes['FIELD_R14_IN']), {
            'FIELD_R14_OUT': ROW_ACTION, 'FIELD_R13_OUT': NAV_ACTION,
        })

    def test_disabled_contour_ignores_interior_points_and_waypoint_interval(self) -> None:
        """A multi-point swath still produces a direct edge when contour mode is off."""
        build = build_row_nodes(
            [swath((0, 0), (5, 5), (0, 10))], ANCHOR,
            settings(contour_used=False, waypoint_interval_m=0))

        self.assertEqual(set(build.nodes), {'FIELD_R1_IN', 'FIELD_R1_OUT'})
        self.assertEqual(targets(build.nodes['FIELD_R1_IN']), {'FIELD_R1_OUT': ROW_ACTION})
        self.assertEqual(targets(build.nodes['FIELD_R1_OUT']), {})

    def test_endpoint_coordinates_round_after_adding_anchor_offset(self) -> None:
        """Preserve millimetre rounding for fractional and negative map coordinates."""
        build = build_row_nodes(
            [swath((-12.3456, -23.4567), (1.2346, 2.3456))], ANCHOR, settings())

        entry = build.nodes['FIELD_R1_IN']
        exit_node = build.nodes['FIELD_R1_OUT']
        self.assertEqual((entry.x, entry.y), (-2.346, -3.457))
        self.assertEqual((exit_node.x, exit_node.y), (11.235, 22.346))

    def test_invalid_consumed_endpoint_raises_without_changing_swaths(self) -> None:
        """Reject malformed endpoints without modifying the caller's planned rows."""
        for endpoint, error in (((51.0,), ValueError), (None, TypeError)):
            with self.subTest(endpoint=endpoint):
                rows = [*straight_rows(1), [(51.0, -2.0), endpoint]]
                before = copy.deepcopy(rows)

                with self.assertRaises(error):
                    build_row_nodes(rows, ANCHOR, settings())

                self.assertEqual(rows, before)

    def test_contour_zero_interval_propagates_resampling_error(self) -> None:
        """Do not silently save a straight row when contour spacing is invalid."""
        with self.assertRaises(ZeroDivisionError):
            build_row_nodes([swath((0, 0), (0, 5), (5, 5))], ANCHOR, settings(
                contour_used=True, waypoint_interval_m=0))

    def test_empty_overwrite_removes_only_matching_rows_and_incoming_edges(self) -> None:
        """An empty replacement can clear a prefix while retaining unrelated map content."""
        for overwrite in (False, True):
            with self.subTest(overwrite=overwrite):
                old = build_row_nodes(straight_rows(1), ANCHOR, settings())
                dock = TopoNode(name='DOCK', x=0, y=0)
                neighbour = TopoNode(name='FIELD_RIDGE', x=1, y=1)
                unrelated = TopoNode(name='FIELD2_R1_IN', x=2, y=2)
                dock.add_edge('FIELD_R1_IN', action=NAV_ACTION)
                dock.add_edge(unrelated.name, action='authored')
                doc = TopoDoc(name='field', nodes=[
                    dock, neighbour, unrelated, *old.nodes.values(),
                ])
                before = copy.deepcopy(doc.to_dict())

                apply_row_nodes(doc, RowBuild(), 'FIELD', overwrite)

                if overwrite:
                    self.assertEqual({node.name for node in doc.nodes}, {'DOCK', 'FIELD2_R1_IN'})
                    self.assertEqual(targets(dock), {'FIELD2_R1_IN': 'authored'})
                    self.assertIs(doc.get_node(unrelated.name), unrelated)
                else:
                    self.assertEqual(doc.to_dict(), before)


class TestRepairPlanRegressions(unittest.TestCase):
    def test_invalid_and_incomplete_rows_do_not_hide_a_valid_chain(self) -> None:
        """Count incomplete rows, ignore invalid metadata, and still repair valid rows."""
        doc = TopoDoc(name='field', nodes=[
            TopoNode(name='OUT', x=0, y=10, meta={'row_id': '7', 'row_role': 'exit'}),
            TopoNode(name='BAD', x=1000, y=1000,
                     meta={'row_id': 'invalid', 'row_role': 'entry'}),
            TopoNode(name='ORPHAN_W1', x=1000, y=1000,
                     meta={'row_id': 8, 'row_role': 'waypoint'}),
            TopoNode(name='IN', x=0, y=0, meta={'row_id': 7, 'row_role': 'entry'}),
        ])
        before = copy.deepcopy(doc.to_dict())

        plan = plan_row_repair(doc, ROW_ACTION)

        self.assertEqual(plan.row_count, 2)
        self.assertFalse(plan.headlands_skipped)
        self.assertEqual(plan.edges, (('IN', 'OUT', ROW_ACTION),))
        self.assertEqual(doc.to_dict(), before)

    def test_infinite_row_id_raises_before_mutating_existing_edges(self) -> None:
        """Propagate unconvertible infinite IDs without applying a partial repair."""
        for row_id in (float('inf'), float('-inf')):
            with self.subTest(row_id=row_id):
                build = build_row_nodes(straight_rows(1), ANCHOR, settings())
                doc = TopoDoc(name='field', nodes=list(build.nodes.values()))
                doc.insert_node(TopoNode(name='BAD', x=0, y=0, meta={
                    'row_id': row_id, 'row_role': 'entry',
                }))
                before = copy.deepcopy(doc.to_dict())

                with self.assertRaises(OverflowError):
                    plan_row_repair(doc, ROW_ACTION)

                self.assertEqual(doc.to_dict(), before)

    def test_missing_destination_is_allowed_without_creating_a_node(self) -> None:
        """Keep a requested outgoing edge even if its target is absent from this document."""
        node = TopoNode(name='IN', x=0, y=0)
        doc = TopoDoc(name='field', nodes=[node])
        edges = (('IN', 'MISSING', ROW_ACTION), ('IN', 'MISSING', NAV_ACTION))

        self.assertEqual(add_missing_edges(doc, edges), 1)

        self.assertEqual(targets(node), {'MISSING': ROW_ACTION})
        self.assertEqual([entry.name for entry in doc.nodes], ['IN'])
        self.assertEqual(add_missing_edges(doc, edges), 0)


if __name__ == '__main__':
    unittest.main()
