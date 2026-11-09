<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:d3ba7f8c212a401ee166eefd529668a6df821e27e4357d514978865a5e03cb98 -->

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

### Docks and routes (farm map)

The farm map is a separate multi-field export from LiteFarm’s published farm-map
GeoJSON endpoint, `GET /farm_map/farm/:farm_id/published/geojson`. It is imported
separately from the row plan and may carry `dock` and `route` features.
The robot saves them as topological nodes.
They are part of the farm map's `content_hash` and are also checked for form.

- Dock: `Point`, `properties`: `{"role": "dock", "name": <string>, "heading_deg": <number or null>}`. Names are unique.
- Route: `LineString`, `properties`: `{"role": "route", "name": <string>, "from_node": <dock name>, "to_node": "F<field>R<row>N<n>"}`. Route names are unique.
- The first route point is the dock and the last is the row end. Points between them are the route's waypoints.
- Node names: `<prefix>_D_<NAME>` for a dock, `<prefix>_D_<ROUTE>_<k>` for the k-th waypoint.
- Edges are `navigate_to_pose`, both ways, along dock, waypoints and row end.
- Route targets must match an exported row endpoint in that fragment’s `node_names`.
  The exact exported identity is preserved; an arbitrary nonzero index is not an exit alias.
- Supplied route endpoints must match the named dock and row endpoint at 7 decimal places.
- A route whose row is not in the map is skipped with a warning. Its dock is still saved.
- `heading_deg` is stored in the dock node's metadata and is not used for its pose.

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

### Farm-map `content_hash`

Sort fields by ascending field number, keeping each field’s rows in mission order.
The canonical field text is `[[field,<canonical rows>],...]`, using the same row
serialization and coordinate rules as `map_hash` above.

If there are docks or routes, append one literal semicolon (`;`), then the canonical
feature array. Sort docks by name first, followed by routes sorted by name:

```
["dock",name,heading,null,null,[[lon,lat]]]
["route",name,null,from_node,to_node,[[lon,lat],...]]
```

Enclose these entries in a single array, separated by commas, with no whitespace.
Names and endpoint identities are quoted valid IDs. Heading is `null` or a JavaScript
number string (`1`, `0.000001`, `1e-7`); negative zero is `0`. Fractional magnitudes
from 1e-6 inclusive to 1e21 exclusive use decimal notation; others use scientific
notation with no leading exponent zero. Coordinates always use exactly 7 decimals.
The exact hash input is the UTF-8 field text plus that optional suffix, with no newline.
With no docks or routes, omit both the semicolon and feature array.
`content_hash` is the lower-case hexadecimal SHA-256 of those bytes.

Compatibility vector (one line; no trailing newline):

```
[[1,[[0,0,[[3.6600000,48.0000000],[3.6610000,48.0000000]]]]]];[["dock","dock",1,null,null,[[3.6615000,48.0039000]]],["dock","dock1",null,null,null,[[3.6614000,48.0038000]]],["route","route1",null,"dock","F1R0N0",[[3.6615000,48.0039000],[3.6617000,48.0040000],[3.6619000,48.0041000]]]]
```

Expected hash: `08cad4e963a19ebc8abf3fa58edb96e3d34e72e063653c4cc688aad4f5120194`.

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

## Web UI

System tab, Tools card: **LiteFarm GeoJSON export** downloads the loaded map's plan as `<map_id>.geojson` and shows the row count, length and `map_id`, or the reason the map cannot be exported. The file has no `field_location_id`; LiteFarm takes the field from the import request.


<!-- /coderabbit-ai-generated:v1 -->