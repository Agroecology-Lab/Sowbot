"""Tests for reading a LiteFarm farm map. tests/farm-map.geojson is LiteFarm's own export of its test fixture."""
import copy
import json
import unittest
from pathlib import Path

from devkit_ui.farm_map_import import (
    FarmMapError,
    farm_map_hash,
    looks_like_farm_map,
    parse_farm_map,
)

FIXTURE = Path(__file__).resolve().parents[3] / 'tests' / 'farm-map.geojson'
# From LiteFarm tests/utils-tests/farmMapFixture.test.js. If the two sides disagree, a published map would be refused.
FIXTURE_HASH = 'c50f60d85849c514ef0cab4dc65b2417cd03ccb0acf624d2387c35cc77212eb8'


def load() -> dict:
    """Return the fixture as a fresh dict."""
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


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


if __name__ == '__main__':
    unittest.main()
