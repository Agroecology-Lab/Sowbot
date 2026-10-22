"""GPS status conversion to NavSatFix fields and diagnostics."""

import math
import unittest
from types import SimpleNamespace

from devkit_mavlink_bridge.modules import gps_status_handler as gps
from pymavlink import mavutil

MAV = mavutil.mavlink


def _report(fix_type=gps.FIX_RTK_FIXED, satellites_visible=20, eph=80, epv=120,
            h_acc=14, v_acc=22, yaw=0):
    """Build a GPS_RAW_INT double."""
    return SimpleNamespace(fix_type=fix_type, satellites_visible=satellites_visible,
                           eph=eph, epv=epv, h_acc=h_acc, v_acc=v_acc, yaw=yaw)


class FixTypeTest(unittest.TestCase):
    def test_fix_type_constants_match_mavlink(self):
        """Verify the literals here are the MAVLink GPS_FIX_TYPE values."""
        self.assertEqual((gps.FIX_NO_GPS, gps.FIX_NO_FIX, gps.FIX_2D, gps.FIX_3D, gps.FIX_DGPS,
                          gps.FIX_RTK_FLOAT, gps.FIX_RTK_FIXED),
                         (MAV.GPS_FIX_TYPE_NO_GPS, MAV.GPS_FIX_TYPE_NO_FIX, MAV.GPS_FIX_TYPE_2D_FIX,
                          MAV.GPS_FIX_TYPE_3D_FIX, MAV.GPS_FIX_TYPE_DGPS, MAV.GPS_FIX_TYPE_RTK_FLOAT,
                          MAV.GPS_FIX_TYPE_RTK_FIXED))
        self.assertEqual((MAV.GPS_FIX_TYPE_STATIC, MAV.GPS_FIX_TYPE_PPP), (7, 8))

    def test_navsat_status_follows_the_carrier_solution_convention(self):
        """Verify RTK float is SBAS, RTK fixed is GBAS and no fix is -1."""
        expected = {0: gps.STATUS_NO_FIX, 1: gps.STATUS_NO_FIX, 2: gps.STATUS_FIX, 3: gps.STATUS_FIX,
                    4: gps.STATUS_SBAS_FIX, 5: gps.STATUS_SBAS_FIX, 6: gps.STATUS_GBAS_FIX,
                    7: gps.STATUS_FIX, 8: gps.STATUS_GBAS_FIX, 99: gps.STATUS_NO_FIX}
        for fix_type, status in expected.items():
            with self.subTest(fix_type=fix_type):
                self.assertEqual(gps.fix_type_to_navsat_status(fix_type), status)


class UnitTest(unittest.TestCase):
    def test_accuracy_converts_millimetres_and_hides_unknown(self):
        """Verify zero and all-ones accuracies are unknown, not zero metres."""
        self.assertEqual(gps.accuracy_m(14), 0.014)
        self.assertIsNone(gps.accuracy_m(0))
        self.assertIsNone(gps.accuracy_m(0xFFFFFFFF))

    def test_dop_converts_hundredths_and_hides_unknown(self):
        """Verify UINT16_MAX is unknown."""
        self.assertEqual(gps.dop(80), 0.8)
        self.assertIsNone(gps.dop(0xFFFF))


class NavSatFieldsTest(unittest.TestCase):
    def test_known_accuracy_gives_a_diagonal_covariance(self):
        """Verify the covariance is the squared accuracy and is marked approximate."""
        fields = gps.gps_raw_to_navsat(gps.FIX_RTK_FIXED, 513000000, -25000000, 12500, 20, 50)
        self.assertEqual(fields['status'], gps.STATUS_GBAS_FIX)
        self.assertEqual((fields['latitude'], fields['longitude'], fields['altitude']), (51.3, -2.5, 12.5))
        self.assertAlmostEqual(fields['position_covariance'][0], 0.0004)
        self.assertAlmostEqual(fields['position_covariance'][4], 0.0004)
        self.assertAlmostEqual(fields['position_covariance'][8], 0.0025)
        self.assertEqual(fields['position_covariance_type'], gps.COVARIANCE_DIAGONAL_KNOWN)

    def test_unknown_accuracy_gives_an_unknown_covariance(self):
        """Verify no accuracy is never reported as perfect."""
        fields = gps.gps_raw_to_navsat(gps.FIX_3D, 1, 1, 0, 0, 50)
        self.assertEqual(fields['position_covariance'], [0.0] * 9)
        self.assertEqual(fields['position_covariance_type'], gps.COVARIANCE_UNKNOWN)

    def test_no_fix_has_no_position(self):
        """Verify a receiver without a fix reports NaN, not its last coordinates."""
        for fix_type in (gps.FIX_NO_GPS, gps.FIX_NO_FIX):
            fields = gps.gps_raw_to_navsat(fix_type, 513000000, -25000000, 12500, 20, 50)
            self.assertEqual(fields['status'], gps.STATUS_NO_FIX)
            for key in ('latitude', 'longitude', 'altitude'):
                self.assertTrue(math.isnan(fields[key]))

    def test_missing_ellipsoid_height_is_nan(self):
        """Verify the MSL altitude is never passed off as ellipsoidal."""
        fields = gps.gps_raw_to_navsat(gps.FIX_3D, 513000000, -25000000, 0, 20, 50)
        self.assertTrue(math.isnan(fields['altitude']))

    def test_either_unknown_accuracy_keeps_the_whole_covariance_unknown(self):
        """Verify both axes need accuracy, including the all-ones sentinel on either axis."""
        for horizontal, vertical in ((0, 50), (20, 0), (0xFFFFFFFF, 50),
                                     (20, 0xFFFFFFFF), (0xFFFFFFFF, 0xFFFFFFFF)):
            with self.subTest(horizontal=horizontal, vertical=vertical):
                fields = gps.gps_raw_to_navsat(gps.FIX_3D, 1, 1, 12500, horizontal, vertical)
                self.assertEqual(fields['position_covariance_type'], gps.COVARIANCE_UNKNOWN)
                self.assertEqual(fields['position_covariance'], [0.0] * 9)

    def test_geographic_boundaries_and_negative_ellipsoid_height(self):
        """Verify signed coordinates and below-ellipsoid altitudes retain their units."""
        for lat, lon in ((0, 0), (-900000000, -1800000000), (900000000, 1800000000)):
            with self.subTest(lat=lat, lon=lon):
                fields = gps.gps_raw_to_navsat(gps.FIX_3D, lat, lon, -12500, 20, 50)
                self.assertEqual((fields['latitude'], fields['longitude']), (lat / 1e7, lon / 1e7))
                self.assertEqual(fields['altitude'], -12.5)
                self.assertEqual(fields['service'], gps.SERVICE_GPS)

    def test_unrecognised_fix_type_does_not_expose_coordinates(self):
        """Verify an unsupported fix type cannot publish apparently usable coordinates."""
        fields = gps.gps_raw_to_navsat(99, 513000000, -25000000, 12500, 20, 50)
        self.assertEqual(fields['status'], gps.STATUS_NO_FIX)
        for key in ('latitude', 'longitude', 'altitude'):
            self.assertTrue(math.isnan(fields[key]))

    def test_no_fix_preserves_reported_accuracy_without_exposing_position(self):
        """Verify accuracy does not turn an invalid receiver solution into a usable fix."""
        fields = gps.gps_raw_to_navsat(gps.FIX_NO_FIX, 513000000, -25000000, 12500, 300, 500)
        self.assertEqual(fields['status'], gps.STATUS_NO_FIX)
        for key in ('latitude', 'longitude', 'altitude'):
            self.assertTrue(math.isnan(fields[key]))
        self.assertEqual(fields['position_covariance_type'], gps.COVARIANCE_DIAGONAL_KNOWN)
        expected = (0.09, 0.0, 0.0, 0.0, 0.09, 0.0, 0.0, 0.0, 0.25)
        for actual, value in zip(fields['position_covariance'], expected, strict=True):
            self.assertAlmostEqual(actual, value)

    def test_navsat_fields_requires_both_accuracy_values(self):
        """Verify either missing accuracy keeps covariance unknown at the shared boundary."""
        for horizontal, vertical in ((None, None), (None, 0.5), (0.3, None)):
            with self.subTest(horizontal=horizontal, vertical=vertical):
                fields = gps.navsat_fields(gps.STATUS_FIX, 0, 0, -12.5, horizontal, vertical)
                self.assertEqual(fields['altitude'], -12.5)
                self.assertEqual(fields['position_covariance_type'], gps.COVARIANCE_UNKNOWN)
                self.assertEqual(fields['position_covariance'], [0.0] * 9)

    def test_covariance_is_independent_between_fixes(self):
        """Verify changing one result cannot affect another receiver or a later fix."""
        first = gps.gps_raw_to_navsat(gps.FIX_3D, 1, 1, 12500, 20, 50)
        second = gps.gps_raw_to_navsat(gps.FIX_3D, 1, 1, 12500, 20, 50)
        first['position_covariance'][0] = 99.0
        self.assertAlmostEqual(second['position_covariance'][0], 0.0004)
        unknown = gps.navsat_fields(gps.STATUS_NO_FIX, 0, 0, math.nan)
        self.assertEqual(unknown['position_covariance'], [0.0] * 9)


class DiagnosticTest(unittest.TestCase):
    def test_no_report_is_stale(self):
        """Verify missing data is STALE."""
        self.assertEqual(gps.gps_diagnostic(None)[0], gps.LEVEL_STALE)

    def test_levels_follow_the_fix_type(self):
        """Verify no fix is an error, 2D a warning and 3D or better ok."""
        expected = {0: gps.LEVEL_ERROR, 1: gps.LEVEL_ERROR, 2: gps.LEVEL_WARN, 3: gps.LEVEL_OK,
                    5: gps.LEVEL_OK, 6: gps.LEVEL_OK}
        for fix_type, level in expected.items():
            with self.subTest(fix_type=fix_type):
                self.assertEqual(gps.gps_diagnostic(_report(fix_type=fix_type))[0], level)

    def test_values_report_dop_accuracy_and_heading(self):
        """Verify the receiver's own numbers are passed through, with unknowns named as such."""
        _, summary, values = gps.gps_diagnostic(_report(yaw=9000))
        self.assertEqual(summary, 'RTK_FIXED')
        self.assertEqual(values['satellites_visible'], '20')
        self.assertEqual((values['hdop'], values['vdop']), ('0.8', '1.2'))
        self.assertEqual((values['h_acc_m'], values['v_acc_m']), ('0.014', '0.022'))
        self.assertEqual(values['yaw_deg'], '90')
        _, _, unknown = gps.gps_diagnostic(_report(eph=0xFFFF, h_acc=0, yaw=0))
        self.assertEqual((unknown['hdop'], unknown['h_acc_m'], unknown['yaw_deg']),
                         ('unknown', 'unknown', 'unknown'))

    def test_heading_of_north_is_zero_degrees(self):
        """Verify the MAVLink 36000 centidegree encoding of north reads as 0."""
        self.assertEqual(gps.gps_diagnostic(_report(yaw=36000))[2]['yaw_deg'], '0')

    def test_unknown_heading_sentinels(self):
        """Verify absent and unavailable headings are reported as unknown."""
        for yaw in (0, 0xFFFF):
            with self.subTest(yaw=yaw):
                self.assertEqual(gps.gps_diagnostic(_report(yaw=yaw))[2]['yaw_deg'], 'unknown')

    def test_unknown_satellite_count(self):
        """Verify UINT8_MAX is unknown while zero remains a reported count."""
        for satellites_visible, expected in ((255, 'unknown'), (0, '0'), (254, '254')):
            with self.subTest(satellites_visible=satellites_visible):
                values = gps.gps_diagnostic(_report(satellites_visible=satellites_visible))[2]
                self.assertEqual(values['satellites_visible'], expected)

    def test_missing_mavlink_extension_fields_are_unknown(self):
        """Verify MAVLink 1 reports without accuracy and yaw extensions remain usable."""
        report = SimpleNamespace(fix_type=gps.FIX_3D, satellites_visible=12, eph=100, epv=200)
        level, summary, values = gps.gps_diagnostic(report)
        self.assertEqual((level, summary), (gps.LEVEL_OK, '3D_FIX'))
        self.assertEqual((values['hdop'], values['vdop']), ('1', '2'))
        for key in ('h_acc_m', 'v_acc_m', 'yaw_deg'):
            self.assertEqual(values[key], 'unknown')

    def test_zero_dop_and_fractional_heading_are_reported(self):
        """Verify zero DOP is known and heading keeps centidegree precision."""
        values = gps.gps_diagnostic(_report(eph=0, epv=0, yaw=12345))[2]
        self.assertEqual((values['hdop'], values['vdop'], values['yaw_deg']), ('0', '0', '123.45'))


if __name__ == '__main__':
    unittest.main()
