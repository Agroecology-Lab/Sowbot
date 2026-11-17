#!/usr/bin/env python3
"""Upload one inclusion polygon fence to an ArduPilot flight controller, then read it back.

Takes a polygon from a farm-map or fence GeoJSON ([lon, lat] coordinates) and sends it with the
MAVLink mission protocol (mission type FENCE, MAV_CMD_NAV_FENCE_POLYGON_VERTEX_INCLUSION).

Use a link the devkit_mavlink_bridge does not own, such as the board's USB port: the bridge keeps
the telemetry port. The upload only stores the polygon. Fence behaviour comes from FENCE_ENABLE,
FENCE_TYPE and FENCE_ACTION (params/sowbot_rover.parm). The polygon is an inclusion fence: it must
enclose the whole area the rover works in, with margin, and the rover must start inside it. A rover
outside it is in breach, and a field boundary used as-is puts the outer rows on the limit.

    tools/upload_fence.py --geojson farm-map.geojson --role field --field 1 --endpoint /dev/ttyACM0
    tools/upload_fence.py --geojson farm-map.geojson --role field --field 1 --dry-run

Not run against a flight controller or SITL. The unit tests use a scripted stand-in.
"""

import argparse
import json
import os
import sys
import time

os.environ.setdefault('MAVLINK20', '1')  # mission_type is a MAVLink 2 field

TIMEOUT_S = 5.0
COORD_SCALE = 1e7
TOLERANCE_DEG = 1.5e-7  # one degE7 count plus rounding


class FenceError(Exception):
    """A fence could not be loaded, uploaded or verified."""


def load_polygon(document: dict, role: str, field: int | None = None) -> list[tuple[float, float]]:
    """Return the (lat, lon) vertices of exactly one Polygon feature with the given role.

    The closing vertex is dropped. For role 'field' a field number is required when there is
    more than one. Raise FenceError for no match, several matches, or fewer than three vertices.
    """
    matches = [f for f in document.get('features', [])
               if (f.get('geometry') or {}).get('type') == 'Polygon'
               and (f.get('properties') or {}).get('role') == role
               and (field is None or f['properties'].get('field') == field)]
    if len(matches) != 1:
        raise FenceError(f'expected one Polygon with role={role!r}'
                         f'{"" if field is None else f" and field={field}"}, found {len(matches)}')
    rings = matches[0]['geometry']['coordinates']
    if len(rings) != 1:
        raise FenceError('a fence polygon must have exactly one exterior ring and no interior rings')
    ring = rings[0]
    vertices = [(float(lat), float(lon)) for lon, lat in (point[:2] for point in ring)]
    if len(vertices) > 1 and vertices[0] == vertices[-1]:
        vertices.pop()
    if len(vertices) < 3:
        raise FenceError('a fence polygon needs at least three vertices')
    if any(not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0) for lat, lon in vertices):
        raise FenceError('vertex outside valid latitude or longitude (is the file [lon, lat]?)')
    return vertices


def _wait(mav, types: list[str], timeout_s: float):
    """Return the next message of one of the given types, or raise FenceError on timeout."""
    message = mav.recv_match(type=types, blocking=True, timeout=timeout_s)
    if message is None:
        raise FenceError(f'timed out waiting for {" or ".join(types)}')
    return message


def upload(mav, vertices: list[tuple[float, float]], timeout_s: float = TIMEOUT_S) -> None:
    """Send the vertices as one inclusion polygon and wait for the controller to accept it."""
    from pymavlink import mavutil
    mavlink = mavutil.mavlink
    mav.mav.mission_count_send(
        mav.target_system, mav.target_component, len(vertices), mavlink.MAV_MISSION_TYPE_FENCE)
    while True:
        message = _wait(mav, ['MISSION_REQUEST_INT', 'MISSION_REQUEST', 'MISSION_ACK'], timeout_s)
        if message.get_type() == 'MISSION_ACK':
            if message.type != mavlink.MAV_MISSION_ACCEPTED:
                raise FenceError(f'flight controller refused the fence (MAV_MISSION_RESULT {message.type})')
            return
        if not 0 <= message.seq < len(vertices):
            raise FenceError(f'flight controller asked for vertex {message.seq} of {len(vertices)}')
        lat, lon = vertices[message.seq]
        mav.mav.mission_item_int_send(
            mav.target_system, mav.target_component, message.seq, mavlink.MAV_FRAME_GLOBAL,
            mavlink.MAV_CMD_NAV_FENCE_POLYGON_VERTEX_INCLUSION, 0, 0,
            len(vertices), 0, 0, 0, round(lat * COORD_SCALE), round(lon * COORD_SCALE), 0,
            mavlink.MAV_MISSION_TYPE_FENCE)


def download(mav, timeout_s: float = TIMEOUT_S) -> list[tuple[float, float]]:
    """Read the stored fence items back as (lat, lon) vertices."""
    from pymavlink import mavutil
    mavlink = mavutil.mavlink
    mav.mav.mission_request_list_send(
        mav.target_system, mav.target_component, mavlink.MAV_MISSION_TYPE_FENCE)
    count = _wait(mav, ['MISSION_COUNT'], timeout_s).count
    vertices = []
    for seq in range(count):
        mav.mav.mission_request_int_send(
            mav.target_system, mav.target_component, seq, mavlink.MAV_MISSION_TYPE_FENCE)
        item = _wait(mav, ['MISSION_ITEM_INT'], timeout_s)
        if item.command != mavlink.MAV_CMD_NAV_FENCE_POLYGON_VERTEX_INCLUSION:
            raise FenceError(f'stored fence holds a different item (command {item.command}); '
                             'this tool only handles one inclusion polygon')
        vertices.append((item.x / COORD_SCALE, item.y / COORD_SCALE))
    mav.mav.mission_ack_send(
        mav.target_system, mav.target_component, mavlink.MAV_MISSION_ACCEPTED,
        mavlink.MAV_MISSION_TYPE_FENCE)
    return vertices


def verify(sent: list[tuple[float, float]], stored: list[tuple[float, float]]) -> None:
    """Raise FenceError unless the stored vertices match those sent, in order."""
    if len(sent) != len(stored):
        raise FenceError(f'sent {len(sent)} vertices, controller holds {len(stored)}')
    for index, ((lat, lon), (s_lat, s_lon)) in enumerate(zip(sent, stored, strict=True)):
        if abs(lat - s_lat) > TOLERANCE_DEG or abs(lon - s_lon) > TOLERANCE_DEG:
            raise FenceError(f'vertex {index} differs: sent {(lat, lon)}, stored {(s_lat, s_lon)}')


def _connect(endpoint: str, baud: int, timeout_s: float):
    """Open the link and wait for the flight controller's heartbeat."""
    from pymavlink import mavutil
    mav = mavutil.mavlink_connection(endpoint, baud=baud)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        beat = mav.recv_match(type='HEARTBEAT', blocking=True, timeout=1.0)
        if beat is not None and beat.type != mavutil.mavlink.MAV_TYPE_GCS \
                and beat.autopilot != mavutil.mavlink.MAV_AUTOPILOT_INVALID:
            mav.target_system, mav.target_component = beat.get_srcSystem(), beat.get_srcComponent()
            return mav
    raise FenceError(f'no flight controller heartbeat on {endpoint}')


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, then upload and verify, or print the vertices with --dry-run."""
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--geojson', required=True, help='GeoJSON file with the polygon')
    parser.add_argument('--role', required=True, help='properties.role of the Polygon to use, e.g. field or fence')
    parser.add_argument('--field', type=int, help='properties.field number, for role field')
    parser.add_argument('--endpoint', help='MAVLink connection, for example /dev/ttyACM0 (not the bridge port)')
    parser.add_argument('--baud', type=int, default=115200)
    parser.add_argument('--dry-run', action='store_true', help='print the vertices and stop')
    args = parser.parse_args(argv)
    try:
        with open(args.geojson, encoding='utf-8') as handle:
            vertices = load_polygon(json.load(handle), args.role, args.field)
        print(f'{len(vertices)} vertices:')
        for lat, lon in vertices:
            print(f'  {lat:.7f}, {lon:.7f}')
        if args.dry_run:
            return 0
        if not args.endpoint:
            raise FenceError('--endpoint is required unless --dry-run')
        mav = _connect(args.endpoint, args.baud, 10.0)
        upload(mav, vertices)
        verify(vertices, download(mav))
    except (FenceError, OSError, ValueError, KeyError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    print('fence uploaded and read back. Check FENCE_ENABLE, FENCE_TYPE and FENCE_ACTION, '
          'and that the rover starts inside it.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
