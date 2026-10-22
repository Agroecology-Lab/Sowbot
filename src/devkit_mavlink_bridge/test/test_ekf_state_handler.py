"""EKF3 state conversion from aviation frames (NED/FRD) to ROS frames (ENU/FLU)."""

import math
import unittest

from devkit_mavlink_bridge.modules import ekf_state_handler as ekf
from pymavlink import mavutil

MAV = mavutil.mavlink
SQRT_HALF = math.sqrt(0.5)


def _yaw_quaternion(yaw_deg):
    """Return the (w, x, y, z) NED attitude of a level vehicle with the given compass heading."""
    half = math.radians(yaw_deg) / 2.0
    return math.cos(half), 0.0, 0.0, math.sin(half)


def _rotate(orientation, vector):
    """Rotate a vector by an (x, y, z, w) quaternion: body-frame vector to world frame."""
    x, y, z, w = orientation
    vx, vy, vz = vector
    tx, ty, tz = (2 * (y * vz - z * vy), 2 * (z * vx - x * vz), 2 * (x * vy - y * vx))
    return (vx + w * tx + (y * tz - z * ty),
            vy + w * ty + (z * tx - x * tz),
            vz + w * tz + (x * ty - y * tx))


class FlagTest(unittest.TestCase):
    def test_flag_values_match_mavlink(self):
        """Verify the literals here are the MAVLink EKF_STATUS_FLAGS bits."""
        for name, bit in ekf.EKF_FLAG_NAMES.items():
            with self.subTest(name=name):
                self.assertEqual(bit, getattr(MAV, f'EKF_{name.upper()}'))

    def test_pose_validity(self):
        """Verify the pose needs attitude, horizontal velocity and a position, and no veto bit."""
        base = ekf.EKF_ATTITUDE | ekf.EKF_VELOCITY_HORIZ
        self.assertTrue(ekf.ekf_pose_valid(base | ekf.EKF_POS_HORIZ_ABS))
        self.assertTrue(ekf.ekf_pose_valid(base | ekf.EKF_POS_HORIZ_REL))
        self.assertFalse(ekf.ekf_pose_valid(base))
        self.assertFalse(ekf.ekf_pose_valid(ekf.EKF_ATTITUDE | ekf.EKF_POS_HORIZ_ABS))
        self.assertFalse(ekf.ekf_pose_valid(ekf.EKF_VELOCITY_HORIZ | ekf.EKF_POS_HORIZ_ABS))
        self.assertFalse(ekf.ekf_pose_valid(base | ekf.EKF_POS_HORIZ_ABS | ekf.EKF_CONST_POS_MODE))
        self.assertFalse(ekf.ekf_pose_valid(base | ekf.EKF_POS_HORIZ_ABS | ekf.EKF_UNINITIALIZED))
        self.assertFalse(ekf.ekf_pose_valid(0))

    def test_absolute_position_needs_the_absolute_flag(self):
        """Verify a relative-only position is not reported as geodetic."""
        base = ekf.EKF_ATTITUDE | ekf.EKF_VELOCITY_HORIZ
        self.assertTrue(ekf.ekf_absolute_position_valid(base | ekf.EKF_POS_HORIZ_ABS))
        self.assertFalse(ekf.ekf_absolute_position_valid(base | ekf.EKF_POS_HORIZ_REL))

    def test_flag_names_are_listed_in_bit_order(self):
        """Verify set flags are named and unset ones are not."""
        self.assertEqual(ekf.ekf_flag_names(ekf.EKF_POS_HORIZ_ABS | ekf.EKF_ATTITUDE),
                         ['attitude', 'pos_horiz_abs'])
        self.assertEqual(ekf.ekf_flag_names(0), [])


class AttitudeTest(unittest.TestCase):
    def assertVectorAlmostEqual(self, actual, expected):
        """Compare vectors component by component."""
        for a, e in zip(actual, expected, strict=True):
            self.assertAlmostEqual(a, e, places=6)

    def test_facing_north_is_ninety_degrees_in_enu(self):
        """Verify a level vehicle facing north points along +y in ENU."""
        orientation = ekf.attitude_to_enu_flu(1.0, 0.0, 0.0, 0.0)
        self.assertVectorAlmostEqual(orientation, (0.0, 0.0, SQRT_HALF, SQRT_HALF))
        self.assertVectorAlmostEqual(_rotate(orientation, (1, 0, 0)), (0, 1, 0))  # forward is north
        self.assertVectorAlmostEqual(_rotate(orientation, (0, 1, 0)), (-1, 0, 0))  # left is west
        self.assertVectorAlmostEqual(_rotate(orientation, (0, 0, 1)), (0, 0, 1))  # up is up

    def test_facing_east_has_zero_yaw_in_enu(self):
        """Verify a vehicle facing east is the identity rotation in ENU."""
        orientation = ekf.attitude_to_enu_flu(*_yaw_quaternion(90.0))
        self.assertVectorAlmostEqual(orientation, (0.0, 0.0, 0.0, 1.0))

    def test_nose_up_pitch_raises_the_forward_axis(self):
        """Verify pitch up in FRD still points the nose upwards in ENU."""
        half = math.radians(10.0) / 2.0
        orientation = ekf.attitude_to_enu_flu(math.cos(half), 0.0, math.sin(half), 0.0)  # north, nose up
        forward = _rotate(orientation, (1, 0, 0))
        self.assertVectorAlmostEqual(forward, (0.0, math.cos(math.radians(10)), math.sin(math.radians(10))))

    def test_right_side_down_roll_lowers_the_right_side(self):
        """Verify roll right in FRD lowers the vehicle's right side, which is -y in FLU."""
        half = math.radians(10.0) / 2.0
        orientation = ekf.attitude_to_enu_flu(math.cos(half), math.sin(half), 0.0, 0.0)  # north, roll right
        right = _rotate(orientation, (0, -1, 0))
        self.assertLess(right[2], 0.0)

    def test_result_is_a_unit_quaternion_with_nonnegative_w(self):
        """Verify the output is normalised even for a slightly off-length input."""
        x, y, z, w = ekf.attitude_to_enu_flu(*(1.01 * c for c in _yaw_quaternion(200.0)))
        self.assertAlmostEqual(x * x + y * y + z * z + w * w, 1.0, places=9)
        self.assertGreaterEqual(w, 0.0)

    def test_unusable_quaternions_are_rejected(self):
        """Verify nonfinite, zero and wildly non-unit quaternions give None."""
        for q in ((0.0, 0.0, 0.0, 0.0), (float('nan'), 0.0, 0.0, 0.0), (5.0, 0.0, 0.0, 0.0),
                  (float('inf'), 0.0, 0.0, 0.0)):
            with self.subTest(q=q):
                self.assertIsNone(ekf.attitude_to_enu_flu(*q))


class OdometryTest(unittest.TestCase):
    def build(self, ned=(0, 0, 0), v_ned=(0, 0, 0), q=(1, 0, 0, 0), rates=(0, 0, 0)):
        """Build odometry from NED position and velocity, an attitude and body rates."""
        return ekf.build_odometry(*ned, *v_ned, *q, *rates)

    def test_position_is_converted_from_ned_to_enu(self):
        """Verify north, east and down become y, x and -z."""
        odometry = self.build(ned=(1.0, 2.0, -3.0))
        self.assertEqual(odometry.position, (2.0, 1.0, 3.0))

    def test_forward_speed_is_positive_x_whatever_the_heading(self):
        """Verify the twist is in the body frame: driving forward is +x facing north or east."""
        north = self.build(v_ned=(1.0, 0.0, 0.0))
        east = self.build(v_ned=(0.0, 1.0, 0.0), q=_yaw_quaternion(90.0))
        for odometry in (north, east):
            self.assertAlmostEqual(odometry.linear_velocity[0], 1.0, places=6)
            self.assertAlmostEqual(odometry.linear_velocity[1], 0.0, places=6)

    def test_sideways_speed_to_the_right_is_negative_y(self):
        """Verify moving east while facing north is moving right, so -y in FLU."""
        odometry = self.build(v_ned=(0.0, 1.0, 0.0))
        self.assertAlmostEqual(odometry.linear_velocity[1], -1.0, places=6)

    def test_downward_velocity_is_negative_z(self):
        """Verify NED down velocity becomes negative z."""
        odometry = self.build(v_ned=(0.0, 0.0, 0.5))
        self.assertAlmostEqual(odometry.linear_velocity[2], -0.5, places=6)

    def test_angular_velocity_flips_pitch_and_yaw_signs(self):
        """Verify FRD rates (roll, pitch, yaw) become FLU (x, -y, -z)."""
        odometry = self.build(rates=(0.1, 0.2, 0.3))
        self.assertEqual(odometry.angular_velocity, (0.1, -0.2, -0.3))

    def test_nonfinite_input_gives_none(self):
        """Verify NaN or infinity anywhere gives no odometry."""
        self.assertIsNone(self.build(ned=(float('nan'), 0, 0)))
        self.assertIsNone(self.build(v_ned=(0, float('inf'), 0)))
        self.assertIsNone(self.build(rates=(0, 0, float('nan'))))
        self.assertIsNone(self.build(q=(0, 0, 0, 0)))


if __name__ == '__main__':
    unittest.main()
