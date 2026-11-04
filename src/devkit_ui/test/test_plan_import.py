# pylint: disable=missing-docstring
import json
import unittest

from devkit_ui.plan_import import PlanError, parse_plan


def _feat(coords_lonlat, row, frag=0, role='row'):
    """Build a GeoJSON LineString feature with row, fragment, and role metadata."""
    return {'type': 'Feature', 'properties': {'role': role, 'row': row, 'frag': frag},
            'geometry': {'type': 'LineString', 'coordinates': coords_lonlat}}


def _plan(features, **props):
    """Serialize a version-1 plan fixture with the supplied features and property overrides."""
    p = {'version': 1, 'origin': [51.45, -2.6], 'generator': 'test'}
    p.update(props)
    return json.dumps({'type': 'FeatureCollection', 'properties': p, 'features': features})


ROW0 = [[-2.6, 51.45], [-2.599, 51.45]]
ROW1 = [[-2.599, 51.4501], [-2.6, 51.4501]]


class ParsePlanTest(unittest.TestCase):
    def test_swaps_lonlat_to_latlon(self):
        """Verify GeoJSON coordinates become latitude/longitude pairs and preserve the origin."""
        plan = parse_plan(_plan([_feat(ROW0, 0)]))
        self.assertEqual(plan.swaths[0][0], (51.45, -2.6))
        self.assertEqual(plan.origin_ll, (51.45, -2.6))

    def test_order_preserved_and_non_rows_ignored(self):
        """Verify import preserves row order while ignoring features with other roles."""
        plan = parse_plan(_plan([_feat(ROW0, 0, role='field'), _feat(ROW0, 0), _feat(ROW1, 1)]))
        self.assertEqual(len(plan.swaths), 2)
        self.assertEqual(plan.swaths[1][0], (51.4501, -2.599))

    def test_fragments_of_one_row_are_not_linked(self):
        """Verify consecutive fragments of the same row mark a break in headland links."""
        plan = parse_plan(_plan([_feat(ROW0, 0, 0), _feat(ROW0, 0, 1), _feat(ROW1, 1)]))
        self.assertEqual(plan.break_after, {0})

    def test_rejects_bad_input(self):
        """Verify malformed plans, invalid coordinates, and invalid row IDs raise PlanError."""
        bad = [
            'not json',
            json.dumps({'type': 'Feature'}),
            _plan([_feat(ROW0, 0)], version=2),
            _plan([_feat(ROW0, 0)], origin=[200, 0]),
            _plan([]),
            _plan([_feat([[-2.6, 51.45]], 0)]),
            _plan([_feat([[-2.6, 91.0], [-2.5, 51.0]], 0)]),
            _plan([_feat([[-2.6, float('nan')], [-2.5, 51.0]], 0)]),
            _plan([_feat(ROW0, -1)]),
            _plan([_feat(ROW0, True)]),
        ]
        for text in bad:
            with self.subTest(text=text[:60]), self.assertRaises(PlanError):
                parse_plan(text)

    def test_distance_guard(self):
        """Verify plans near the robot are accepted and distant plans are rejected."""
        text = _plan([_feat(ROW0, 0)])
        parse_plan(text, robot_ll=(51.4501, -2.5995))
        with self.assertRaises(PlanError):
            parse_plan(text, robot_ll=(28.6, 77.2))

    def test_contour_may_be_missing_or_boolean(self):
        self.assertIsNone(parse_plan(_plan([_feat(ROW0, 0)])).params['contour'])
        for contour in (False, True):
            with self.subTest(contour=contour):
                plan = parse_plan(_plan([_feat(ROW0, 0)], contour=contour))
                self.assertIs(plan.params['contour'], contour)

    def test_rejects_present_non_boolean_contour(self):
        for contour in ('false', 'true', '', 0, 1, None, [], {}):
            with self.subTest(contour=contour), self.assertRaisesRegex(PlanError, 'contour'):
                parse_plan(_plan([_feat(ROW0, 0)], contour=contour))


if __name__ == '__main__':
    unittest.main()


def _multi(features, fields=None, **props):
    """Serialize a multi-field fixture like LiteFarm's all-fields export."""
    fields = fields if fields is not None else [
        {'field_number': 1, 'origin': [51.45, -2.6], 'tool_width': 3, 'snake': True},
        {'field_number': 2, 'origin': [51.46, -2.6], 'tool_width': 5, 'snake': False}]
    p = {'version': 1, 'multi_field': True, 'fields': fields, 'generator': 'litefarm'}
    p.update(props)
    return json.dumps({'type': 'FeatureCollection', 'properties': p, 'features': features})


def _mfeat(coords_lonlat, row, field, frag=0):
    f = _feat(coords_lonlat, row, frag)
    f['properties']['field'] = field
    return f


ROW0B = [[-2.6, 51.46], [-2.599, 51.46]]
ROW1B = [[-2.599, 51.4601], [-2.6, 51.4601]]


class ParseMultiFieldPlanTest(unittest.TestCase):
    def test_merges_fields_in_file_order(self):
        """Verify rows of every field are imported and the first field supplies origin and params."""
        plan = parse_plan(_multi([_mfeat(ROW0, 0, 1), _mfeat(ROW1, 1, 1),
                                  _mfeat(ROW0B, 0, 2), _mfeat(ROW1B, 1, 2)]))
        self.assertEqual(len(plan.swaths), 4)
        self.assertEqual(plan.swaths[2][0], (51.46, -2.6))
        self.assertEqual(plan.origin_ll, (51.45, -2.6))
        self.assertEqual(plan.params['tool_width'], 3)

    def test_no_edge_joins_two_fields(self):
        """Verify the last swath of a field is not linked to the first of the next."""
        plan = parse_plan(_multi([_mfeat(ROW0, 0, 1), _mfeat(ROW1, 1, 1),
                                  _mfeat(ROW0B, 0, 2), _mfeat(ROW1B, 1, 2)]))
        self.assertEqual(plan.break_after, {1})

    def test_equal_row_numbers_in_different_fields_are_not_fragments(self):
        """Verify a single row 0 in each field is not read as two fragments of one row."""
        plan = parse_plan(_multi([_mfeat(ROW0, 0, 1), _mfeat(ROW0B, 0, 2)]))
        self.assertEqual(plan.break_after, {0})

    def test_fragments_within_a_field_still_break(self):
        """Verify obstacle splits inside a field keep their break next to the field break."""
        plan = parse_plan(_multi([_mfeat(ROW0, 0, 1, 0), _mfeat(ROW0, 0, 1, 1),
                                  _mfeat(ROW0B, 0, 2)]))
        self.assertEqual(plan.break_after, {0, 1})

    def test_rejects_bad_multi_field_input(self):
        """Verify missing fields, bad field numbers and interleaved fields raise PlanError."""
        bad = [
            _multi([_mfeat(ROW0, 0, 1)], fields=[]),
            _multi([_mfeat(ROW0, 0, 1)], fields=['x']),
            _multi([_feat(ROW0, 0)]),
            _multi([_mfeat(ROW0, 0, 0)]),
            _multi([_mfeat(ROW0, 0, True)]),
            _multi([_mfeat(ROW0, 0, 1), _mfeat(ROW0B, 0, 2), _mfeat(ROW1, 1, 1)]),
            _multi([_mfeat(ROW0, 0, 1)], fields=[{'origin': [200, 0]}]),
        ]
        for text in bad:
            with self.subTest(text=text[:80]), self.assertRaises(PlanError):
                parse_plan(text)

    def test_single_field_files_are_unchanged(self):
        """Verify a file without multi_field still needs a top-level origin."""
        with self.assertRaises(PlanError):
            parse_plan(_plan([_feat(ROW0, 0)], origin=None))

