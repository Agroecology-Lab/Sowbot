"""Geodetic anchoring of map -> odom."""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from support import load  # pylint: disable=wrong-import-position

map_anchor = load('map_anchor')

LAT0, LON0 = 51.0, -2.0


def _apply(transform, x, y):
    """Map an odom point into the map frame with (tx, ty, yaw)."""
    tx, ty, yaw = transform
    return tx + x * math.cos(yaw) - y * math.sin(yaw), ty + x * math.sin(yaw) + y * math.cos(yaw)


class TestGeodeticOffset(unittest.TestCase):
    def test_origin_has_no_offset(self):
        """Verify the origin maps to (0, 0)."""
        self.assertEqual(map_anchor.geodetic_offset_en(LAT0, LON0, LAT0, LON0), (0.0, 0.0))

    def test_north_and_east_scales_at_51_degrees(self):
        """Verify 0.001 degrees is about 111.2 m north and 70.1 m east at 51 degrees."""
        east, north = map_anchor.geodetic_offset_en(LAT0 + 0.001, LON0 + 0.001, LAT0, LON0)
        self.assertAlmostEqual(north, 111.2, delta=0.1)
        self.assertAlmostEqual(east, 70.1, delta=0.1)

    def test_longitude_wraps_at_the_antimeridian(self):
        """Verify a point just across 180 degrees is a small eastward offset."""
        east, _ = map_anchor.geodetic_offset_en(0.0, -179.999, 0.0, 179.999)
        self.assertAlmostEqual(east, 222.6, delta=0.2)


class TestMapOdomTransform(unittest.TestCase):
    def test_robot_at_origin_with_odom_offset(self):
        """Verify the transform cancels the odom position when the fix is at the origin."""
        tx, ty, yaw = map_anchor.map_odom_transform(LAT0, LON0, 3.0, 4.0, LAT0, LON0, 0.0)
        self.assertAlmostEqual(tx, -3.0)
        self.assertAlmostEqual(ty, -4.0)
        self.assertEqual(yaw, 0.0)

    def test_map_x_axis_pointing_north(self):
        """Verify map_yaw 90 degrees puts a point 10 m north of the origin at map (10, 0)."""
        north_10m = LAT0 + 10.0 / 111231.0
        transform = map_anchor.map_odom_transform(north_10m, LON0, 0.0, 0.0, LAT0, LON0, math.pi / 2)
        x, y = _apply(transform, 0.0, 0.0)
        self.assertAlmostEqual(x, 10.0, delta=0.01)
        self.assertAlmostEqual(y, 0.0, delta=0.01)
        self.assertAlmostEqual(transform[2], -math.pi / 2)

    def test_transform_reproduces_the_map_position_of_any_odom_point(self):
        """Verify applying the transform to the odom position gives the fix's map position."""
        yaw = math.radians(30.0)
        lat, lon = LAT0 + 0.0004, LON0 - 0.0006
        transform = map_anchor.map_odom_transform(lat, lon, 12.0, -7.0, LAT0, LON0, yaw)
        east, north = map_anchor.geodetic_offset_en(lat, lon, LAT0, LON0)
        expected = (east * math.cos(yaw) + north * math.sin(yaw), -east * math.sin(yaw) + north * math.cos(yaw))
        got = _apply(transform, 12.0, -7.0)
        self.assertAlmostEqual(got[0], expected[0], places=6)
        self.assertAlmostEqual(got[1], expected[1], places=6)


class TestAnchorEstimator(unittest.TestCase):
    def _estimator(self, **kwargs):
        return map_anchor.AnchorEstimator(LAT0, LON0, 0.0, samples=3, **kwargs)

    def _feed(self, estimator, count, t0=0.0, odom_x=0.0, fix_lat=LAT0):
        result = None
        for i in range(count):
            estimator.add_fix(t0 + i, fix_lat, LON0)
            result = estimator.add_odom(t0 + i + 0.1, odom_x, 0.0)
        return result

    def test_nothing_until_the_window_is_full(self):
        """Verify fewer pairs than samples return no transform."""
        self.assertIsNone(self._feed(self._estimator(), 2))

    def test_agreeing_pairs_return_the_median(self):
        """Verify a full window of agreeing pairs returns the transform."""
        tx, ty, yaw = self._feed(self._estimator(), 3, odom_x=2.0)
        self.assertAlmostEqual(tx, -2.0)
        self.assertAlmostEqual(ty, 0.0)
        self.assertEqual(yaw, 0.0)

    def test_skewed_samples_are_not_paired(self):
        """Verify a fix and odom sample further apart than max_skew_s make no pair."""
        estimator = self._estimator(max_skew_s=0.5)
        for i in range(5):
            estimator.add_fix(i * 10.0, LAT0, LON0)
            self.assertIsNone(estimator.add_odom(i * 10.0 + 1.0, 0.0, 0.0))

    def test_disagreeing_window_is_refused(self):
        """Verify a window spread wider than max_spread_m returns nothing and reports the spread."""
        estimator = self._estimator(max_spread_m=0.5)
        result = None
        for i, x in enumerate((0.0, 5.0, 10.0)):
            estimator.add_fix(float(i), LAT0, LON0)
            result = estimator.add_odom(i + 0.1, x, 0.0)
        self.assertIsNone(result)
        self.assertAlmostEqual(estimator.spread_m, 10.0)

    def test_invalid_limits_are_rejected(self):
        """Verify non-positive limits raise ValueError."""
        for kwargs in ({'samples': 0}, {'max_skew_s': 0.0}, {'max_spread_m': -1.0}, {'max_skew_s': math.nan},
                       {'max_spread_m': math.nan}, {'max_spread_m': math.inf}):
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                map_anchor.AnchorEstimator(LAT0, LON0, 0.0, **kwargs)

    def test_origin_outside_the_valid_range_is_rejected(self):
        """Verify a latitude beyond 90 degrees or a longitude beyond 180 degrees raises ValueError."""
        for lat, lon in ((91.0, 0.0), (-90.5, 0.0), (0.0, 181.0), (0.0, -180.5), (math.nan, 0.0)):
            with self.subTest(lat=lat, lon=lon), self.assertRaises(ValueError):
                map_anchor.AnchorEstimator(lat, lon, 0.0)


if __name__ == '__main__':
    unittest.main()
