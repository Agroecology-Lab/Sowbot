# Row plan GeoJSON (schema version 1)

Format for exchanging planned rows between Sowbot and LiteFarm. It is the web planner's `swaths.geojson` plus properties that identify a plan made from a topological map.

Producer: `devkit_ui/row_plan_export.py` (`python -m devkit_ui.row_plan_cli`).
Consumer: LiteFarm `POST /row_plan/import` (`packages/api/src/util/rowPlan.js`).

## Document

A GeoJSON `FeatureCollection`. Coordinates are `[lon, lat]`, rounded to 7 decimals.

### Top-level `properties`

| Key | Type | Notes |
|---|---|---|
| `version` | int | Must be `1`. |
| `source` | string | `robot` for plans from a topological map. |
| `origin` | `[lat, lon]` | First point of the first row. |
| `map_id` | string | `^[A-Za-z0-9._:-]{1,100}$`. Required when `source` is `robot`. |
| `map_hash` | string | 64 lower-case hex. Required with `map_id`. See below. |
| `generator` | string | Free text, up to 255 characters. |
| `inputs_hash` | string, optional | SHA-256 of the map's row nodes. Not checked by LiteFarm beyond format. |
| `supersedes` | string, optional | `map_id` of the plan this replaces. Must exist on the farm. |
| `field_location_id` | string, optional | If present, must equal the `location_id` of the import request. |

### Features

One `LineString` per row fragment, `properties`: `{"role": "row", "row": <int>, "frag": <int>}`.

- `row`: the `row_id` of the map's nodes. Non-negative.
- `frag`: 0 unless a row is split. `(row, frag)` is unique.
- Feature order is mission (driving) order. Points run entry to exit.
- 2 to 5000 points per row, at most 2000 rows, file at most 5 000 000 bytes.

## Hash

`map_hash` is the SHA-256 (hex, lower case) of the UTF-8 canonical text:

```
[[row,frag,[[lon,lat],...]],...]
```

- Rows in mission order. No whitespace.
- Each coordinate is exactly 7 decimals, the exact binary value rounded half away from zero (`toFixed(7)` in JavaScript, `Decimal.quantize(1e-7, ROUND_HALF_UP)` in Python).
- `-0.0000000` is written `0.0000000`.
- The hash covers geometry and order only, not `map_id`.

LiteFarm recomputes the hash and refuses the plan on mismatch. Vectors: `tests/row_plan_vectors.json`. Both repositories test against it.

## Mapping from a topological map

- Nodes with a `row_id` and `row_role` in `entry`, `waypoint`, `exit` belong to a row. Other nodes are ignored and counted.
- Each row needs exactly one entry and one exit.
- Point order: entry, waypoints in edge order (edges whose action is not `navigate_to_pose`; if edges do not reach every waypoint, by the number in the `_W<n>` name), exit.
- Point position is the node's `gps_lat`, `gps_lon`. A node with `0, 0` is refused.
- Entry and exit at the same GPS position is refused.
- A warning is given where a row's length on the map differs from its length by GPS by more than 0.5 m and 5 %.
- Default `map_id`: `<map name>-<first 12 hash characters>`, with characters outside the allowed set replaced by `_`.

## LiteFarm import rules

- `location_id` must be a field or garden on the farm.
- Rows with a vertex more than 5 m outside the field are refused for robot plans.
- Same `map_id`, hash and field: returns 200, nothing created. Same `map_id`, different rows or field: 409.
- User needs the `edit:row_plan` permission.

## CLI

```
python -m devkit_ui.row_plan_cli maps/maize_map -o rows.geojson
LITEFARM_PASSWORD=... python -m devkit_ui.row_plan_cli maps/maize_map \
    --import-to http://localhost:5000 --email USER --farm-id FARM --location-id FIELD
```

Exit status: 0 success, 1 map or import refused, 2 bad arguments.
