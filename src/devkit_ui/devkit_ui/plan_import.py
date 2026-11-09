"""Validate and parse a swath plan exported by the web planner (web/index.html).

Pure Python, no ROS or UI imports, so it is unit-testable. The web planner
exports geometry only; topo nodes are built on the robot by
PlanApplicationService.save_rows() because node coordinates depend on the
robot's live GPS/odometry anchor and the loaded topo map.

Schema (version 1): GeoJSON FeatureCollection, [lon, lat] coordinates.
  properties: version, generator, origin [lat, lon], tool_width, angle,
              headland, snake, contour (bool, optional)
  features:   properties.role == 'row', LineString, properties.row (int),
              properties.frag (int, optional, default 0). Feature order is mission order.
              'field' / 'obstacle' features are informational and ignored.

Multi-field files (properties.multi_field true, exported by LiteFarm's "All fields"
download) hold the rows of several fields. properties.fields lists them, each with its
own origin and settings; the first one supplies origin and params. Every row feature
carries properties.field. Rows are merged in file order with unique row numbers (each
field's numbers are offset past the previous field's), and no headland edge joins the
last swath of one field to the first of the next.

Plan.lf_rows records each swath's LiteFarm identity, (field, row, frag), exactly as the file
gives it, before the row numbers are offset. Only multi-field files carry a field number, so
for any other file lf_rows is empty and its rows cannot be named in a station mission.
"""
import json
import math
from typing import NamedTuple

SCHEMA_VERSION = 1
MAX_ROWS = 2000
MAX_POINTS_PER_ROW = 5000
MAX_BYTES = 5_000_000
DEFAULT_MAX_DISTANCE_M = 2000.0


class PlanError(ValueError):
    """The plan file is malformed or unsafe to import."""


class Plan(NamedTuple):
    swaths: list          # [[(lat, lon), ...], ...] in mission order
    origin_ll: tuple      # (lat, lon): the planner's projection origin
    break_after: set      # swath indices with no headland edge to the next swath
    params: dict
    lf_rows: tuple = ()   # [(field, row, frag), ...] per swath, or () for a single-field file


def _num(v, what):
    """Return a finite numeric value as a float, or raise PlanError labeled with what."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise PlanError(f'{what}: not a finite number')
    return float(v)


def _latlon(lat, lon, what):
    """Return validated latitude/longitude degrees, or raise PlanError for invalid values."""
    lat, lon = _num(lat, what), _num(lon, what)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise PlanError(f'{what}: coordinate out of range')
    return lat, lon


def _dist_m(a, b):
    """Estimate distance in metres between two (latitude, longitude) pairs in degrees."""
    r = 6_378_137.0
    dx = math.radians(b[1] - a[1]) * r * math.cos(math.radians(a[0]))
    dy = math.radians(b[0] - a[0]) * r
    return math.hypot(dx, dy)


def _merge_fields(rows, field_of):
    """Offset row numbers between fields and find the swaths that end a field.

    ``rows`` and ``field_of`` contain corresponding entries in mission order.
    Equal row numbers within a field remain equal; later fields are offset past the
    largest number used so far. Return the adjusted rows and the zero-based indices
    immediately before field transitions.

    Raise PlanError if a field reappears after another field, or ValueError if the
    two inputs have different lengths.
    """
    merged, breaks, seen = [], set(), set()
    offset = top = 0
    for i, (row, field) in enumerate(zip(rows, field_of, strict=True)):
        if i and field != field_of[i - 1]:
            if field in seen:
                raise PlanError(f'rows of field {field} are not together')
            offset = top + 1
            breaks.add(i - 1)
        seen.add(field)
        merged.append(offset + row)
        top = max(top, offset + row)
    return merged, breaks


def parse_plan(text, robot_ll=None, max_distance_m=DEFAULT_MAX_DISTANCE_M) -> Plan:
    """Parse a version-1 GeoJSON plan into swaths, origin, breaks and parameters.

    Multi-field plans use the first field's origin and parameters, preserve row
    order, and mark breaks between fields as well as consecutive row fragments.
    Their result includes the original (field, row, fragment) identity for each swath;
    single-field plans have no LiteFarm row identities.
    If ``robot_ll`` is given as (latitude, longitude) in degrees, reject a plan
    whose row-endpoint center is more than ``max_distance_m`` meters away.

    Raise PlanError with a user-readable reason for validation failures. Malformed
    feature or coordinate structures can also propagate AttributeError, TypeError,
    IndexError or KeyError.
    """
    if len(text) > MAX_BYTES:
        raise PlanError('file too large')
    try:
        doc = json.loads(text)
    except ValueError as e:
        raise PlanError(f'not valid JSON: {e}') from e
    if not isinstance(doc, dict) or doc.get('type') != 'FeatureCollection':
        raise PlanError('not a GeoJSON FeatureCollection')
    props = doc.get('properties')
    if not isinstance(props, dict):
        raise PlanError('missing top-level properties')
    if props.get('version') != SCHEMA_VERSION:
        raise PlanError(f"unsupported plan version {props.get('version')!r}")
    multi = props.get('multi_field') is True
    meta = props
    if multi:
        fields = props.get('fields')
        if not (isinstance(fields, list) and fields and isinstance(fields[0], dict)):
            raise PlanError('multi-field plan lists no fields')
        meta = fields[0]
    origin = meta.get('origin')
    if not (isinstance(origin, list) and len(origin) == 2):
        raise PlanError('origin must be [lat, lon]')
    origin_ll = _latlon(origin[0], origin[1], 'origin')

    swaths, rows, field_of, frags = [], [], [], []
    for i, f in enumerate(doc.get('features') or []):
        fp = (f or {}).get('properties') or {}
        if fp.get('role') != 'row':
            continue
        geom = f.get('geometry') or {}
        if geom.get('type') != 'LineString':
            raise PlanError(f'feature {i}: row is not a LineString')
        coords = geom.get('coordinates')
        if not isinstance(coords, list) or not 2 <= len(coords) <= MAX_POINTS_PER_ROW:
            raise PlanError(f'feature {i}: bad point count')
        row = fp.get('row')
        if isinstance(row, bool) or not isinstance(row, int) or row < 0:
            raise PlanError(f'feature {i}: row must be a non-negative integer')
        # GeoJSON is [lon, lat]; the planner and topo code use (lat, lon).
        field = fp.get('field')
        if multi and (isinstance(field, bool) or not isinstance(field, int) or field < 1):
            raise PlanError(f'feature {i}: field must be a positive integer')
        frag = fp.get('frag', 0)
        if isinstance(frag, bool) or not isinstance(frag, int) or frag < 0:
            raise PlanError(f'feature {i}: frag must be a non-negative integer')
        swaths.append([_latlon(c[1], c[0], f'feature {i}') for c in coords])
        rows.append(row)
        frags.append(frag)
        field_of.append(field if multi else None)
        if len(swaths) > MAX_ROWS:
            raise PlanError(f'more than {MAX_ROWS} rows')
    if not swaths:
        raise PlanError('no rows in plan')

    field_breaks = set()
    lf_rows = ()
    if multi:
        lf_rows = tuple(zip(field_of, rows, frags, strict=True))
        rows, field_breaks = _merge_fields(rows, field_of)

    # Fragments of one row (split by an obstacle) must not be joined by a
    # headland edge: that edge would cross the obstacle.
    break_after = {i for i in range(len(rows) - 1) if rows[i] == rows[i + 1]} | field_breaks

    if robot_ll is not None:
        pts = [p for s in swaths for p in (s[0], s[-1])]
        centre = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        d = _dist_m(robot_ll, centre)
        if d > max_distance_m:
            raise PlanError(f'plan is {d / 1000:.1f} km from the robot '
                            f'(limit {max_distance_m / 1000:.1f} km); refusing to import')

    if 'contour' in meta and not isinstance(meta['contour'], bool):
        raise PlanError('contour must be a boolean')
    keys = ('tool_width', 'angle', 'headland', 'snake', 'contour', 'generator')
    params = {k: meta.get(k) for k in keys}
    return Plan(swaths, origin_ll, break_after, params, lf_rows)
