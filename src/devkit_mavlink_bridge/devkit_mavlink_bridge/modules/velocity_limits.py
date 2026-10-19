"""Speed limits for the cmd_vel -> MAVLink path.

Plain Python, no rclpy or pymavlink, so it can be unit tested without ROS.
The flight controller has its own limits; these keep a bad upstream command
(Nav2 misconfiguration, a scaled joystick) from reaching it in the first place.
"""

import math


def limit_velocity(linear_x: float, angular_z: float,
                   max_linear_x: float, max_angular_z: float) -> tuple[float, float]:
    """Clamp linear_x (m/s) and angular_z (rad/s) to symmetric limits.

    Raise ValueError if either limit is nonpositive or nonfinite.
    A nonfinite input returns zero for that component.
    """
    for name, limit in (('max_linear_x', max_linear_x), ('max_angular_z', max_angular_z)):
        if not math.isfinite(limit) or limit <= 0.0:
            raise ValueError(f'{name} must be a positive finite number, got {limit!r}')
    return _clamp(linear_x, max_linear_x), _clamp(angular_z, max_angular_z)


def _clamp(value: float, limit: float) -> float:
    """Clamp a finite value to symmetric bounds, replacing nonfinite values with zero."""
    if not math.isfinite(value):
        return 0.0
    return max(-limit, min(limit, value))
