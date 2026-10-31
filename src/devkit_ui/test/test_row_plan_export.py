"""Tests for turning topological map rows into a LiteFarm row plan."""
import math
import unittest
from importlib import import_module
from pathlib import Path

from devkit_ui.constants import NAV_ACTION, ROW_ACTION
from devkit_ui.models import TopoDoc, TopoEdge, TopoNode, TopoProperties
from devkit_ui.parse import parse_topo_yaml
from devkit_ui.row_plan_export import ROW_ROLES, export_row_plan
from devkit_ui.row_plan_format import PlanRow, RowPlanError, map_hash

MAIZE_MAP = Path(__file__).resolve().parents[3] / 'maps' / 'maize_map'
LAT0, LON0, EARTH_RADIUS_M = 51.5, -2.4, 6_378_137.0


def latlon(x: float, y: float) -> tuple[float, float]:
    """Return the (lat, lon) of a map-frame position, with the map frame east and north of (LAT0, LON0)."""
    return (LAT0 + math.degrees(y / EARTH_RADIUS_M),
            LON0 + math.degrees(x / (EARTH_RADIUS_M * math.cos(math.radians(LAT0)))))


def node(name, row, role, x, y, *, gps=True, edges=()):
    """Make a row node whose GPS position agrees with its map position."""
    lat, lon = latlon(x, y) if gps else (0.0, 0.0)
    return TopoNode(name=name, x=x, y=y, edges=list(edges),
                    properties=TopoProperties(row_id=row, row_role=role, gps_lat=lat, gps_lon=lon))


def edge(src, dst, action=ROW_ACTION):
    return TopoEdge(action=action, edge_id=f'{src}_{dst}', node=dst)


def simple_row(row, y, *, prefix='F'):
    """A 10 m row from x=0 to x=10 at north offset y."""
    return [node(f'{prefix}_R{row}_IN', row, 'entry', 0.0, y,
                 edges=[edge(f'{prefix}_R{row}_IN', f'{prefix}_R{row}_OUT')]),
            node(f'{prefix}_R{row}_OUT', row, 'exit', 10.0, y)]


class RealMapTest(unittest.TestCase):
    def setUp(self):
        self.doc = parse_topo_yaml(str(MAIZE_MAP))
        self.plan = export_row_plan(self.doc)

    def test_every_row_is_exported_in_row_order(self):
        self.assertEqual([r.row for r in self.plan.rows], [1, 2, 3, 4])
        self.assertEqual(self.plan.ignored_nodes, 0)

    def test_a_row_runs_from_entry_through_its_waypoint_to_exit(self):
        row2 = self.plan.rows[1]
        names = ['F2C_R2_IN', 'F2C_R2_W1', 'F2C_R2_OUT']
        self.assertEqual(row2.points, [(self.doc.get_node(n).properties.gps_lat,
                                        self.doc.get_node(n).properties.gps_lon) for n in names])

    def test_map_and_gps_agree(self):
        self.assertEqual(self.plan.warnings, [])

    def test_geojson_hash_is_the_hash_of_the_coordinates_in_the_file(self):
        coordinates = [f['geometry']['coordinates'] for f in self.plan.geojson['features']]
        rows = [PlanRow(r.row, [(lat, lon) for lon, lat in c])
                for r, c in zip(self.plan.rows, coordinates, strict=True)]
        self.assertEqual(self.plan.map_hash, map_hash(rows))
        self.assertEqual(self.plan.geojson['properties']['origin'], list(self.plan.rows[0].points[0]))

    def test_map_id_names_the_map_and_the_rows(self):
        self.assertEqual(self.plan.map_id, f'maize_map-{self.plan.map_hash[:12]}')
        self.assertEqual(export_row_plan(self.doc).map_id, self.plan.map_id)


class RowOrderTest(unittest.TestCase):
    def test_rows_are_in_numeric_order_not_text_order(self):
        doc = TopoDoc('f', nodes=[n for r, y in ((10, 0.0), (2, 3.0), (9, 6.0)) for n in simple_row(r, y)])
        self.assertEqual([r.row for r in export_row_plan(doc).rows], [2, 9, 10])

    def test_row_id_given_as_text_is_read(self):
        doc = TopoDoc('f', nodes=simple_row('3', 0.0))
        self.assertEqual([r.row for r in export_row_plan(doc).rows], [3])

    def test_waypoints_follow_the_row_edges(self):
        # Names would sort the other way round, so this only passes if the edges decide.
        doc = TopoDoc('f', nodes=[
            node('IN', 1, 'entry', 0, 0, edges=[edge('IN', 'B'), edge('IN', 'OTHER', NAV_ACTION)]),
            node('B', 1, 'waypoint', 3, 1, edges=[edge('B', 'A')]),
            node('A', 1, 'waypoint', 6, 1, edges=[edge('A', 'OUT')]),
            node('OUT', 1, 'exit', 10, 0),
        ])
        lons = [lon for _, lon in export_row_plan(doc).rows[0].points]
        self.assertEqual(lons, [latlon(x, 0)[1] for x in (0, 3, 6, 10)])

    def test_waypoints_without_edges_go_by_the_number_in_their_name(self):
        doc = TopoDoc('f', nodes=[
            node('F_R1_IN', 1, 'entry', 0, 0), node('F_R1_W10', 1, 'waypoint', 8, 0),
            node('F_R1_W2', 1, 'waypoint', 4, 0), node('F_R1_W1', 1, 'waypoint', 2, 0),
            node('F_R1_OUT', 1, 'exit', 10, 0),
        ])
        lons = [lon for _, lon in export_row_plan(doc).rows[0].points]
        self.assertEqual(lons, [latlon(x, 0)[1] for x in (0, 2, 4, 8, 10)])

    def test_headland_edges_to_other_rows_do_not_change_the_order(self):
        first, second = simple_row(1, 0.0), simple_row(2, 3.0)
        first[1].add_edge(edge('F_R1_OUT', 'F_R2_OUT', NAV_ACTION))
        first[0].add_edge(edge('F_R1_IN', 'F_R2_IN', NAV_ACTION))
        plan = export_row_plan(TopoDoc('f', nodes=[*first, *second]))
        self.assertEqual([len(r.points) for r in plan.rows], [2, 2])


class RefusalTest(unittest.TestCase):
    def assert_refused(self, nodes, *words):
        with self.assertRaises(RowPlanError) as caught:
            export_row_plan(TopoDoc('f', nodes=nodes))
        for word in words:
            self.assertIn(word, str(caught.exception))

    def test_a_map_without_rows(self):
        self.assert_refused([node('P1', '', 'waypoint', 0, 0)], 'no row nodes')
        self.assert_refused([], 'no row nodes')

    def test_a_row_without_an_exit(self):
        self.assert_refused(simple_row(1, 0.0)[:1], 'row 1', 'one entry and one exit', '1 and 0')

    def test_a_row_with_two_entries(self):
        self.assert_refused([*simple_row(1, 0.0), node('EXTRA', 1, 'entry', 1, 1)], 'row 1', '2 and 1')

    def test_a_node_without_a_gps_position(self):
        rows = [*simple_row(1, 0.0), node('F_R2_IN', 2, 'entry', 0, 3, gps=False), node('F_R2_OUT', 2, 'exit', 10, 3)]
        self.assert_refused(rows, 'row 2', 'no GPS position', 'F_R2_IN')

    def test_a_map_id_litefarm_would_refuse(self):
        with self.assertRaises(RowPlanError):
            export_row_plan(TopoDoc('f', nodes=simple_row(1, 0.0)), map_id='has space')

    def test_a_row_that_starts_and_ends_in_the_same_place(self):
        self.assert_refused([node('IN', 1, 'entry', 0, 0), node('OUT', 1, 'exit', 0, 0)], 'row 1', 'same place')


class IgnoredNodesTest(unittest.TestCase):
    def test_nodes_that_are_not_rows_are_left_out_and_counted(self):
        doc = TopoDoc('f', nodes=[*simple_row(1, 0.0), node('HOME', '', '', 5, 5),
                                  node('GATE', '', 'waypoint', 6, 6), node('ODD', 'x', 'entry', 7, 7)])
        plan = export_row_plan(doc)
        self.assertEqual((len(plan.rows), plan.ignored_nodes), (1, 3))


class MapIdTest(unittest.TestCase):
    def doc(self, x_out=10.0, name='field'):
        entry = simple_row(1, 0.0)[0]
        return TopoDoc(name, nodes=[entry, node('F_R1_OUT', 1, 'exit', x_out, 0.0)])

    def test_same_rows_same_id_and_hash(self):
        first, second = export_row_plan(self.doc()), export_row_plan(self.doc())
        self.assertEqual((first.map_id, first.map_hash), (second.map_id, second.map_hash))
        self.assertEqual(first.geojson['properties']['inputs_hash'], second.geojson['properties']['inputs_hash'])

    def test_changed_rows_get_a_new_id_and_hash(self):
        first, second = export_row_plan(self.doc()), export_row_plan(self.doc(x_out=12.0))
        self.assertNotEqual(first.map_id, second.map_id)
        self.assertNotEqual(first.map_hash, second.map_hash)
        self.assertNotEqual(first.geojson['properties']['inputs_hash'], second.geojson['properties']['inputs_hash'])

    def test_the_map_name_is_made_safe(self):
        plan = export_row_plan(self.doc(name='my map/2026!'))
        self.assertRegex(plan.map_id, r'^my_map_2026_-[0-9a-f]{12}$')

    def test_a_given_id_and_supersedes_are_kept(self):
        plan = export_row_plan(self.doc(), map_id='north-2', supersedes='north-1', field_location_id='abc')
        props = plan.geojson['properties']
        self.assertEqual((props['map_id'], props['supersedes'], props['field_location_id']),
                         ('north-2', 'north-1', 'abc'))

    def test_inputs_hash_is_a_sha256(self):
        self.assertRegex(export_row_plan(self.doc()).geojson['properties']['inputs_hash'], r'^[0-9a-f]{64}$')


class WarningTest(unittest.TestCase):
    def test_a_row_whose_map_length_and_gps_length_disagree(self):
        entry = simple_row(1, 0.0)[0]
        far = TopoNode(name='F_R1_OUT', x=10.0, y=0.0, properties=TopoProperties(
            row_id=1, row_role='exit', gps_lat=latlon(60.0, 0.0)[0], gps_lon=latlon(60.0, 0.0)[1]))
        plan = export_row_plan(TopoDoc('f', nodes=[entry, far]))
        self.assertEqual(len(plan.warnings), 1)
        self.assertIn('row 1', plan.warnings[0])
        self.assertIn('10.0 m on the map', plan.warnings[0])
        self.assertRegex(plan.warnings[0], r'but (59\.9|60\.0) m from GPS$')

    def test_small_differences_are_not_reported(self):
        self.assertEqual(export_row_plan(TopoDoc('f', nodes=simple_row(1, 0.0))).warnings, [])


class RolesTest(unittest.TestCase):
    def test_roles_are_the_ones_the_row_builder_writes(self):
        try:
            builder = import_module('devkit_ui.topo_row_builder')
        except ModuleNotFoundError:
            self.skipTest('the Fields2Cover planner is not installed')
        self.assertEqual(ROW_ROLES, builder.ROW_ROLES)


if __name__ == '__main__':
    unittest.main()
