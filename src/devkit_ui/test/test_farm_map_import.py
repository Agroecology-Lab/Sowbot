"""Tests for reading a LiteFarm farm map. tests/farm-map.geojson is LiteFarm's own export of its test fixture."""
import copy
import json
import unittest
from pathlib import Path

from devkit_ui.farm_map_import import (
    FarmDock,
    FarmMapError,
    FarmRoute,
    FieldRows,
    canonical_features_text,
    farm_map_hash,
    looks_like_farm_map,
    parse_farm_map,
)
from devkit_ui.row_plan_format import PlanRow

FIXTURE = Path(__file__).resolve().parents[3] / 'tests' / 'farm-map.geojson'
# From LiteFarm tests/utils-tests/farmMapFixture.test.js. If the two sides disagree, a published map would be refused.
FIXTURE_HASH = 'c50f60d85849c514ef0cab4dc65b2417cd03ccb0acf624d2387c35cc77212eb8'


def load() -> dict:
    """Return the fixture as a fresh dict."""
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


DOCKS = [FarmDock('dock', 48.0039, 3.6615, 1.0), FarmDock('dock1', 48.0038, 3.6614)]
ROUTES = [FarmRoute('route1', 'dock', 'F1R0N0', [(48.0039, 3.6615), (48.0040, 3.6617), (48.0041, 3.6619)])]
# From LiteFarm's computeFarmMapHash for one field, row 0 (48.0, 3.66) to (48.0, 3.661), and DOCKS and ROUTES.
DOCKS_HASH = '08cad4e963a19ebc8abf3fa58edb96e3d34e72e063653c4cc688aad4f5120194'
ROWS_ONLY_HASH = '8ff7d39a783d26c225de3ad433d8159cc1b764cd3579a771bb5ac2375c8edae8'


def with_docks(doc: dict) -> dict:
    """Return doc with DOCKS and ROUTES added and its content_hash recomputed, as LiteFarm publishes it."""
    meta = {'farm_map_id': doc['properties']['farm_map_id']}
    for dock in DOCKS:
        doc['features'].append({'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [dock.lon, dock.lat]},
                                'properties': {'role': 'dock', 'name': dock.name, 'heading_deg': dock.heading_deg,
                                               **meta}})
    for route in ROUTES:
        coordinates = [[lon, lat] for lat, lon in route.points]
        doc['features'].append({'type': 'Feature', 'geometry': {'type': 'LineString', 'coordinates': coordinates},
                                'properties': {'role': 'route', 'name': route.name, 'from_node': route.from_node,
                                               'to_node': route.to_node, **meta}})
    return resign(doc, DOCKS, ROUTES)


def resign(doc: dict, docks: list[FarmDock], routes: list[FarmRoute]) -> dict:
    """Set doc's content_hash to the hash of the fixture rows with these docks and routes."""
    doc['properties']['content_hash'] = farm_map_hash(parse(load()).fields, docks, routes)
    return doc


def parse(doc: dict, **kwargs):
    """Parse a dict as a farm map file."""
    return parse_farm_map(json.dumps(doc), **kwargs)


class FarmMapImportTest(unittest.TestCase):
    """The robot accepts exactly the maps LiteFarm published, and nothing altered."""

    def test_fixture_hash_matches_litefarm(self):
        """The hash computed here is the one LiteFarm wrote, to the last digit."""
        farm_map = parse(load())
        self.assertEqual(farm_map.content_hash, FIXTURE_HASH)
        self.assertEqual(farm_map_hash(farm_map.fields), FIXTURE_HASH)
        self.assertEqual(farm_map.hash8, 'c50f60d8')

    def test_map_description(self):
        """Map id, version, previous map and fields are read."""
        farm_map = parse(load())
        self.assertEqual(farm_map.farm_map_id, 'cccccccc-cccc-4ccc-8ccc-cccccccccccc')
        self.assertEqual(farm_map.version, 2)
        self.assertEqual(farm_map.previous_farm_map_id, 'dddddddd-dddd-4ddd-8ddd-dddddddddddd')
        self.assertEqual([f.field_number for f in farm_map.fields], [1, 2])
        self.assertEqual([f.name for f in farm_map.fields], ['North field', 'South field'])
        self.assertEqual(farm_map.row_count, 6)

    def test_fragments_keep_their_row_and_frag_numbers(self):
        """A split row stays two fragments of one row, so a (field, row) lookup can find both."""
        farm_map = parse(load())
        self.assertEqual([(f.field_number, r.row, r.frag) for f in farm_map.fields for r in f.rows],
                         [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 2, 0), (2, 0, 0), (2, 1, 0)])

    def test_swaths_are_lat_lon_in_mission_order(self):
        """GeoJSON [lon, lat] becomes (lat, lon), and the file order is kept."""
        swaths = parse(load()).swaths()
        self.assertEqual(len(swaths), 6)
        self.assertEqual(swaths[0], [(48.0041, 3.6619), (48.0041, 3.6624)])
        self.assertEqual(len(swaths[3]), 4)

    def test_no_headland_edge_across_a_field_or_a_fragment(self):
        """The last row of field 1 and the first fragment of row 1 are not joined to what follows."""
        self.assertEqual(parse(load()).break_after(), {1, 3})

    def test_origin_and_contour_come_from_the_fields(self):
        """Field 1 is a contour plan, so the map is built with waypoints."""
        farm_map = parse(load())
        self.assertTrue(farm_map.contour)
        self.assertEqual(farm_map.origin, (48.004066, 3.661831))

    def test_looks_like_farm_map(self):
        """A farm map is told from a single-field plan by its farm_map_id."""
        self.assertTrue(looks_like_farm_map(json.dumps(load())))
        self.assertFalse(looks_like_farm_map('{"type": "FeatureCollection", "properties": {"version": 1}}'))
        self.assertFalse(looks_like_farm_map('not json'))

    def test_moved_vertex_is_refused(self):
        """A changed coordinate no longer matches the content hash."""
        doc = load()
        rows = [f for f in doc['features'] if f['properties'].get('role') == 'row']
        rows[3]['geometry']['coordinates'][1][1] += 0.0000001
        with self.assertRaisesRegex(FarmMapError, 'content_hash does not match'):
            parse(doc)

    def test_reordered_rows_are_refused(self):
        """Mission order is part of the hash."""
        doc = load()
        idx = [i for i, f in enumerate(doc['features']) if f['properties'].get('role') == 'row']
        doc['features'][idx[0]], doc['features'][idx[1]] = doc['features'][idx[1]], doc['features'][idx[0]]
        with self.assertRaises(FarmMapError):
            parse(doc)

    def test_renumbered_field_is_refused(self):
        """Field numbers are part of the hash, and rows must belong to a listed field."""
        doc = load()
        for f in doc['features']:
            if f['properties'].get('role') == 'row' and f['properties'].get('field') == 2:
                f['properties']['field'] = 3
                f['properties']['name'] = f"F3R{f['properties']['row']}"
        with self.assertRaisesRegex(FarmMapError, 'not listed'):
            parse(doc)

    def test_declared_hash_must_be_well_formed(self):
        """A missing or short hash is refused before rows are read."""
        for value in (None, 'abc', FIXTURE_HASH.upper()):
            doc = load()
            doc['properties']['content_hash'] = value
            with self.assertRaisesRegex(FarmMapError, 'content_hash'):
                parse(doc)

    def test_retired_map_is_refused_unless_allowed(self):
        """A retired map has been replaced, so a robot does not run it by default."""
        doc = load()
        doc['properties']['status'] = 'retired'
        with self.assertRaisesRegex(FarmMapError, 'only a published map'):
            parse(doc)
        self.assertEqual(parse(doc, accept_retired=True).status, 'retired')

    def test_draft_map_is_refused(self):
        """A draft is never exported, and is never accepted."""
        doc = load()
        doc['properties']['status'] = 'draft'
        with self.assertRaises(FarmMapError):
            parse(doc, accept_retired=True)

    def test_single_field_plan_is_not_a_farm_map(self):
        """A plan without a farm_map_id is sent to the single-plan importer instead."""
        doc = load()
        del doc['properties']['farm_map_id']
        with self.assertRaisesRegex(FarmMapError, 'not a farm map'):
            parse(doc)

    def test_row_from_another_map_is_refused(self):
        """Every row must name this map."""
        doc = load()
        next(f for f in doc['features'] if f['properties'].get('role') == 'row')['properties']['farm_map_id'] = 'x'
        with self.assertRaisesRegex(FarmMapError, 'belongs to map'):
            parse(doc)

    def test_row_name_must_match_field_and_row(self):
        """The names the robot and LiteFarm use for a row agree."""
        doc = load()
        next(f for f in doc['features'] if f['properties'].get('role') == 'row')['properties']['name'] = 'F9R9'
        with self.assertRaisesRegex(FarmMapError, 'should be F1R0'):
            parse(doc)

    def test_rows_of_a_field_must_be_together(self):
        """Interleaved fields are refused, as the single-plan importer does."""
        doc = load()
        rows = [f for f in doc['features'] if f['properties'].get('role') == 'row']
        rows[4], rows[2] = rows[2], rows[4]
        doc['features'] = [f for f in doc['features'] if f['properties'].get('role') != 'row'] + rows
        with self.assertRaises(FarmMapError):
            parse(doc)

    def test_bad_input(self):
        """Non-JSON, wrong type, wrong version and oversized files give a reason, not a traceback."""
        for text, reason in (('{', 'not valid JSON'), ('[]', 'FeatureCollection'),
                             ('x' * 5_000_001, 'too large')):
            with self.assertRaisesRegex(FarmMapError, reason):
                parse_farm_map(text)
        doc = load()
        doc['properties']['version'] = 2
        with self.assertRaisesRegex(FarmMapError, 'version'):
            parse(doc)

    def test_plan_version_must_be_the_supported_integer(self):
        """Numerically equal booleans and floats are not schema versions."""
        for value in (True, False, 1.0, '1', None, 2):
            with self.subTest(value=value):
                doc = load()
                doc['properties']['version'] = value
                with self.assertRaisesRegex(FarmMapError, 'unsupported plan version'):
                    parse(doc)

    def test_field_numbers_must_match_all_listed_fields(self):
        """Each listed location must map to its integer number, even without rows."""
        for value in (None, [], 'numbers'):
            with self.subTest(field_numbers=value):
                doc = load()
                doc['properties']['field_numbers'] = value
                with self.assertRaisesRegex(FarmMapError, 'field_numbers is missing'):
                    parse(doc)
        for value in (None, 2, True, 1.0, '1'):
            with self.subTest(mapped_number=value):
                doc = load()
                field = doc['properties']['fields'][0]
                doc['properties']['field_numbers'][field['field_location_id']] = value
                with self.assertRaisesRegex(FarmMapError, 'field_numbers'):
                    parse(doc)
        doc = load()
        field = doc['properties']['fields'][0]
        del doc['properties']['field_numbers'][field['field_location_id']]
        with self.assertRaisesRegex(FarmMapError, 'field_numbers'):
            parse(doc)
        for location_id in (None, '', [], {}):
            with self.subTest(location_id=location_id):
                doc = load()
                doc['properties']['fields'][0]['field_location_id'] = location_id
                with self.assertRaisesRegex(FarmMapError, 'field_location_id'):
                    parse(doc)
        doc = load()
        doc['properties']['fields'].append({'field_number': 3, 'field_location_id': 'empty-field'})
        with self.assertRaisesRegex(FarmMapError, 'field_numbers'):
            parse(doc)
        doc['properties']['field_numbers']['empty-field'] = 3
        self.assertEqual(parse(doc).warnings, ['field 3 is listed but has no rows'])

    def test_malformed_features_are_refused(self):
        """Malformed feature collections, features and properties give readable errors."""
        for value in (None, {}, 'features', 1):
            with self.subTest(features=value):
                doc = load()
                doc['features'] = value
                with self.assertRaisesRegex(FarmMapError, 'features must be a list'):
                    parse(doc)
        for value in (None, [], 'feature', 1):
            with self.subTest(feature=value):
                doc = load()
                doc['features'][0] = value
                with self.assertRaisesRegex(FarmMapError, 'feature 0: must be an object'):
                    parse(doc)
        for value in (None, [], 'properties', 1):
            with self.subTest(properties=value):
                doc = load()
                doc['features'][0]['properties'] = value
                with self.assertRaisesRegex(FarmMapError, 'feature 0: properties must be an object'):
                    parse(doc)

    def test_malformed_row_geometry_is_refused(self):
        """Only row geometry needs a LineString object; other features are still ignored."""
        for value in (None, [], 'geometry', 1, {'type': 'Polygon'}):
            with self.subTest(geometry=value):
                doc = load()
                row = next(f for f in doc['features'] if f['properties'].get('role') == 'row')
                row['geometry'] = value
                with self.assertRaisesRegex(FarmMapError, 'row is not a LineString'):
                    parse(doc)
        doc = load()
        doc['features'][0]['geometry'] = None
        self.assertEqual(parse(doc).content_hash, FIXTURE_HASH)

    def test_non_finite_and_short_rows_are_refused(self):
        """A row needs two finite positions."""
        doc = load()
        row = next(f for f in doc['features'] if f['properties'].get('role') == 'row')
        row['geometry']['coordinates'] = [[3.6, 48.0]]
        with self.assertRaisesRegex(FarmMapError, 'points'):
            parse(doc)
        row['geometry']['coordinates'] = [[3.6, 48.0], [None, 48.1]]
        with self.assertRaisesRegex(FarmMapError, 'finite'):
            parse(doc)

    def test_does_not_modify_its_input_text(self):
        """Parsing twice gives the same map."""
        text = json.dumps(copy.deepcopy(load()))
        self.assertEqual(parse_farm_map(text).content_hash, parse_farm_map(text).content_hash)


class FarmMapDockTest(unittest.TestCase):
    """Canonical docks and routes are included in the verified farm map hash."""

    def test_fractional_heading_uses_javascript_number_format(self):
        for value, expected in ((0.000001, '0.000001'), (1e-7, '1e-7'),
                                (-1e-7, '-1e-7'), (1.5, '1.5'), (1e20, '100000000000000000000'),
                                (1e21, '1e+21'), (-0.0, '0')):
            with self.subTest(value=value):
                self.assertIn(f',"dock",{expected},',
                              canonical_features_text([FarmDock('dock', 48, 3, value)], []))

    def test_out_of_bounds_docks_and_route_waypoints_are_refused(self):
        for index, point_index in ((-3, None), (-1, 1)):
            for point in ([181, 48], [-181, 48], [3, 91], [3, -91]):
                doc = with_docks(load())
                geometry = doc['features'][index]['geometry']
                if point_index is None:
                    geometry['coordinates'] = point
                else:
                    geometry['coordinates'][point_index] = point
                with self.assertRaisesRegex(FarmMapError, 'bounds'):
                    parse(doc)

    def test_docks_and_routes_are_read(self):
        """Points become (lat, lon), and a null heading stays None."""
        farm_map = parse(with_docks(load()))
        self.assertEqual([(d.name, d.lat, d.lon, d.heading_deg) for d in farm_map.docks],
                         [('dock', 48.0039, 3.6615, 1.0), ('dock1', 48.0038, 3.6614, None)])
        route = farm_map.routes[0]
        self.assertEqual((route.name, route.from_node, route.to_node), ('route1', 'dock', 'F1R0N0'))
        self.assertEqual(route.points, [(48.0039, 3.6615), (48.0040, 3.6617), (48.0041, 3.6619)])

    def test_docks_and_routes_are_in_the_hash(self):
        """The hash is LiteFarm's: rows, then ';', then docks and routes."""
        field = FieldRows(1, [PlanRow(0, [(48.0, 3.66), (48.0, 3.661)])])
        self.assertEqual(farm_map_hash([field], DOCKS, ROUTES), DOCKS_HASH)
        self.assertEqual(farm_map_hash([field]), ROWS_ONLY_HASH)
        self.assertEqual(
            canonical_features_text(DOCKS, ROUTES),
            '[["dock","dock",1,null,null,[[3.6615000,48.0039000]]],'
            '["dock","dock1",null,null,null,[[3.6614000,48.0038000]]],'
            '["route","route1",null,"dock","F1R0N0",'
            '[[3.6615000,48.0039000],[3.6617000,48.0040000],[3.6619000,48.0041000]]]]')

    def test_editing_a_dock_breaks_the_hash(self):
        doc = with_docks(load())
        doc['features'][-3]['geometry']['coordinates'][0] += 0.0001
        with self.assertRaisesRegex(FarmMapError, 'content_hash does not match'):
            parse(doc)

    def test_a_map_with_docks_needs_a_hash_that_covers_them(self):
        """A hash of the rows alone no longer verifies a map that has docks."""
        doc = with_docks(load())
        doc['properties']['content_hash'] = FIXTURE_HASH
        with self.assertRaisesRegex(FarmMapError, 'content_hash does not match'):
            parse(doc)

    def test_a_map_without_docks_has_none(self):
        farm_map = parse(load())
        self.assertEqual((farm_map.docks, farm_map.routes), ([], []))

    def test_route_from_an_unknown_dock_is_refused(self):
        doc = with_docks(load())
        doc['features'][-1]['properties']['from_node'] = 'nowhere'
        with self.assertRaisesRegex(FarmMapError, 'not a dock in the map'):
            parse(doc)

    def test_dock_must_be_a_point_with_a_numeric_heading(self):
        doc = with_docks(load())
        doc['features'][-3]['properties']['heading_deg'] = 'north'
        with self.assertRaisesRegex(FarmMapError, 'heading_deg'):
            parse(doc)
        doc = with_docks(load())
        doc['features'][-3]['geometry'] = {'type': 'LineString', 'coordinates': [[3.6, 48.0], [3.7, 48.1]]}
        with self.assertRaisesRegex(FarmMapError, 'not a Point'):
            parse(doc)

    def test_duplicate_dock_names_are_refused(self):
        doc = with_docks(load())
        doc['features'][-2]['properties']['name'] = 'dock'
        with self.assertRaisesRegex(FarmMapError, 'not unique'):
            parse(doc)


if __name__ == '__main__':
    unittest.main()
