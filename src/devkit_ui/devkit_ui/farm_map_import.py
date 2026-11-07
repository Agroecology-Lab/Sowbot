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

This module only validates and describes. It does not touch the topological map.
"""
import hashlib
import json
import math
import re
from dataclasses import dataclass, field

from devkit_ui.row_plan_format import (
    MAX_BYTES,
    MAX_POINTS_PER_ROW,
    MAX_ROWS,
    SCHEMA_VERSION,
    PlanRow,
    canonical_rows_text,
    check_rows,
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


def farm_map_hash(fields: list[FieldRows]) -> str:
    """Return the farm map hash LiteFarm computes: SHA-256 of ``[[field,<rows>],...]`` by field number."""
    parts = [f'[{f.field_number},{canonical_rows_text(f.rows)}]'
             for f in sorted(fields, key=lambda f: f.field_number)]
    return hashlib.sha256(f'[{",".join(parts)}]'.encode()).hexdigest()


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
        points.append((float(c[1]), float(c[0])))
    return points


def parse_farm_map(text: str, *, accept_retired: bool = False) -> FarmMap:
    """Parse a LiteFarm farm map export and verify its content hash.

    Checks: version 1; ``multi_field``; a ``farm_map_id`` and ``content_hash`` in the allowed format; status
    ``published`` (``retired`` only with ``accept_retired``, because a retired map has been replaced); every row has
    a field and row number, belongs to this map and is named ``F<field>R<row>``; fields are listed in
    ``field_numbers``; each field's rows are together; and the hash recomputed from the rows equals ``content_hash``.

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

    rows_by_field: dict[int, list[PlanRow]] = {}
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
        last_field = number
        rows_by_field.setdefault(number, []).append(
            PlanRow(row, _points(geometry.get('coordinates'), what), frag))
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

    computed = farm_map_hash(fields)
    if computed != declared:
        raise FarmMapError(f'content_hash does not match the rows (file says {declared[:12]}, '
                           f'rows give {computed[:12]}): the file was changed or damaged')
    return FarmMap(map_id, version, declared, status, fields, props.get('farm_map_name'), props.get('season'),
                   props.get('previous_farm_map_id'), warnings)
