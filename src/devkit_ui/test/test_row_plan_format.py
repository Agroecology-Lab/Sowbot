"""Tests for the row plan GeoJSON format that LiteFarm imports."""
import json
import unittest
from pathlib import Path

from devkit_ui.row_plan_format import (
    MAX_POINTS_PER_ROW,
    PlanProperties,
    PlanRow,
    RowPlanError,
    build_geojson,
    canonical_rows_text,
    format_coordinate,
    map_hash,
    row_length_m,
)

# The cross-language vector from LiteFarm's packages/api/tests/utils-tests/rowPlan.test.js. It holds a rounding
# tie (0.00390625 is exact in binary), a negative zero and a long decimal. Change both together or not at all.
VECTOR_ROWS = [
    PlanRow(12, [(51.53941, -2.41201), (51.53978, -2.41188)], 0),
    PlanRow(13, [(51.53978, -2.4118), (51.53941, -2.41193)], 0),
    PlanRow(13, [(0.00390625, -0.00390625), (-1e-9, 1.2345678901)], 1),
]
VECTOR_TEXT = (
    '[[12,0,[[-2.4120100,51.5394100],[-2.4118800,51.5397800]]],'
    '[13,0,[[-2.4118000,51.5397800],[-2.4119300,51.5394100]]],'
    '[13,1,[[-0.0039063,0.0039063],[1.2345679,0.0000000]]]]'
)
VECTOR_HASH = '91e75dec1e1f5165254ea0dd498f92d18931bd1659b6e93d014694a2cf19f4ed'

VECTOR_FILE = Path(__file__).resolve().parents[3] / 'tests' / 'row_plan_vectors.json'

ROW = PlanRow(1, [(48.0, 3.0), (48.001, 3.0)])
PROPS = PlanProperties(map_id='field-1', origin=(48.0, 3.0))


class CanonicalHashTest(unittest.TestCase):
    """The text and hash must be the same as LiteFarm's, to the last character."""

    def test_matches_the_cross_language_vector(self):
        self.assertEqual(canonical_rows_text(VECTOR_ROWS), VECTOR_TEXT)
        self.assertEqual(map_hash(VECTOR_ROWS), VECTOR_HASH)

    def test_order_is_the_order_given(self):
        self.assertNotEqual(map_hash(list(reversed(VECTOR_ROWS))), VECTOR_HASH)

    def test_ties_round_away_from_zero(self):
        self.assertEqual(format_coordinate(0.00390625), '0.0039063')
        self.assertEqual(format_coordinate(-0.00390625), '-0.0039063')

    def test_negative_zero_is_written_as_zero(self):
        self.assertEqual(format_coordinate(-1e-9), '0.0000000')
        self.assertEqual(format_coordinate(-0.0), '0.0000000')

    def test_always_seven_decimals(self):
        self.assertEqual(format_coordinate(52), '52.0000000')
        self.assertEqual(format_coordinate(-122.4194155), '-122.4194155')
        self.assertEqual(format_coordinate(0.1), '0.1000000')

    def test_hash_is_the_same_for_the_rounded_coordinates(self):
        long = [PlanRow(1, [(51.123456789012, -2.987654321098), (51.2, -2.9)])]
        short = [PlanRow(1, [(51.1234568, -2.9876543), (51.2, -2.9)])]
        self.assertEqual(map_hash(long), map_hash(short))


class VectorFileTest(unittest.TestCase):
    """tests/row_plan_vectors.json is shared with LiteFarm."""

    def setUp(self):
        self.vectors = json.loads(VECTOR_FILE.read_text(encoding='utf-8'))

    def test_rows_give_the_recorded_text_and_hash(self):
        for vector in self.vectors['vectors']:
            rows = [PlanRow(r['row'], [tuple(p) for p in r['points']], r['frag']) for r in vector['rows']]
            with self.subTest(vector['name']):
                self.assertEqual(canonical_rows_text(rows), vector['canonical_text'])
                self.assertEqual(map_hash(rows), vector['map_hash'])

    def test_coordinates_give_the_recorded_text(self):
        for case in self.vectors['coordinates']:
            with self.subTest(case['value']):
                self.assertEqual(format_coordinate(case['value']), case['text'])

    def test_the_inline_vector_is_in_the_file(self):
        hashes = {v['map_hash'] for v in self.vectors['vectors']}
        self.assertIn(VECTOR_HASH, hashes)


class BuildGeoJsonTest(unittest.TestCase):
    def test_a_robot_plan_has_the_properties_litefarm_reads(self):
        plan = build_geojson([ROW], PlanProperties(map_id='field-1', origin=(48.0, 3.0), supersedes='field-0',
                                                   field_location_id='abc', inputs_hash='f' * 64))
        props = plan['properties']
        self.assertEqual((props['version'], props['source'], props['origin']), (1, 'robot', [48.0, 3.0]))
        self.assertEqual((props['map_id'], props['supersedes'], props['field_location_id']),
                         ('field-1', 'field-0', 'abc'))
        self.assertEqual(props['map_hash'], map_hash([ROW]))
        self.assertEqual(plan['type'], 'FeatureCollection')

    def test_optional_properties_are_left_out_when_not_given(self):
        props = build_geojson([ROW], PROPS)['properties']
        for key in ('supersedes', 'field_location_id', 'inputs_hash', 'tool_width', 'angle'):
            self.assertNotIn(key, props)

    def test_rows_are_line_strings_in_lon_lat_order_as_given(self):
        rows = [PlanRow(2, [(1.0, 2.0), (3.0, 4.0)]), PlanRow(1, [(5.0, 6.0), (7.0, 8.0)])]
        features = build_geojson(rows, PROPS)['features']
        self.assertEqual([f['properties'] for f in features],
                         [{'role': 'row', 'row': 2, 'frag': 0}, {'role': 'row', 'row': 1, 'frag': 0}])
        self.assertEqual(features[0]['geometry'], {'type': 'LineString', 'coordinates': [[2.0, 1.0], [4.0, 3.0]]})

    def test_file_holds_exactly_what_the_hash_covers(self):
        plan = build_geojson([PlanRow(1, [(51.123456789012, -2.987654321098), (51.2, -2.9)])], PROPS)
        coordinates = plan['features'][0]['geometry']['coordinates']
        self.assertEqual(coordinates[0], [-2.9876543, 51.1234568])
        rows = [PlanRow(1, [(lat, lon) for lon, lat in coordinates])]
        self.assertEqual(map_hash(rows), plan['properties']['map_hash'])

    def test_refuses_what_litefarm_would_refuse(self):
        cases = {
            'no rows': ([], PROPS),
            'one point': ([PlanRow(1, [(48.0, 3.0)])], PROPS),
            'too many points': ([PlanRow(1, [(48.0, 3.0)] * (MAX_POINTS_PER_ROW + 1))], PROPS),
            'latitude out of range': ([PlanRow(1, [(91.0, 3.0), (48.0, 3.0)])], PROPS),
            'not a number': ([PlanRow(1, [(float('nan'), 3.0), (48.0, 3.0)])], PROPS),
            'negative row': ([PlanRow(-1, [(48.0, 3.0), (48.1, 3.0)])], PROPS),
            'same row and fragment twice': ([ROW, ROW], PROPS),
            'map_id with a space': ([ROW], PlanProperties(map_id='my map', origin=(48.0, 3.0))),
            'empty map_id': ([ROW], PlanProperties(map_id='', origin=(48.0, 3.0))),
            'map_id over 100 characters': ([ROW], PlanProperties(map_id='a' * 101, origin=(48.0, 3.0))),
            'bad supersedes': ([ROW], PlanProperties(map_id='ok', origin=(48.0, 3.0), supersedes='no/slash')),
        }
        for name, (rows, props) in cases.items():
            with self.subTest(name), self.assertRaises(RowPlanError):
                build_geojson(rows, props)

    def test_fragments_of_one_row_are_allowed(self):
        rows = [PlanRow(1, [(48.0, 3.0), (48.1, 3.0)], 0), PlanRow(1, [(48.2, 3.0), (48.3, 3.0)], 1)]
        self.assertEqual(len(build_geojson(rows, PROPS)['features']), 2)


class RowLengthTest(unittest.TestCase):
    def test_a_thousandth_of_a_degree_of_latitude_is_about_111_metres(self):
        self.assertAlmostEqual(row_length_m(ROW), 111.2, delta=0.2)


if __name__ == '__main__':
    unittest.main()
