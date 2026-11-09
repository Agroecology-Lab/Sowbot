"""Read a LiteFarm farm map export and check it before a robot uses it. No ROS imports.

A farm map is the definitive set of row plans a farm hands to its robots (LiteFarm
``GET /farm_map/farm/:farm_id/published/geojson``). It is the multi-field file of
docs/row-plan-geojson.md with properties that name the map:

  properties: version, multi_field true, farm_map_id, farm_map_version, farm_map_name, season,
              status, previous_farm_map_id, content_hash, published_at, fields[], field_numbers
  rows:       role 'row', row, frag, field, farm_map_id, row_id, name (F<field>R<row>), node_names

``content_hash`` is the SHA-256 of ``[[field,<canonical rows>],...]`` ordered by field number, where
the canonical rows are those of row_plan_format (``[[row,frag,[[lon,lat],...]],...]``, 7 decimals,
mission order). It covers geometry, order and field numbers, not names. A robot that holds a map keeps
this hash; a mission is only valid for the map whose hash it names.

  docks:      role 'dock' (Point), name, heading_deg (number or null)
  routes:     role 'route' (LineString), name, from_node (a dock), to_node (F<field>R<row>N<n>)

Docks and routes are part of the hash, after the rows (see farm_map_hash). They are also checked for form.

This module only validates and describes. It does not touch the topological map.
"""
import hashlib
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal

from devkit_ui.row_plan_format import (
    MAX_BYTES,
    MAX_POINTS_PER_ROW,
    MAX_ROWS,
    SCHEMA_VERSION,
    PlanRow,
    canonical_rows_text,
    check_rows,
    format_coordinate,
)

HASH_PATTERN = re.compile(r'^[0-9a-f]{64}$')
ID_PATTERN = re.compile(r'^[A-Za-z0-9._:-]{1,100}$')
ACCEPTED_STATUSES = ('published',)


class FarmMapError(ValueError):
    """The farm map file is malformed, fails its hash, or is not one a robot should run."""


@dataclass(frozen=True)
class FieldRows:
    """The rows of one field, in mission order. ``rows`` are PlanRow with the LiteFarm row number."""

    field_number: int
    rows: list[PlanRow]
    location_id: str | None = None
    name: str | None = None
    origin: tuple[float, float] | None = None
    contour: bool = False


@dataclass(frozen=True)
class FarmDock:
    """A dock point. ``heading_deg`` is None when the map gives no heading."""

    name: str
    lat: float
    lon: float
    heading_deg: float | None = None


@dataclass(frozen=True)
class FarmRoute:
    """A route from a dock to a row end, as (lat, lon) points.

    ``from_node`` names a dock and ``to_node`` a row node (``F<field>R<row>N<n>``). The first point is the
    dock and the last is the row end, so the points between them are the route's own waypoints.
    target is the exported endpoint's (field, row, fragment, role), resolved by the reader.
    """

    name: str
    from_node: str
    to_node: str
    points: list[tuple[float, float]]
    target: tuple[int, int, int, str] | None = None


@dataclass(frozen=True)
class FarmMap:
    """A verified farm map."""

    farm_map_id: str
    version: int
    content_hash: str
    status: str
    fields: list[FieldRows]
    name: str | None = None
    season: str | None = None
    previous_farm_map_id: str | None = None
    warnings: list[str] = field(default_factory=list)
    docks: list[FarmDock] = field(default_factory=list)
    routes: list[FarmRoute] = field(default_factory=list)

    @property
    def origin(self) -> tuple[float, float]:
        """Return the (lat, lon) the first field was planned from, or the start of its first row."""
        first = self.fields[0]
        return first.origin or first.rows[0].points[0]

    @property
    def contour(self) -> bool:
        """Return whether any field was planned along contours, so rows get waypoints."""
        return any(f.contour for f in self.fields)

    @property
    def hash8(self) -> str:
        """Return the first 8 hex characters of the hash, as carried in station missions."""
        return self.content_hash[:8]

    @property
    def row_count(self) -> int:
        """Return how many row fragments the map holds."""
        return sum(len(f.rows) for f in self.fields)

    def swaths(self) -> list[list[tuple[float, float]]]:
        """Return every row as (lat, lon) points, fields in number order, rows in mission order."""
        return [list(r.points) for f in self.fields for r in f.rows]

    def break_after(self) -> set[int]:
        """Return swath indices that must not get a headland edge to the next swath.

        That is the last swath of each field, and any swath followed by another fragment of the same row.
        """
        breaks, index = set(), 0
        for f in self.fields:
            for i, r in enumerate(f.rows):
                last = i == len(f.rows) - 1
                if last or f.rows[i + 1].row == r.row:
                    breaks.add(index)
                index += 1
        breaks.discard(index - 1)  # nothing follows the final swath
        return breaks


def farm_map_hash(fields: list[FieldRows], docks: Sequence[FarmDock] = (), routes: Sequence[FarmRoute] = ()) -> str:
    """Return the farm map hash LiteFarm computes.

    It is the SHA-256 of ``[[field,<rows>],...]`` by field number. A map with docks or routes continues with
    ``;`` and their canonical text, see ``canonical_features_text``. A map with neither hashes as it did before
    docks existed.
    """
    parts = [f'[{f.field_number},{canonical_rows_text(f.rows)}]'
             for f in sorted(fields, key=lambda f: f.field_number)]
    text = f'[{",".join(parts)}]'
    if docks or routes:
        text += f';{canonical_features_text(docks, routes)}'
    return hashlib.sha256(text.encode()).hexdigest()


def canonical_features_text(docks: Sequence[FarmDock], routes: Sequence[FarmRoute]) -> str:
    """Return the canonical text of docks and routes, as LiteFarm's canonicalFeaturesText writes it.

    ``[["dock",name,heading,from,to,[[lon,lat],...]],...]``: docks first, then routes, each by name. A heading
    is written as a JavaScript number would be (``1``, not ``1.0``) or ``null``, a name as ``"name"`` or ``null``.
    """
    def text(value: str | None) -> str:
        return 'null' if value is None else f'"{value}"'

    def heading(value: float | None) -> str:
        if value is None:
            return 'null'
        if value == 0:
            return '0'
        if 1e-6 <= abs(value) < 1e21:
            fixed = format(Decimal(repr(value)), 'f')
            return fixed.rstrip('0').rstrip('.') if '.' in fixed else fixed
        mantissa, exponent = repr(value).lower().split('e')
        return f'{mantissa.rstrip("0").rstrip(".")}e{int(exponent):+d}'

    def points(pts: Sequence[tuple[float, float]]) -> str:
        return '[' + ','.join(f'[{format_coordinate(lon)},{format_coordinate(lat)}]' for lat, lon in pts) + ']'

    entries = [f'["dock","{d.name}",{heading(d.heading_deg)},null,null,{points([(d.lat, d.lon)])}]'
               for d in sorted(docks, key=lambda d: d.name)]
    entries += [f'["route","{r.name}",null,{text(r.from_node)},{text(r.to_node)},{points(r.points)}]'
                for r in sorted(routes, key=lambda r: r.name)]
    return f'[{",".join(entries)}]'


def looks_like_farm_map(text_or_doc: str | dict) -> bool:
    """Return whether a file is a farm map export (it names a ``farm_map_id``), without validating it."""
    doc = text_or_doc
    if isinstance(doc, str):
        try:
            doc = json.loads(doc)
        except ValueError:
            return False
    props = doc.get('properties') if isinstance(doc, dict) else None
    return isinstance(props, dict) and bool(props.get('farm_map_id'))


def _int(value: object, what: str, minimum: int) -> int:
    """Return value as an int of at least ``minimum``, or raise FarmMapError labelled with what."""
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise FarmMapError(f'{what}: must be an integer of at least {minimum}')
    return value


def _points(coords: object, what: str) -> list[tuple[float, float]]:
    """Return GeoJSON [lon, lat] positions as (lat, lon) tuples, or raise FarmMapError."""
    if not isinstance(coords, list) or not 2 <= len(coords) <= MAX_POINTS_PER_ROW:
        raise FarmMapError(f'{what}: needs 2 to {MAX_POINTS_PER_ROW} points')
    points = []
    for c in coords:
        if (not isinstance(c, list) or len(c) < 2
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                       for v in c[:2])):
            raise FarmMapError(f'{what}: a position is not a pair of finite numbers')
        if not -180 <= c[0] <= 180 or not -90 <= c[1] <= 90:
            raise FarmMapError(f'{what}: position is outside latitude/longitude bounds')
        points.append((float(c[1]), float(c[0])))
    return points


def _name(fp: dict, what: str) -> str:
    """Return the feature's name, or raise FarmMapError."""
    name = fp.get('name')
    if not isinstance(name, str) or not ID_PATTERN.match(name):
        raise FarmMapError(f'{what}: name is missing or not a valid id')
    return name


def _dock(feature: dict, fp: dict, what: str) -> FarmDock:
    """Return the dock a ``dock`` feature describes, or raise FarmMapError."""
    geometry = feature.get('geometry')
    if not isinstance(geometry, dict) or geometry.get('type') != 'Point':
        raise FarmMapError(f'{what}: dock is not a Point')
    lat, lon = _points([geometry.get('coordinates'), geometry.get('coordinates')], what)[0]
    heading = fp.get('heading_deg')
    if heading is not None and (isinstance(heading, bool) or not isinstance(heading, (int, float))
                                or not math.isfinite(heading)):
        raise FarmMapError(f'{what}: heading_deg must be a finite number or null')
    return FarmDock(_name(fp, what), lat, lon, None if heading is None else float(heading))


def _route(feature: dict, fp: dict, what: str) -> FarmRoute:
    """Return the route a ``route`` feature describes, or raise FarmMapError."""
    geometry = feature.get('geometry')
    if not isinstance(geometry, dict) or geometry.get('type') != 'LineString':
        raise FarmMapError(f'{what}: route is not a LineString')
    ends = [fp.get('from_node'), fp.get('to_node')]
    if not all(isinstance(e, str) and ID_PATTERN.match(e) for e in ends):
        raise FarmMapError(f'{what}: from_node and to_node must be valid ids')
    return FarmRoute(_name(fp, what), ends[0], ends[1], _points(geometry.get('coordinates'), what))


def parse_farm_map(text: str, *, accept_retired: bool = False) -> FarmMap:
    """Parse a LiteFarm farm map export and verify its content hash.

    Checks: version 1; ``multi_field``; a ``farm_map_id`` and ``content_hash`` in the allowed format; status
    ``published`` (``retired`` only with ``accept_retired``, because a retired map has been replaced); every row has
    a field and row number, belongs to this map and is named ``F<field>R<row>``; fields are listed in
    ``field_numbers``; each field's rows are together; and the hash recomputed from the rows, docks
    and routes equals ``content_hash``. Dock and route names must be unique within their respective
    kinds, and each route must start at a dock in the map. A route's target is resolved when it
    names an exported row endpoint.

    Raise FarmMapError with a reason a person can read. The hash is checked last, so a bad file says what is
    wrong with it before saying only that the hash differs.
    """
    if len(text) > MAX_BYTES:
        raise FarmMapError('file too large')
    try:
        doc = json.loads(text)
    except ValueError as error:
        raise FarmMapError(f'not valid JSON: {error}') from error
    if not isinstance(doc, dict) or doc.get('type') != 'FeatureCollection':
        raise FarmMapError('not a GeoJSON FeatureCollection')
    props = doc.get('properties')
    if not isinstance(props, dict):
        raise FarmMapError('missing top-level properties')
    schema_version = props.get('version')
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != SCHEMA_VERSION:
        raise FarmMapError(f'unsupported plan version {schema_version!r}')
    if props.get('multi_field') is not True or not props.get('farm_map_id'):
        raise FarmMapError('not a farm map: export the published farm map from LiteFarm, not a single field')
    map_id, declared = props['farm_map_id'], props.get('content_hash')
    if not isinstance(map_id, str) or not ID_PATTERN.match(map_id):
        raise FarmMapError('farm_map_id is not a valid id')
    if not isinstance(declared, str) or not HASH_PATTERN.match(declared):
        raise FarmMapError('content_hash must be 64 lower-case hex characters')
    status = props.get('status')
    if status not in ACCEPTED_STATUSES and not (accept_retired and status == 'retired'):
        raise FarmMapError(f'farm map status is {status!r}: only a published map can be run')
    version = _int(props.get('farm_map_version'), 'farm_map_version', 1)

    numbers = props.get('field_numbers')
    if not isinstance(numbers, dict):
        raise FarmMapError('field_numbers is missing')
    listed = props.get('fields')
    if not isinstance(listed, list) or not listed:
        raise FarmMapError('farm map lists no fields')
    field_meta = {}
    for entry in listed:
        number = _int(entry.get('field_number') if isinstance(entry, dict) else None, 'fields[].field_number', 1)
        if number in field_meta:
            raise FarmMapError(f'field {number} is listed twice')
        location_id = entry.get('field_location_id')
        if not isinstance(location_id, str) or not location_id:
            raise FarmMapError(f'field {number}: field_location_id is missing or invalid')
        mapped_number = numbers.get(location_id)
        if isinstance(mapped_number, bool) or not isinstance(mapped_number, int) or mapped_number != number:
            raise FarmMapError(f'field {number}: field_numbers does not match location {location_id!r}')
        field_meta[number] = entry

    exported_ends = {}
    rows_by_field: dict[int, list[PlanRow]] = {}
    docks: list[FarmDock] = []
    routes: list[FarmRoute] = []
    last_field = None
    count = 0
    features = doc.get('features')
    if not isinstance(features, list):
        raise FarmMapError('features must be a list')
    for i, feature in enumerate(features):
        what = f'feature {i}'
        if not isinstance(feature, dict):
            raise FarmMapError(f'{what}: must be an object')
        fp = feature.get('properties')
        if not isinstance(fp, dict):
            raise FarmMapError(f'{what}: properties must be an object')
        if fp.get('role') == 'dock':
            docks.append(_dock(feature, fp, what))
            continue
        if fp.get('role') == 'route':
            routes.append(_route(feature, fp, what))
            continue
        if fp.get('role') != 'row':
            continue
        count += 1
        if count > MAX_ROWS:
            raise FarmMapError(f'more than {MAX_ROWS} rows')
        number = _int(fp.get('field'), f'{what}: field', 1)
        row = _int(fp.get('row'), f'{what}: row', 0)
        frag = _int(fp.get('frag', 0), f'{what}: frag', 0)
        if number not in field_meta:
            raise FarmMapError(f'{what}: field {number} is not listed in the map')
        if fp.get('farm_map_id') != map_id:
            raise FarmMapError(f'{what}: row belongs to map {fp.get("farm_map_id")!r}, not {map_id!r}')
        if fp.get('name') != f'F{number}R{row}':
            raise FarmMapError(f'{what}: row name {fp.get("name")!r} should be F{number}R{row}')
        geometry = feature.get('geometry')
        if not isinstance(geometry, dict) or geometry.get('type') != 'LineString':
            raise FarmMapError(f'{what}: row is not a LineString')
        if number != last_field and number in rows_by_field:
            raise FarmMapError(f'rows of field {number} are not together')
        row_points = _points(geometry.get('coordinates'), what)
        names = fp.get('node_names', [])
        if isinstance(names, list) and len(names) == len(row_points) and len(names) >= 2:
            for name, role in ((names[0], 'entry'), (names[-1], 'exit')):
                if isinstance(name, str):
                    if not re.fullmatch(fr'F{number}R{row}N[0-9]+', name):
                        raise FarmMapError(f'{what}: invalid exported node {name}')
                    if name in exported_ends:
                        raise FarmMapError(f'{what}: duplicate exported node {name}')
                    exported_ends[name] = (number, row, frag, role)
        last_field = number
        rows_by_field.setdefault(number, []).append(
            PlanRow(row, row_points, frag))
    if not rows_by_field:
        raise FarmMapError('no rows in farm map')
    if list(rows_by_field) != sorted(rows_by_field):
        raise FarmMapError('fields are not in field number order')

    fields = []
    for number in sorted(rows_by_field):
        try:
            check_rows(rows_by_field[number])
        except ValueError as error:
            raise FarmMapError(f'field {number}: {error}') from error
        meta = field_meta[number]
        origin = meta.get('origin')
        if origin is not None:
            if not (isinstance(origin, list) and len(origin) == 2):
                raise FarmMapError(f'field {number}: origin must be [lat, lon]')
            origin = _points([[origin[1], origin[0]], [origin[1], origin[0]]], f'field {number} origin')[0]
        if 'contour' in meta and not isinstance(meta['contour'], bool):
            raise FarmMapError(f'field {number}: contour must be a boolean')
        fields.append(FieldRows(number, rows_by_field[number], meta.get('field_location_id'),
                                meta.get('field_name'), origin, meta.get('contour') is True))
    warnings = [f'field {n} is listed but has no rows' for n in sorted(set(field_meta) - set(rows_by_field))]

    dock_names = [d.name for d in docks]
    if len(set(dock_names)) != len(dock_names) or len({r.name for r in routes}) != len(routes):
        raise FarmMapError('dock or route names are not unique')
    for route in routes:
        if route.from_node not in dock_names:
            raise FarmMapError(f'route {route.name!r}: from_node {route.from_node!r} is not a dock in the map')

    routes = [replace(r, target=exported_ends.get(r.to_node)) for r in routes]
    computed = farm_map_hash(fields, docks, routes)
    if computed != declared:
        raise FarmMapError(f'content_hash does not match the rows and docks (file says {declared[:12]}, '
                           f'rows give {computed[:12]}): the file was changed or damaged')
    return FarmMap(map_id, version, declared, status, fields, props.get('farm_map_name'), props.get('season'),
                   props.get('previous_farm_map_id'), warnings, docks, routes)
