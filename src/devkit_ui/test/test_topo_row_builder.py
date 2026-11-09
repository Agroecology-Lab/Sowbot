import math
import unittest
from itertools import pairwise
from pathlib import Path
from tempfile import TemporaryDirectory

from devkit_f2c_planner.f2c_planner import f2c_xy_to_latlon
from devkit_ui.constants import NAV_ACTION, ROW_ACTION
from devkit_ui.models import TopoDoc
from devkit_ui.parse import dump_topo_yaml, parse_topo_yaml
from devkit_ui.topo_row_builder import (
    RowAnchor,
    RowBuildSettings,
    add_missing_edges,
    apply_row_nodes,
    build_row_nodes,
    plan_row_repair,
)

ANCHOR = RowAnchor(lat=51.0, lon=-2.0, x=10.0, y=20.0)


def swath(*points_xy: tuple[float, float]) -> list[tuple[float, float]]:
    """Build a lat/lon swath from local xy points around the test anchor."""
    return [f2c_xy_to_latlon(x, y, ANCHOR.lat, ANCHOR.lon) for x, y in points_xy]


def straight_rows(count: int, *, spacing: float = 3.0, length: float = 50.0) -> list:
    """Return snake-ordered straight swaths running along y, one per row."""
    rows = []
    for i in range(count):
        ends = (0.0, length) if i % 2 == 0 else (length, 0.0)
        rows.append(swath((i * spacing, ends[0]), (i * spacing, ends[1])))
    return rows


def settings(**overrides) -> RowBuildSettings:
    values = {'prefix': 'FIELD', 'row_id_start': 1, 'row_action': ROW_ACTION,
              'nav_frame': 'map', 'map_name': 'field', 'timestamp': '01-01-2026_00-00-00',
              'fix_type': 2}
    values.update(overrides)
    return RowBuildSettings(**values)


def targets(node) -> dict[str, str]:
    """Return a node's outgoing edges as {target: action}."""
    return {edge.node: edge.action for edge in node.edges}


class TestBuildRowNodes(unittest.TestCase):
    def test_each_row_gets_entry_and_exit_nodes_with_row_identity(self) -> None:
        """Verify nodes are named per row and carry role and id in properties and metadata."""
        build = build_row_nodes(straight_rows(2), ANCHOR, settings())

        self.assertEqual(
            set(build.nodes), {'FIELD_R1_IN', 'FIELD_R1_OUT', 'FIELD_R2_IN', 'FIELD_R2_OUT'})
        self.assertEqual(build.added, [1, 2])
        entry = build.nodes['FIELD_R1_IN']
        self.assertEqual(entry.meta['row_id'], 1)
        self.assertEqual(entry.meta['row_role'], 'entry')
        self.assertEqual(build.nodes['FIELD_R1_OUT'].meta['row_role'], 'exit')

    def test_litefarm_identity_is_written_to_every_node_of_the_row(self) -> None:
        """Verify entry, waypoint and exit nodes carry lf_field, lf_row and lf_frag."""
        build = build_row_nodes(straight_rows(2), ANCHOR, settings(
            contour_used=True, lf_rows=((3, 12, 0), (3, 12, 1))))

        for name, lf in (('FIELD_R1_IN', (3, 12, 0)), ('FIELD_R1_W1', (3, 12, 0)),
                         ('FIELD_R1_OUT', (3, 12, 0)), ('FIELD_R2_IN', (3, 12, 1)),
                         ('FIELD_R2_OUT', (3, 12, 1))):
            with self.subTest(name):
                meta = build.nodes[name].meta
                self.assertEqual((meta['lf_field'], meta['lf_row'], meta['lf_frag']), lf)

    def test_nodes_have_no_litefarm_keys_without_lf_rows(self) -> None:
        """Verify a plan with no LiteFarm identity adds no lf_* metadata."""
        build = build_row_nodes(straight_rows(1), ANCHOR, settings())

        self.assertFalse([key for key in build.nodes['FIELD_R1_IN'].meta if key.startswith('lf_')])

    def test_lf_rows_must_match_the_swaths(self) -> None:
        """Verify a different count of LiteFarm rows and swaths is refused."""
        with self.assertRaisesRegex(ValueError, '1 LiteFarm rows for 2 swaths'):
            build_row_nodes(straight_rows(2), ANCHOR, settings(lf_rows=((1, 0, 0),)))

    def test_litefarm_identity_survives_a_yaml_round_trip(self) -> None:
        """Verify lf_* metadata is kept when the map is written and read back."""
        build = build_row_nodes(straight_rows(1), ANCHOR, settings(lf_rows=((3, 12, 1),)))
        doc = TopoDoc(name='field')
        apply_row_nodes(doc, build, 'FIELD', False)

        with TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'topo.yaml')
            dump_topo_yaml(doc, path)
            restored = parse_topo_yaml(path)

        meta = next(n for n in restored.nodes if n.name == 'FIELD_R1_IN').meta
        self.assertEqual((meta['lf_field'], meta['lf_row'], meta['lf_frag']), (3, 12, 1))

    def test_positions_are_offset_by_the_anchor_pose(self) -> None:
        """Verify local xy is added to the anchor's map position and rounded to millimetres."""
        build = build_row_nodes(straight_rows(1), ANCHOR, settings())

        self.assertAlmostEqual(build.nodes['FIELD_R1_IN'].x, 10.0, places=3)
        self.assertAlmostEqual(build.nodes['FIELD_R1_IN'].y, 20.0, places=3)
        self.assertAlmostEqual(build.nodes['FIELD_R1_OUT'].y, 70.0, places=3)

    def test_straight_rows_use_one_row_edge_and_no_waypoints(self) -> None:
        """Verify a row without contour mode is a single entry-to-exit edge."""
        build = build_row_nodes(straight_rows(1), ANCHOR, settings())

        self.assertEqual(targets(build.nodes['FIELD_R1_IN']), {'FIELD_R1_OUT': ROW_ACTION})
        self.assertEqual(targets(build.nodes['FIELD_R1_OUT']), {'FIELD_R1_IN': ROW_ACTION})

    def test_default_interval_places_waypoints_every_three_metres(self) -> None:
        """Verify the default spacing is 3 m of arc length, for two-point swaths too."""
        build = build_row_nodes(
            [swath((0.0, 0.0), (0.0, 10.0))], ANCHOR, settings(contour_used=True))

        waypoints = [build.nodes[f'FIELD_R1_W{k}'] for k in (1, 2, 3)]
        self.assertNotIn('FIELD_R1_W4', build.nodes)
        self.assertEqual([round(w.y - ANCHOR.y, 3) for w in waypoints], [3.0, 6.0, 9.0])

    def test_two_point_contour_row_longer_than_one_interval_gets_a_waypoint(self) -> None:
        """Verify a 3.96 m two-point row keeps one waypoint instead of a single edge."""
        build = build_row_nodes(
            [swath((0.0, 0.0), (0.0, 3.96))], ANCHOR, settings(contour_used=True))

        self.assertEqual(
            targets(build.nodes['FIELD_R1_IN']), {'FIELD_R1_W1': ROW_ACTION})

    def test_every_generated_edge_has_a_reverse_edge(self) -> None:
        """Verify in-row and headland edges are all bidirectional, for straight and contour rows."""
        curve = swath((0.0, 0.0), (0.0, 12.0), (5.0, 24.0))
        for rows, contour in ((straight_rows(3), False), ([curve, curve, curve], True)):
            with self.subTest(contour=contour):
                build = build_row_nodes(rows, ANCHOR, settings(
                    contour_used=contour, waypoint_interval_m=10.0))
                pairs = {(n.name, e.node): e.action for n in build.nodes.values() for e in n.edges}
                self.assertTrue(pairs)
                for (src, dst), action in pairs.items():
                    self.assertEqual(pairs.get((dst, src)), action, (src, dst))

    def test_contour_rows_chain_through_waypoints_in_order(self) -> None:
        """Verify a contour row is chained entry, W1, W2... exit using the row action."""
        curve = swath((0.0, 0.0), (0.0, 12.0), (5.0, 24.0))
        build = build_row_nodes([curve], ANCHOR, settings(
            contour_used=True, waypoint_interval_m=10.0, row_action='limbic_row_follow'))

        names = [name for name in build.nodes if '_W' in name]
        self.assertGreaterEqual(len(names), 1)
        self.assertTrue(all(build.nodes[n].meta['row_role'] == 'waypoint' for n in names))
        chain = ['FIELD_R1_IN', *names, 'FIELD_R1_OUT']
        for a, b in pairwise(chain):
            self.assertEqual(targets(build.nodes[a]).get(b), 'limbic_row_follow')
            self.assertEqual(targets(build.nodes[b]).get(a), 'limbic_row_follow')

    def test_headland_edges_join_row_exit_to_next_row_entry_both_ways(self) -> None:
        """Verify consecutive rows are linked OUT to next IN with navigation edges."""
        build = build_row_nodes(straight_rows(2), ANCHOR, settings())

        self.assertEqual(targets(build.nodes['FIELD_R1_OUT'])['FIELD_R2_IN'], NAV_ACTION)
        self.assertEqual(targets(build.nodes['FIELD_R2_IN'])['FIELD_R1_OUT'], NAV_ACTION)

    def test_break_after_stops_the_link_to_the_next_row(self) -> None:
        """Verify a row listed in break_after is not linked to the row after it."""
        build = build_row_nodes(
            straight_rows(3), ANCHOR, settings(break_after=frozenset({0})))

        self.assertNotIn('FIELD_R2_IN', targets(build.nodes['FIELD_R1_OUT']))
        self.assertIn('FIELD_R3_IN', targets(build.nodes['FIELD_R2_OUT']))

    def test_connect_to_links_first_entry_and_last_exit(self) -> None:
        """Verify splicing links the first IN and last OUT to the chosen node."""
        build = build_row_nodes(straight_rows(2), ANCHOR, settings(connect_to='DOCK'))

        self.assertEqual(targets(build.nodes['FIELD_R1_IN'])['DOCK'], NAV_ACTION)
        self.assertEqual(targets(build.nodes['FIELD_R2_OUT'])['DOCK'], NAV_ACTION)
        self.assertNotIn('DOCK', targets(build.nodes['FIELD_R1_OUT']))

    def test_short_swaths_are_ignored_but_keep_their_row_id_slot(self) -> None:
        """Verify a one-point swath adds nothing and later rows keep index-based ids."""
        rows = [swath((0.0, 0.0)), *straight_rows(1)]
        build = build_row_nodes(rows, ANCHOR, settings())

        self.assertEqual(build.added, [2])
        self.assertIn('FIELD_R2_IN', build.nodes)

    def test_no_usable_swaths_gives_an_empty_build(self) -> None:
        """Verify there are no nodes or rows when nothing can be built."""
        build = build_row_nodes([swath((0.0, 0.0))], ANCHOR, settings())

        self.assertEqual((build.nodes, build.added), ({}, []))


def yaw_of(node) -> float:
    """Return a node's pose yaw in radians, from its quaternion."""
    q = node.to_dict()['node']['pose']['orientation']
    return 2.0 * math.atan2(q['z'], q['w'])


class TestRowOrientation(unittest.TestCase):
    def test_row_nodes_face_along_the_row_from_in_to_out(self) -> None:
        """Verify rows along -x face -x at both ends, so the end goal asks for no spin."""
        build = build_row_nodes([swath((0.0, 0.0), (-12.0, 0.0))], ANCHOR, settings())

        for name in ('FIELD_R1_IN', 'FIELD_R1_OUT'):
            self.assertAlmostEqual(abs(yaw_of(build.nodes[name])), math.pi, places=5)

    def test_snake_ordered_rows_face_opposite_ways(self) -> None:
        """Verify the heading follows each swath's own direction, not a fixed axis."""
        build = build_row_nodes(straight_rows(2), ANCHOR, settings())

        self.assertAlmostEqual(yaw_of(build.nodes['FIELD_R1_OUT']), math.pi / 2, places=5)
        self.assertAlmostEqual(yaw_of(build.nodes['FIELD_R2_IN']), -math.pi / 2, places=5)
        self.assertAlmostEqual(yaw_of(build.nodes['FIELD_R2_OUT']), -math.pi / 2, places=5)

    def test_row_along_plus_x_keeps_the_identity_orientation(self) -> None:
        build = build_row_nodes([swath((0.0, 0.0), (12.0, 0.0))], ANCHOR, settings())

        for node in build.nodes.values():
            self.assertEqual(node.to_dict()['node']['pose']['orientation'],
                             {'x': 0.0, 'y': 0.0, 'z': 0.0, 'w': 1.0})

    def test_contour_waypoints_face_the_next_node_and_the_exit_keeps_the_last_heading(self) -> None:
        """Verify a bent row turns its heading at the bend instead of using one end-to-end bearing."""
        build = build_row_nodes([swath((0.0, 0.0), (9.0, 0.0), (9.0, 9.0))], ANCHOR,
                                settings(contour_used=True))

        chain = ['FIELD_R1_IN', *sorted((n for n in build.nodes if '_W' in n),
                                        key=lambda n: int(n.rsplit('W', 1)[1])), 'FIELD_R1_OUT']
        for here, there in pairwise(chain):
            a, b = build.nodes[here], build.nodes[there]
            self.assertAlmostEqual(yaw_of(a), math.atan2(b.y - a.y, b.x - a.x), places=4)
        self.assertAlmostEqual(yaw_of(build.nodes[chain[-1]]), yaw_of(build.nodes[chain[-2]]), places=4)
        self.assertAlmostEqual(yaw_of(build.nodes[chain[0]]), 0.0, places=4)
        self.assertAlmostEqual(yaw_of(build.nodes[chain[-1]]), math.pi / 2, places=4)

    def test_orientation_survives_a_yaml_round_trip(self) -> None:
        """Verify a saved and reloaded map keeps the headings instead of resetting them."""
        build = build_row_nodes([swath((0.0, 0.0), (-12.0, 0.0))], ANCHOR, settings())
        doc = TopoDoc(name='field')
        apply_row_nodes(doc, build, 'FIELD', overwrite=False)

        with TemporaryDirectory() as folder:
            path = Path(folder) / 'field.tmap2.yaml'
            dump_topo_yaml(doc, str(path))
            loaded = parse_topo_yaml(str(path))

        for node in doc.nodes:
            self.assertEqual(loaded.get_node(node.name).to_dict()['node']['pose'],
                             node.to_dict()['node']['pose'])


class TestApplyRowNodes(unittest.TestCase):
    def test_nodes_are_inserted_into_the_document(self) -> None:
        """Verify generated nodes and their edges end up in the document."""
        doc = TopoDoc(name='field')
        build = build_row_nodes(straight_rows(2), ANCHOR, settings())

        apply_row_nodes(doc, build, 'FIELD', overwrite=False)

        self.assertEqual({n.name for n in doc.nodes}, set(build.nodes))
        self.assertTrue(doc.get_node('FIELD_R1_OUT').is_connected_to('FIELD_R2_IN'))

    def test_overwrite_replaces_nodes_with_the_prefix_only(self) -> None:
        """Verify overwrite removes old prefix rows, keeps other nodes and prunes dangling edges."""
        doc = TopoDoc(name='field')
        apply_row_nodes(doc, build_row_nodes(straight_rows(3), ANCHOR, settings()), 'FIELD', False)
        apply_row_nodes(doc, build_row_nodes(
            straight_rows(1), ANCHOR, settings(prefix='OTHER')), 'OTHER', False)

        apply_row_nodes(doc, build_row_nodes(
            straight_rows(1), ANCHOR, settings(overwrite=True)), 'FIELD', overwrite=True)

        names = {n.name for n in doc.nodes}
        self.assertEqual(names, {'FIELD_R1_IN', 'FIELD_R1_OUT', 'OTHER_R1_IN', 'OTHER_R1_OUT'})

    def test_existing_names_are_kept_without_overwrite(self) -> None:
        """Verify applying the same build twice does not duplicate or replace nodes."""
        doc = TopoDoc(name='field')
        build = build_row_nodes(straight_rows(1), ANCHOR, settings())
        apply_row_nodes(doc, build, 'FIELD', overwrite=False)
        before = doc.get_node('FIELD_R1_IN')

        apply_row_nodes(doc, build_row_nodes(straight_rows(1), ANCHOR, settings()),
                        'FIELD', overwrite=False)

        self.assertIs(doc.get_node('FIELD_R1_IN'), before)
        self.assertEqual(len(list(doc.nodes)), 2)


class TestRowRepair(unittest.TestCase):
    def doc_without_edges(self, rows: list, **overrides) -> TopoDoc:
        """Build a document of row nodes whose edges have all been lost."""
        doc = TopoDoc(name='field')
        build = build_row_nodes(rows, ANCHOR, settings(**overrides))
        apply_row_nodes(doc, build, 'FIELD', overwrite=False)
        for node in doc.nodes:
            for edge in list(node.edges):
                node.remove_edge(edge.node)
        return doc

    def test_map_without_rows_has_nothing_to_repair(self) -> None:
        """Verify a document with no row metadata gives no plan."""
        self.assertIsNone(plan_row_repair(TopoDoc(name='field'), ROW_ACTION))

    def test_repair_restores_in_row_and_headland_edges(self) -> None:
        """Verify lost row and same-end headland edges are all restored."""
        doc = self.doc_without_edges(straight_rows(2))
        plan = plan_row_repair(doc, ROW_ACTION)

        self.assertEqual(plan.row_count, 2)
        self.assertFalse(plan.headlands_skipped)
        self.assertEqual(add_missing_edges(doc, plan.edges), 8)
        self.assertEqual(targets(doc.get_node('FIELD_R1_IN'))['FIELD_R1_OUT'], ROW_ACTION)
        self.assertEqual(targets(doc.get_node('FIELD_R1_OUT'))['FIELD_R1_IN'], ROW_ACTION)
        # Rows are snaked, so R1_OUT (y=50) shares its end with R2_IN (y=50).
        self.assertEqual(targets(doc.get_node('FIELD_R1_OUT'))['FIELD_R2_IN'], NAV_ACTION)
        self.assertEqual(targets(doc.get_node('FIELD_R2_IN'))['FIELD_R1_OUT'], NAV_ACTION)

    def test_repair_is_idempotent(self) -> None:
        """Verify a second repair adds nothing."""
        doc = self.doc_without_edges(straight_rows(2))
        plan = plan_row_repair(doc, ROW_ACTION)
        add_missing_edges(doc, plan.edges)

        self.assertEqual(add_missing_edges(doc, plan.edges), 0)

    def test_repair_threads_waypoints_in_numeric_order(self) -> None:
        """Verify W2 comes before W10, so the curve is not reordered by name."""
        doc = TopoDoc(name='field')
        curve = swath((0.0, 0.0), (0.0, 120.0), (30.0, 240.0))
        build = build_row_nodes([curve], ANCHOR, settings(
            contour_used=True, waypoint_interval_m=10.0))
        apply_row_nodes(doc, build, 'FIELD', overwrite=False)
        for node in doc.nodes:
            for edge in list(node.edges):
                node.remove_edge(edge.node)
        waypoints = sorted((n for n in build.nodes if '_W' in n), key=lambda n: int(n.split('_W')[1]))
        self.assertGreaterEqual(len(waypoints), 10)

        add_missing_edges(doc, plan_row_repair(doc, ROW_ACTION).edges)

        chain = ['FIELD_R1_IN', *waypoints, 'FIELD_R1_OUT']
        for a, b in pairwise(chain):
            self.assertEqual(targets(doc.get_node(a)).get(b), ROW_ACTION, (a, b))
            self.assertEqual(targets(doc.get_node(b)).get(a), ROW_ACTION, (b, a))

    def test_existing_edge_actions_are_not_changed(self) -> None:
        """Verify an edge that already exists keeps its action when the row action differs."""
        doc = self.doc_without_edges(straight_rows(1))
        doc.get_node('FIELD_R1_IN').add_edge('FIELD_R1_OUT', action='limbic_row_follow')

        plan = plan_row_repair(doc, ROW_ACTION)
        add_missing_edges(doc, plan.edges)

        self.assertEqual(
            targets(doc.get_node('FIELD_R1_IN'))['FIELD_R1_OUT'], 'limbic_row_follow')

    def test_single_row_needs_no_headland_edges(self) -> None:
        """Verify one row restores only its in-row edges, both ways, since it has no neighbours."""
        doc = self.doc_without_edges(straight_rows(1))
        plan = plan_row_repair(doc, ROW_ACTION)

        self.assertFalse(plan.headlands_skipped)
        self.assertEqual(plan.edges, (
            ('FIELD_R1_IN', 'FIELD_R1_OUT', ROW_ACTION),
            ('FIELD_R1_OUT', 'FIELD_R1_IN', ROW_ACTION),
        ))

    def test_headlands_are_reported_skipped_with_fewer_than_two_row_ends(self) -> None:
        """Verify a lone row end cannot be classified and the plan says so."""
        doc = self.doc_without_edges(straight_rows(1))
        doc.remove_nodes({'FIELD_R1_OUT'})

        plan = plan_row_repair(doc, ROW_ACTION)

        self.assertTrue(plan.headlands_skipped)
        self.assertEqual(plan.edges, ())


if __name__ == '__main__':
    unittest.main()
