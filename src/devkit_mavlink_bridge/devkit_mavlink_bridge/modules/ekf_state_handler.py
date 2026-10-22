"""Convert the flight controller's EKF3 state into ROS conventions.

Plain Python, no rclpy or pymavlink, so it can be unit tested without ROS.

ArduPilot reports in aviation frames: world NED, body FRD (x forward, y right,
z down). ROS uses REP-103 frames: world ENU, body FLU (x forward, y left, z up).
Every conversion lives here so the node never touches a sign by hand.

MAVLink carries no EKF covariance. EKF_STATUS_REPORT variances are normalised
innovation test ratios, not covariances, so they are never copied into a ROS
covariance matrix. Odometry covariances are flagged unknown instead.
"""

import math
from dataclasses import dataclass

# EKF_STATUS_FLAGS bits (MAVLink common.xml). Checked against pymavlink in the tests.
EKF_ATTITUDE = 1
EKF_VELOCITY_HORIZ = 2
EKF_VELOCITY_VERT = 4
EKF_POS_HORIZ_REL = 8
EKF_POS_HORIZ_ABS = 16
EKF_POS_VERT_ABS = 32
EKF_POS_VERT_AGL = 64
EKF_CONST_POS_MODE = 128
EKF_PRED_POS_HORIZ_REL = 256
EKF_PRED_POS_HORIZ_ABS = 512
EKF_UNINITIALIZED = 1024

EKF_FLAG_NAMES = {
    'attitude': EKF_ATTITUDE,
    'velocity_horiz': EKF_VELOCITY_HORIZ,
    'velocity_vert': EKF_VELOCITY_VERT,
    'pos_horiz_rel': EKF_POS_HORIZ_REL,
    'pos_horiz_abs': EKF_POS_HORIZ_ABS,
    'pos_vert_abs': EKF_POS_VERT_ABS,
    'pos_vert_agl': EKF_POS_VERT_AGL,
    'const_pos_mode': EKF_CONST_POS_MODE,
    'pred_pos_horiz_rel': EKF_PRED_POS_HORIZ_REL,
    'pred_pos_horiz_abs': EKF_PRED_POS_HORIZ_ABS,
    'uninitialized': EKF_UNINITIALIZED,
}

_SQRT_HALF = math.sqrt(0.5)
_Q_ENU_FROM_NED = (0.0, _SQRT_HALF, _SQRT_HALF, 0.0)  # (w, x, y, z): swap x and y, negate z
_Q_FRD_FROM_FLU = (0.0, 1.0, 0.0, 0.0)  # half turn about x: (x, y, z) -> (x, -y, -z)


@dataclass(frozen=True)
class EkfOdometry:
    """EKF3 pose and twist in ROS conventions.

    Position is metres from the EKF origin in ENU. Orientation is the FLU body
    in ENU as an (x, y, z, w) quaternion. Linear velocity (m/s) and angular
    velocity (rad/s) are in the FLU body frame, as nav_msgs/Odometry expects.
    """

    position: tuple[float, float, float]
    orientation: tuple[float, float, float, float]
    linear_velocity: tuple[float, float, float]
    angular_velocity: tuple[float, float, float]


def ekf_pose_valid(flags: int) -> bool:
    """Return True when EKF3 reports attitude, horizontal velocity and a horizontal position.

    A set EKF_CONST_POS_MODE or EKF_UNINITIALIZED bit fails the check: the position then
    carries no information, and publishing it would hide that from consumers.
    """
    required = EKF_ATTITUDE | EKF_VELOCITY_HORIZ
    if flags & required != required:
        return False
    if not flags & (EKF_POS_HORIZ_REL | EKF_POS_HORIZ_ABS):
        return False
    return not flags & (EKF_CONST_POS_MODE | EKF_UNINITIALIZED)


def ekf_absolute_position_valid(flags: int) -> bool:
    """Return True when ekf_pose_valid passes and the absolute horizontal position bit is set."""
    return ekf_pose_valid(flags) and bool(flags & EKF_POS_HORIZ_ABS)


def ekf_flag_names(flags: int) -> list[str]:
    """Return the names of the set EKF status flags, in bit order."""
    return [name for name, bit in EKF_FLAG_NAMES.items() if flags & bit]


def _multiply(a, b):
    """Hamilton product of two (w, x, y, z) quaternions."""
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw)


def _rotate_inverse(q, v):
    """Rotate vector v by the inverse of unit quaternion q (w, x, y, z)."""
    conjugate = (q[0], -q[1], -q[2], -q[3])
    _, x, y, z = _multiply(_multiply(conjugate, (0.0, *v)), q)
    return x, y, z


def attitude_to_enu_flu(w: float, x: float, y: float, z: float):
    """Convert an FRD-to-NED attitude quaternion to an (x, y, z, w) FLU-in-ENU quaternion.

    Return None for nonfinite components or a norm outside the open interval (0.5, 1.5).
    Accepted inputs are normalized, and the result has a nonnegative w component.
    """
    components = (w, x, y, z)
    if not all(math.isfinite(c) for c in components):
        return None
    norm = math.sqrt(sum(c * c for c in components))
    if not 0.5 < norm < 1.5:
        return None
    unit = tuple(c / norm for c in components)
    qw, qx, qy, qz = _multiply(_multiply(_Q_ENU_FROM_NED, unit), _Q_FRD_FROM_FLU)
    if qw < 0.0:  # q and -q are the same rotation; keep w non-negative
        qw, qx, qy, qz = -qw, -qx, -qy, -qz
    return qx, qy, qz, qw


def build_odometry(north_m, east_m, down_m, vn_mps, ve_mps, vd_mps,
                   q_w, q_x, q_y, q_z, rollspeed, pitchspeed, yawspeed):
    """Combine LOCAL_POSITION_NED and ATTITUDE_QUATERNION fields into an EkfOdometry.

    Positions are metres from the EKF origin. Velocities are NED earth-frame m/s. The
    quaternion is body FRD to NED. The three rates are body-frame FRD in rad/s.
    Return None if any input is nonfinite or the quaternion is unusable.
    """
    scalars = (north_m, east_m, down_m, vn_mps, ve_mps, vd_mps, rollspeed, pitchspeed, yawspeed)
    if not all(math.isfinite(s) for s in scalars):
        return None
    orientation = attitude_to_enu_flu(q_w, q_x, q_y, q_z)
    if orientation is None:
        return None
    x, y, z, w = orientation
    velocity_enu = (ve_mps, vn_mps, -vd_mps)
    return EkfOdometry(
        position=(east_m, north_m, -down_m),
        orientation=orientation,
        linear_velocity=_rotate_inverse((w, x, y, z), velocity_enu),
        angular_velocity=(rollspeed, -pitchspeed, -yawspeed),
    )
