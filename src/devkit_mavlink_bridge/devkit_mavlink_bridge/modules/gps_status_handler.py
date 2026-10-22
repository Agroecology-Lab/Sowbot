"""Convert MAVLink GPS reports into ROS NavSatFix fields and diagnostics.

Plain Python, no rclpy or pymavlink, so it can be unit tested without ROS.
Nothing here invents an accuracy: an unknown accuracy gives an unknown covariance.
"""

import math

# GPS_FIX_TYPE (MAVLink common.xml).
FIX_NO_GPS = 0
FIX_NO_FIX = 1
FIX_2D = 2
FIX_3D = 3
FIX_DGPS = 4
FIX_RTK_FLOAT = 5
FIX_RTK_FIXED = 6

FIX_TYPE_NAMES = {
    0: 'NO_GPS', 1: 'NO_FIX', 2: '2D_FIX', 3: '3D_FIX', 4: 'DGPS',
    5: 'RTK_FLOAT', 6: 'RTK_FIXED', 7: 'STATIC', 8: 'PPP',
}

# sensor_msgs/NavSatStatus.status and NavSatFix.position_covariance_type values.
STATUS_NO_FIX = -1
STATUS_FIX = 0
STATUS_SBAS_FIX = 1
STATUS_GBAS_FIX = 2
SERVICE_GPS = 1
COVARIANCE_UNKNOWN = 0
COVARIANCE_DIAGONAL_KNOWN = 2

# Diagnostic levels, mapped onto diagnostic_msgs/DiagnosticStatus by the node.
LEVEL_OK = 0
LEVEL_WARN = 1
LEVEL_ERROR = 2
LEVEL_STALE = 3

_UINT16_UNKNOWN = 0xFFFF
_UINT32_UNKNOWN = 0xFFFFFFFF
_NAN = float('nan')


def fix_type_to_navsat_status(fix_type: int) -> int:
    """Map GPS_FIX_TYPE to NavSatStatus.status.

    RTK float and DGPS map to SBAS_FIX, RTK fixed (and PPP) to GBAS_FIX, matching the
    carrier-solution convention in devkit_bringup/config/fusioncore.yaml.
    """
    if fix_type in (FIX_2D, FIX_3D):
        return STATUS_FIX
    if fix_type in (FIX_DGPS, FIX_RTK_FLOAT):
        return STATUS_SBAS_FIX
    if fix_type in (FIX_RTK_FIXED, 8):
        return STATUS_GBAS_FIX
    if fix_type == 7:  # static: fixed position, still a usable fix
        return STATUS_FIX
    return STATUS_NO_FIX


def accuracy_m(raw_mm: int):
    """Convert a MAVLink accuracy in mm to metres; return None when it is zero or all-ones (unknown)."""
    if raw_mm in (0, _UINT32_UNKNOWN):
        return None
    return raw_mm / 1000.0


def dop(raw_hundredths: int):
    """Convert a MAVLink DOP (value x 100) to a unitless number; return None when unknown."""
    if raw_hundredths == _UINT16_UNKNOWN:
        return None
    return raw_hundredths / 100.0


def navsat_fields(status: int, lat_e7: int, lon_e7: int, altitude_m: float,
                  h_acc_m=None, v_acc_m=None) -> dict:
    """Build NavSatFix fields from a NavSatStatus status and MAVLink coordinates.

    Latitude and longitude are degrees scaled by 1e7; altitude is meters above the
    WGS84 ellipsoid, and horizontal and vertical accuracies are in meters.
    With STATUS_NO_FIX the position is NaN.
    The covariance is diagonal with squared accuracies only when both are given,
    even with no fix; otherwise it is all zeros and marked unknown.
    """
    covariance = [0.0] * 9
    covariance_type = COVARIANCE_UNKNOWN
    if h_acc_m is not None and v_acc_m is not None:
        covariance[0] = covariance[4] = h_acc_m ** 2
        covariance[8] = v_acc_m ** 2
        covariance_type = COVARIANCE_DIAGONAL_KNOWN
    if status == STATUS_NO_FIX:
        latitude = longitude = altitude = _NAN
    else:
        latitude, longitude, altitude = lat_e7 / 1e7, lon_e7 / 1e7, altitude_m
    return {
        'status': status, 'service': SERVICE_GPS,
        'latitude': latitude, 'longitude': longitude, 'altitude': altitude,
        'position_covariance': covariance, 'position_covariance_type': covariance_type,
    }


def gps_raw_to_navsat(fix_type, lat_e7, lon_e7, alt_ellipsoid_mm, h_acc_mm, v_acc_mm) -> dict:
    """Build NavSatFix fields from GPS_RAW_INT or GPS2_RAW.

    NavSatFix altitude is height above the WGS84 ellipsoid, so the MSL `alt` field is not
    used. An alt_ellipsoid of exactly zero is treated as not reported and becomes NaN.
    """
    altitude = alt_ellipsoid_mm / 1000.0 if alt_ellipsoid_mm else _NAN
    return navsat_fields(fix_type_to_navsat_status(fix_type), lat_e7, lon_e7, altitude,
                         accuracy_m(h_acc_mm), accuracy_m(v_acc_mm))


def gps_diagnostic(message):
    """Return (level, summary, values) for a GPS_RAW_INT or GPS2_RAW message.

    Pass None when no recent report exists: the level is then STALE and values is empty.
    For a report, fix types at or below NO_FIX give ERROR, 2D gives WARN, and all others give OK.
    Values contains strings for fix type, satellite count, DOP, accuracy in meters and yaw
    in degrees, with unavailable measurements represented as 'unknown'.
    """
    if message is None:
        return LEVEL_STALE, 'no GPS report', {}
    fix_type = int(message.fix_type)
    name = FIX_TYPE_NAMES.get(fix_type, f'UNKNOWN_{fix_type}')
    satellites_visible = int(message.satellites_visible)
    values = {
        'fix_type': name,
        'satellites_visible': 'unknown' if satellites_visible == 255 else str(satellites_visible),
        'hdop': _format(dop(int(message.eph))),
        'vdop': _format(dop(int(message.epv))),
        'h_acc_m': _format(accuracy_m(int(getattr(message, 'h_acc', 0)))),
        'v_acc_m': _format(accuracy_m(int(getattr(message, 'v_acc', 0)))),
    }
    yaw_cdeg = int(getattr(message, 'yaw', 0))
    values['yaw_deg'] = (
        'unknown' if yaw_cdeg in (0, _UINT16_UNKNOWN) else _format((yaw_cdeg % 36000) / 100.0))
    if fix_type <= FIX_NO_FIX:
        return LEVEL_ERROR, name, values
    if fix_type == FIX_2D:
        return LEVEL_WARN, name, values
    return LEVEL_OK, name, values


def _format(value) -> str:
    """Format an optional number for a diagnostic key-value pair."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 'unknown'
    return f'{value:g}'
