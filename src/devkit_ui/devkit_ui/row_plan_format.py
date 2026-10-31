"""The row plan GeoJSON format that LiteFarm imports (schema version 1). No ROS imports.

The format is written down in docs/row-plan-geojson.md. LiteFarm checks a robot plan against the same
limits and recomputes the same hash (packages/api/src/util/rowPlan.js), so both sides must agree to the
last digit. tests/row_plan_vectors.json holds vectors that both repositories test against.
"""
import hashlib
import math
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

SCHEMA_VERSION = 1
MAX_ROWS = 2000
MAX_POINTS_PER_ROW = 5000
MAX_BYTES = 5_000_000
MAP_ID_PATTERN = re.compile(r'^[A-Za-z0-9._:-]{1,100}$')
HASH_PATTERN = re.compile(r'^[0-9a-f]{64}$')
_COORDINATE_STEP = Decimal('1e-7')
_EARTH_RADIUS_M = 6_371_008.8


class RowPlanError(ValueError):
    """The rows cannot be written as a plan that LiteFarm will accept."""


@dataclass(frozen=True)
class PlanRow:
    """One row in driving order. ``points`` are (latitude, longitude) in degrees."""

    row: int
    points: list[tuple[float, float]]
    frag: int = 0


@dataclass
class PlanProperties:
    """Top-level properties of a robot plan. ``map_id`` and ``map_hash`` are set by the caller."""

    map_id: str
    origin: tuple[float, float]
    generator: str = 'sowbot-topo-rows'
    supersedes: str | None = None
    field_location_id: str | None = None
    inputs_hash: str | None = None


def format_coordinate(value: float) -> str:
    """Return ``value`` as text with exactly 7 decimals, rounding the exact binary value half away from zero.

    LiteFarm does the same with JavaScript's toFixed(7).
    """
    text = format(Decimal(value).quantize(_COORDINATE_STEP, rounding=ROUND_HALF_UP), 'f')
    return '0.0000000' if text == '-0.0000000' else text


def canonical_rows_text(rows: list[PlanRow]) -> str:
    """Return ``[[row,frag,[[lon,lat],...]],...]`` in the given order, with no whitespace."""
    parts = []
    for plan_row in rows:
        points = ','.join(f'[{format_coordinate(lon)},{format_coordinate(lat)}]' for lat, lon in plan_row.points)
        parts.append(f'[{plan_row.row},{plan_row.frag},[{points}]]')
    return f'[{",".join(parts)}]'


def map_hash(rows: list[PlanRow]) -> str:
    """Return the SHA-256 of the canonical text of ``rows`` as lower-case hex.

    The hash covers geometry and order only. The rows must already be in mission order.
    """
    return hashlib.sha256(canonical_rows_text(rows).encode('utf-8')).hexdigest()


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Return the great-circle distance in metres between two (lat, lon) points, as LiteFarm measures it."""
    lat_a, lat_b = math.radians(a[0]), math.radians(b[0])
    half_dlat, half_dlon = (lat_b - lat_a) / 2, math.radians(b[1] - a[1]) / 2
    h = math.sin(half_dlat) ** 2 + math.cos(lat_a) * math.cos(lat_b) * math.sin(half_dlon) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def row_length_m(plan_row: PlanRow) -> float:
    """Return the length of a row in metres."""
    pts = plan_row.points
    return sum(distance_m(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def check_rows(rows: list[PlanRow]) -> None:
    """Raise RowPlanError for rows LiteFarm would refuse."""
    if not rows:
        raise RowPlanError('no rows in plan')
    if len(rows) > MAX_ROWS:
        raise RowPlanError(f'more than {MAX_ROWS} rows')
    seen = set()
    for plan_row in rows:
        if plan_row.row < 0 or plan_row.frag < 0:
            raise RowPlanError(f'row {plan_row.row}: row and frag must not be negative')
        if (plan_row.row, plan_row.frag) in seen:
            raise RowPlanError(f'row {plan_row.row} fragment {plan_row.frag} appears twice')
        seen.add((plan_row.row, plan_row.frag))
        if not 2 <= len(plan_row.points) <= MAX_POINTS_PER_ROW:
            raise RowPlanError(f'row {plan_row.row}: needs 2 to {MAX_POINTS_PER_ROW} points')
        for lat, lon in plan_row.points:
            if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
                raise RowPlanError(f'row {plan_row.row}: coordinate ({lat}, {lon}) is not a valid position')


def build_geojson(rows: list[PlanRow], props: PlanProperties) -> dict:
    """Return the plan as a GeoJSON FeatureCollection with the hash of ``rows`` filled in.

    Coordinates are rounded to 7 decimals (about 1 cm), so the file holds exactly what the hash covers.
    Raise RowPlanError if LiteFarm would refuse the plan.
    """
    if not MAP_ID_PATTERN.match(props.map_id):
        raise RowPlanError('map_id may only use letters, digits and . _ : - (up to 100 characters)')
    if props.supersedes is not None and not MAP_ID_PATTERN.match(props.supersedes):
        raise RowPlanError('supersedes must be a map_id')
    rounded = [
        PlanRow(r.row, [(round(lat, 7), round(lon, 7)) for lat, lon in r.points], r.frag) for r in rows
    ]
    check_rows(rounded)
    properties: dict = {
        'version': SCHEMA_VERSION,
        'generator': props.generator,
        'source': 'robot',
        'origin': [round(props.origin[0], 7), round(props.origin[1], 7)],
        'map_id': props.map_id,
        'map_hash': map_hash(rounded),
    }
    for key, value in (('inputs_hash', props.inputs_hash), ('supersedes', props.supersedes),
                       ('field_location_id', props.field_location_id)):
        if value is not None:
            properties[key] = value
    features = [
        {
            'type': 'Feature',
            'properties': {'role': 'row', 'row': r.row, 'frag': r.frag},
            'geometry': {'type': 'LineString', 'coordinates': [[lon, lat] for lat, lon in r.points]},
        }
        for r in rounded
    ]
    return {'type': 'FeatureCollection', 'properties': properties, 'features': features}
