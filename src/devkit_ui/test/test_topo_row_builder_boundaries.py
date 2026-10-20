"""Boundary and graph-preservation tests for the extracted row builder."""

import copy
import unittest

from devkit_ui.constants import NAV_ACTION, ROW_ACTION
from devkit_ui.models import TopoDoc, TopoEdge, TopoNode
from devkit_ui.topo_row_builder import add_missing_edges, apply_row_nodes, build_row_nodes, plan_row_repair
from test_topo_row_builder import ANCHOR, settings, straight_rows, swath, targets


class TestBuildBoundaries(unittest.TestCase):
    def test_empty_and_short_swaths_do_not_consume_splice_endpoints(self) -> None:
        """Verify unusable swaths retain ID slots but leave splicing to usable row endpoints."""
        rows = [[], *straight_rows(1), swath((1, 1)), *straight_rows(1), []]
        before = copy.deepcopy(rows)

        build = build_row_nodes(rows, ANCHOR, settings(row_id_start=7, connect_to='DOCK'))

        self.assertEqual(rows, before)
        self.assertEqual(build.added, [8, 10])
        self.assertEqual(build.skipped, [])
        self.assertEqual(build.row_names, {
            8: ('FIELD_R8_IN', 'FIELD_R8_OUT'),
            10: ('FIELD_R10_IN', 'FIELD_R10_OUT'),
        })
        self.assertEqual(targets(build.nodes['FIELD_R8_IN']), {
            'FIELD_R8_OUT': ROW_ACTION, 'DOCK': NAV_ACTION,
        })
        self.assertEqual(targets(build.nodes['FIELD_R10_OUT']), {'DOCK': NAV_ACTION})
        self.assertEqual(targets(build.nodes['FIELD_R8_OUT']), {'FIELD_R10_IN': NAV_ACTION})

    def test_break_indices_are_relative_to_nondefault_row_start(self) -> None:
        """Verify break indices remain relative to the swaths when row numbering starts later."""
        build = build_row_nodes(straight_rows(3), ANCHOR, settings(
            row_id_start=20, break_after=frozenset({0})))

        self.assertEqual(targets(build.nodes['FIELD_R20_OUT']), {})
        self.assertNotIn('FIELD_R20_OUT', targets(build.nodes['FIELD_R21_IN']))
        self.assertEqual(targets(build.nodes['FIELD_R21_OUT']), {'FIELD_R22_IN': NAV_ACTION})
        self.assertEqual(targets(build.nodes['FIELD_R22_IN'])['FIELD_R21_OUT'], NAV_ACTION)

    def test_wide_field_keeps_every_consecutive_row_connected(self) -> None:
        """Verify wide fields retain bidirectional headland links between consecutive rows."""
        build = build_row_nodes(straight_rows(5, spacing=10, length=2), ANCHOR, settings())
        expected = {(f'FIELD_R{i}_IN', f'FIELD_R{i}_OUT', ROW_ACTION) for i in range(1, 6)}
        for i in range(1, 5):
            expected.add((f'FIELD_R{i}_OUT', f'FIELD_R{i + 1}_IN', NAV_ACTION))
            expected.add((f'FIELD_R{i + 1}_IN', f'FIELD_R{i}_OUT', NAV_ACTION))

        self.assertEqual({(node.name, edge.node, edge.action)
                          for node in build.nodes.values() for edge in node.edges}, expected)

    def test_contour_without_intermediate_samples_keeps_direct_row_edge(self) -> None:
        """Verify short contour rows retain a direct entry-to-exit edge without waypoints."""
        for points in ([(0, 0), (0, 1)], [(0, 0), (0, 0.5), (0.1, 1)]):
            with self.subTest(points=points):
                build = build_row_nodes([swath(*points)], ANCHOR, settings(
                    contour_used=True, waypoint_interval_m=10))
                self.assertEqual(set(build.nodes), {'FIELD_R1_IN', 'FIELD_R1_OUT'})
                self.assertEqual(targets(build.nodes['FIELD_R1_IN']), {
                    'FIELD_R1_OUT': ROW_ACTION,
                })
                self.assertEqual(targets(build.nodes['FIELD_R1_OUT']), {})

    def test_waypoints_use_anchor_offset_and_serializable_survey_metadata(self) -> None:
        """Verify waypoint poses include the anchor offset and retain survey metadata."""
        build = build_row_nodes([swath((0, 0), (0, 10), (10, 10))], ANCHOR, settings(
            contour_used=True, waypoint_interval_m=5, row_id_start=9, nav_frame='survey'))
        waypoint = build.nodes['FIELD_R9_W1'].to_dict()

        self.assertEqual(waypoint['node']['pose']['position'], {'x': 10.0, 'y': 25.0, 'z': 0.0})
        self.assertEqual(waypoint['node']['nav_frame'], 'survey')
        properties = waypoint['node']['properties']
        lat, lon = swath((0, 5))[0]
        self.assertEqual(properties['gps_lat'], round(lat, 7))
        self.assertEqual(properties['gps_lon'], round(lon, 7))
        self.assertEqual(properties['xy_goal_tolerance'], 0.1)
        self.assertEqual(properties['yaw_goal_tolerance'], 0.05)
        for key, value in {
            'row_id': 9, 'row_role': 'waypoint', 'gps_fix_type': 2,
            'gps_hdop': None, 'timestamp': '01-01-2026_00-00-00', 'dropped_by': 'webui_f2c',
        }.items():
            self.assertEqual(properties[key], value, key)
            self.assertEqual(waypoint['meta'][key], value, key)
        self.assertEqual(waypoint['meta']['map'], 'field')
        self.assertEqual(waypoint['meta']['node'], 'FIELD_R9_W1')


class TestApplyPreservation(unittest.TestCase):
    def test_overwrite_prunes_incoming_edges_and_keeps_other_prefixes(self) -> None:
        """Verify overwrite removes old rows and incoming edges while preserving other prefixes."""
        old = build_row_nodes([swath((0, 0), (0, 10), (10, 10))], ANCHOR, settings(
            contour_used=True, waypoint_interval_m=5))
        dock = TopoNode(name='DOCK', x=0, y=0)
        neighbour = TopoNode(name='FIELD2_R1_IN', x=1, y=1)
        dock.add_edge('FIELD_R1_W1', action=NAV_ACTION)
        dock.add_edge('FIELD_R1_IN', action=NAV_ACTION)
        dock.add_edge(neighbour.name, action='authored')
        doc = TopoDoc(name='field', nodes=[dock, neighbour, *old.nodes.values()])
        replacement = build_row_nodes(straight_rows(1), ANCHOR, settings())

        apply_row_nodes(doc, replacement, 'FIELD', overwrite=True)

        self.assertEqual({node.name for node in doc.nodes}, {
            'DOCK', 'FIELD2_R1_IN', 'FIELD_R1_IN', 'FIELD_R1_OUT',
        })
        self.assertEqual(targets(dock), {'FIELD2_R1_IN': 'authored'})
        self.assertIs(doc.get_node(neighbour.name), neighbour)
        self.assertIs(doc.get_node('FIELD_R1_IN'), replacement.nodes['FIELD_R1_IN'])

    def test_partial_duplicate_preserves_authored_node_and_inserts_missing_nodes(self) -> None:
        """Verify duplicate names preserve authored nodes while missing nodes are inserted."""
        existing = TopoNode(name='FIELD_R1_IN', x=99, y=88, meta={'note': 'surveyed'})
        existing.add_edge('DOCK', action='authored')
        doc = TopoDoc(name='field', nodes=[existing, TopoNode(name='DOCK', x=0, y=0)])
        before = copy.deepcopy(existing.to_dict())
        build = build_row_nodes(straight_rows(2), ANCHOR, settings())

        apply_row_nodes(doc, build, 'FIELD', overwrite=False)

        self.assertEqual(existing.to_dict(), before)
        self.assertIs(doc.get_node(existing.name), existing)
        self.assertEqual(len(list(doc.nodes)), 5)
        self.assertTrue(doc.get_node('FIELD_R1_OUT').is_connected_to('FIELD_R2_IN'))

    def test_splice_does_not_backfill_edges_into_existing_node(self) -> None:
        """Verify splicing adds outgoing row links without changing the existing target node."""
        dock = TopoNode(name='DOCK', x=0, y=0)
        doc = TopoDoc(name='field', nodes=[dock])
        build = build_row_nodes(straight_rows(1), ANCHOR, settings(connect_to=dock.name))

        apply_row_nodes(doc, build, 'FIELD', overwrite=False)

        self.assertEqual(list(dock.edges), [])
        for name in ('FIELD_R1_IN', 'FIELD_R1_OUT'):
            self.assertEqual(targets(doc.get_node(name))['DOCK'], NAV_ACTION)


class TestRepairBoundaries(unittest.TestCase):
    def test_invalid_metadata_is_ignored(self) -> None:
        """Verify invalid row IDs and roles produce no repair plan or document changes."""
        for row_id, role in (
            (None, 'entry'), ('bad', 'exit'), ([], 'entry'), ({}, 'waypoint'), (1, 'dock'),
        ):
            with self.subTest(row_id=row_id, role=role):
                doc = TopoDoc(name='field', nodes=[TopoNode(
                    name='INVALID', x=0, y=0, meta={'row_id': row_id, 'row_role': role})])
                before = copy.deepcopy(doc.to_dict())
                self.assertIsNone(plan_row_repair(doc, ROW_ACTION))
                self.assertEqual(doc.to_dict(), before)

    def test_string_ids_and_unsuffixed_waypoints_form_a_chain_without_mutating_doc(self) -> None:
        """Verify repair accepts string IDs and orders unsuffixed waypoints before numbered ones."""
        doc = TopoDoc(name='field')
        for name, role, y in (
            ('OUT', 'exit', 30), ('ROW_W10', 'waypoint', 20),
            ('ROW_W2', 'waypoint', 10), ('SURVEYED', 'waypoint', 5), ('IN', 'entry', 0),
        ):
            doc.insert_node(TopoNode(name=name, x=0, y=y, meta={
                'row_id': '7', 'row_role': role,
            }))
        before = copy.deepcopy(doc.to_dict())

        plan = plan_row_repair(doc, 'limbic_row_follow')

        self.assertEqual(doc.to_dict(), before)
        self.assertEqual(plan.row_count, 1)
        self.assertEqual(plan.edges, (
            ('IN', 'SURVEYED', 'limbic_row_follow'),
            ('SURVEYED', 'ROW_W2', 'limbic_row_follow'),
            ('ROW_W2', 'ROW_W10', 'limbic_row_follow'),
            ('ROW_W10', 'OUT', 'limbic_row_follow'),
        ))

    def test_incomplete_rows_do_not_create_partial_row_chains(self) -> None:
        """Verify rows without both endpoints produce no partial chains or headland links."""
        for roles in (('waypoint',), ('entry', 'waypoint'), ('waypoint', 'exit')):
            with self.subTest(roles=roles):
                doc = TopoDoc(name='field', nodes=[
                    TopoNode(name=role, x=0, y=i, meta={'row_id': 1, 'row_role': role})
                    for i, role in enumerate(roles)
                ])
                plan = plan_row_repair(doc, ROW_ACTION)
                self.assertEqual(plan.row_count, 1)
                self.assertEqual(plan.edges, ())
                self.assertTrue(plan.headlands_skipped)

    def test_waypoints_far_from_endpoints_do_not_change_headland_connections(self) -> None:
        """Verify headland classification uses row endpoints even when waypoints are far away."""
        build = build_row_nodes(straight_rows(2), ANCHOR, settings())
        doc = TopoDoc(name='field', nodes=list(build.nodes.values()))
        doc.insert_node(TopoNode(name='FIELD_R1_W1', x=10000, y=10000, meta={
            'row_id': 1, 'row_role': 'waypoint',
        }))

        plan = plan_row_repair(doc, ROW_ACTION)

        self.assertEqual(set(plan.edges), {
            ('FIELD_R1_IN', 'FIELD_R1_W1', ROW_ACTION),
            ('FIELD_R1_W1', 'FIELD_R1_OUT', ROW_ACTION),
            ('FIELD_R2_IN', 'FIELD_R2_OUT', ROW_ACTION),
            ('FIELD_R1_IN', 'FIELD_R2_OUT', NAV_ACTION),
            ('FIELD_R2_OUT', 'FIELD_R1_IN', NAV_ACTION),
            ('FIELD_R1_OUT', 'FIELD_R2_IN', NAV_ACTION),
            ('FIELD_R2_IN', 'FIELD_R1_OUT', NAV_ACTION),
        })

    def test_edge_application_ignores_missing_sources_self_links_and_duplicates(self) -> None:
        """Verify edge application skips missing sources, self-links and duplicate targets."""
        original = TopoEdge(action='authored', edge_id='survey-edge', node='B')
        node = TopoNode(name='A', x=0, y=0, edges=[original])
        doc = TopoDoc(name='field', nodes=[node, TopoNode(name='B', x=0, y=1)])

        added = add_missing_edges(doc, (
            ('MISSING', 'A', ROW_ACTION), ('A', 'A', ROW_ACTION),
            ('A', 'B', ROW_ACTION), ('B', 'A', NAV_ACTION), ('B', 'A', ROW_ACTION),
        ))

        self.assertEqual(added, 1)
        self.assertEqual(list(node.edges), [original])
        self.assertEqual(targets(doc.get_node('B')), {'A': NAV_ACTION})


if __name__ == '__main__':
    unittest.main()
