# Robot mission intake spec 

Status: draft, rev 6. Built so far: the SQLite store with the remote-mission columns, `MissionStore.add_remote` and `find_by_external_id`, SQLite-assigned ids that are never reused, ending any mission left running by a restart, no delete of a running mission, `run_saved` with outcome recording, name-based `resolve_steps`, locked store reads, the structured mission result, and `today_queue()` skipping remote missions. The farm map import into the topo map with `hash8`, the edit hook that clears it, and the (field, row) lookup are built. So is the import of the farm map's docks and routes into the topo map (covered by the hash), but no mission code reads those nodes and nothing says which dock is the charger or a bay. Not built: the stall watchdog, the intake, the link layer and its wifi carrier, the charge, park and autonomous return routines, the battery return, resume from a waypoint, and the station. Nothing calls `add_remote` yet.

Rev 6 checked against Sowbot caatinga-dev at d7835e1 on 11 Nov 2026 by reading the missions package, the farm map import and the dock builder. Nothing outside `add_remote`, `find_by_external_id` and `resolve_lf_row`'s own tests calls them. Tests re-run with pytest: 200 pass across the eight files listed under "Test plan". The dock builder, farm-map-to-topo and edit-service tests would not import in the check environment (a missing dependency), so the dock import is read, not re-tested. The sections not about docks were not re-read line by line, and "What exists" still says 1885cbb. Everything about wifi, charging, docking, `soc`, the autonomous return and the resume point is new and is a proposal; none of it exists. The store was simplified earlier: YAML backend removed, no epochs, no `task_id`, random station-chosen mission ids. Nothing ran under colcon, under ROS or on hardware. Companion: Station service spec rev 5, which defines the messages. This spec follows it.

Messages are named in plain words here: mission, cancel, status query, accepted, rejected, end report and hello. The [station spec gives the exact formats.](https://github.com/Agroecology-Lab/Sowbot_Station/blob/main/README.md)

## Purpose

Let one station send a mission to a robot over a link (wifi first, MeshCore possible later) and get the outcome back. The messages are the station spec's: one ASCII line of at most 158 bytes, independent of the carrier. Missions made on the robot and missions sent from the station share one store, one start path and one result record.

Remote missions are of three kinds: `field` (rows from the farm map), `charge` (drive to the shared charger and dock) and `park` (drive to the robot's own wait bay). The station schedules the charger queue. Between dock visits the robot is out of contact and runs on its own: it returns to its bay by itself at the end of every mission and when charge falls to its return level.

The topological map stays the source of truth for rows. Every robot holds the same map, and nothing about rows is kept outside it. The charger and bay locations are not rows. They come in the same farm map as docks and routes, and the import builds them into the topo map (see "Dock and bay locations").

This is not a safety function. E-stop and the independent safety subsystem are unchanged. Remote cancel is best effort. Separating robots near the dock is avoid's job over MeshCore (simulation only so far). The station does not detect or stop a collision.

## Assumptions

- Station traffic goes over wifi at first and must also work over MeshCore later. Each robot has one MeshCore radio, used for avoid only for now (channel, no acks). If station traffic moves to MeshCore it shares that radio (direct messages, with ack).
- **There is no wifi in the field.** The station reaches a robot only within about 10 m of it. The charger and all four bays are in range. A robot completes its mission with no station contact and keeps its end report until the carrier reports it delivered.
- A robot is stopped when it receives a mission. It moves and sends avoid frames only while on a mission. **Every move counts as a mission for avoid, including the autonomous return.**
- 2 to 4 robots over about 1 mile square, sharing one charger. Each has its own wait bay beside it (bay *n* for robot *n*), used before or after charging. Slow robots, long rows, missions lasting hours.
- There are one-way in and out routes between the field and the dock area (keep left). The layout is not decided.
- The robot sends nothing unprompted except a reply to a mission message (accepted or rejected), an end report when a mission ends, and a hello message on boot. Nothing is retried on a timer.

## Map generation

In the diagram, `[new]` marks what is not built. `built:` marks what exists on mission-refactor.

```

LiteFarm: published farm map (GeoJSON, content_hash)
   |
   | export file, once per season
   v
+==================== ROBOT 1 (imports map) ===========================+
|                                                                      |
|  parse_farm_map()            built, called by import_plan            |
|    checks: published, F<f>R<r> names, content_hash recomputed        |
|    -> FarmMap {fields, rows+frag, content_hash}                      |
|                                                                      |
|  import path                                                         |
|    parse_plan -> topo_row_builder                                    |
|    built: keep field/row/frag per swath (multi-field files only)     |
|    built: robot_row_id (field*10000+row) removed                     |
|                                                                      |
|  WRITES: topo map (YAML)                                             |
|  +----------------------------------------------------------------+  |
|  | doc.meta                                                       |  |
|  |   hash8: c50f60d8        built: first 8 of content_hash        |  |
|  | nodes (entry / exit / waypoints per swath)                     |  |
|  |   meta.row_id    = row_id_start + i   (per swath, set once)    |  |
|  |   meta.row_role  = entry | exit                                |  |
|  |   meta.lf_field  built (multi-field files only)                |  |
|  |   meta.lf_row    built (multi-field files only)                |  |
|  |   meta.lf_frag   built (multi-field files only)                |  |
|  |   edges: in-row + headland (no headland across field/frag)    |  |
|  |   docks: node <prefix>_D_<n>; route points _D_<ROUTE>_<k>      |  |
|  |     edges to the route's row end   built, hash covers them     |  |
|  +----------------------------------------------------------------+  |
|  edit service: add/delete node/row, f2c save, repair                 |
|     -> built: any edit sets hash8 to "-" (robot then rejects "map") |
|                                                                      |
|  WRITES: dock roles [new]: which dock is the charger, own bay no.    |
|     robot-local; the topo map is not touched                         |
|                                                                      |
|  WRITES: missions.db (SQLite, /workspace/maps/missions.db)           |
|  +----------------------------------------------------------------+  |
|  | original: id name rows(entry node names) action action_params  |  |
|  |           repeat_every_hours active created_at                 |  |
|  |           last_run_at last_run_success                         |  |
|  | built:    source(local|remote) external_id map_hash            |  |
|  |           groups(lf row per entry) state(idle|running|ended)   |  |
|  |           steps_done stage reason started_at ended_at          |  |
|  |           result_sent                                          |  |
|  +----------------------------------------------------------------+  |
+==========================+===========================================+
                           |
                           | copy topo map file (how: not yet read)
              +------------+-------------+
              v                          v
   +--------------------+     +--------------------+
   |  ROBOT 2           |     |  ROBOT 3           |
   |  placeholder       |     |  placeholder       |
   |  same topo map:    |     |  same topo map:    |
   |   same row_ids     |     |   same row_ids     |
   |   same lf_* meta   |     |   same lf_* meta   |
   |   same hash8       |     |   same hash8       |
   |  own missions.db   |     |  own missions.db   |
   |  own dock roles    |     |  own dock roles    |
   |  (own bay number)  |     |  (own bay number)  |
   |  never sees farm   |     |  never sees farm   |
   |  map               |     |  map               |
   +--------------------+     +--------------------+
   ```

Each robot resolves (field,row) by scanning node meta (lf_field, lf_row,
lf_frag sorted by row_id), so the station never needs robot row ids.

`resolve_lf_row` in `missions/lf_rows.py` does the lookup. Nothing calls it yet. `resolve_steps` still resolves integer row ids and entry node names only, so the intake has to turn a (field, row) into entry names first. The copy to the other robots is not built either. The sim page has "Save copy as" and archive for the local map, and the system page exports a LiteFarm row plan. No robot-to-robot copy exists in the UI.


## Intake behaviour

Not built. This section is the design.

### Receiving

1. Accept only lines the adapter has verified as from the station. Drop anything else with no reply (the wifi adapter returns an HTTP error).
2. Parse strictly. If the mission id can be read but another field is bad, reject the message with the reason `fmt`. If nothing can be read, drop it.
3. Look the mission id up with `find_by_external_id` before anything else. A known id with the same action and rows (for `park`, the same bay) returns the stored state (accepted, or ended) and never runs a second time. A known id with a different action or rows is rejected with the reason `id`, which covers a station bug or an id collision. Order and age of messages are not checked.

### Accepting a mission

Checks run in this order, each with its own rejection reason:

1. busy: a mission is running or starting.
2. estop: the confirmed soft e-stop is active.
3. map: for a `field` action only, the map is edited, or hash8 differs from the map's hash8.
4. action: not in `ACTIONS`, and not `charge` or `park`.
5. row: for a `field` action only, a (field, row) is missing from the map, is duplicated, or lacks an entry or exit node, or `@k` is not a waypoint of the first row. Run the same resolution as `resolve_steps` here.
6. dock: for `charge` or `park` only, the map has no charger dock or no route from it, the robot's own bay number is not set, or `park` names a bay that is not this robot's.
7. battery: for a `field` action only, `soc` is not available, or is below the robot's start level `start_soc` (above its return level). Not applied to `charge`, because a low robot must be able to reach the charger (a short drive from its bay, inside the return reserve), or to `park`, because a robot at the charger must be able to leave it.
8. nav: navigation is unavailable.

Then write the mission with `add_remote` (stored idle) and call `run_saved`, which marks it running. If `start()` delivers an ERROR on the calling thread, delete the record and reject the mission with the reason `nav` (or `busy` for "mission already running"). The record is idle again by then, so `delete` allows it. Otherwise accept it, reporting no rows done and the current `soc`.

The domain service takes its own lock when starting, so a simultaneous start from the web UI loses cleanly and shows up as that ERROR.

### While running

- A status query for the mission returns a progress reply: the mission is accepted and running, with the number of whole rows done and the current `soc`. This is the only progress the robot reports, and only when asked.
- In the field the robot is out of contact, so queries and cancels reach it only while it is at the dock. A cancel cannot be sent to a robot already in the field.
- A cancel message for the running mission calls `cancel()`. The outcome comes back in the end report with the reason `cancel`. Cancelling an ended mission re-sends its end report. Cancelling an unknown mission is rejected with the reason `unknown`.
- Nothing else is sent. No timers, no progress broadcasts.

### Ending

The result is already in the store when the executor finishes. Send the end report through `send_line` (up to three attempts in the adapter) without blocking the executor thread. In the field every send fails, which is expected: `result_sent` stays false and the robot does not retry on a timer. When the robot is back in range the station probes it, sends a hello request, then a status query for any open mission, and the stored end report is re-sent. A report the carrier does not deliver is re-sent at the next query or hello exchange.

- If all rows are done (or a `charge` or `park` finished), send a successful end report.
- Otherwise send a failed end report giving the number of whole rows done, the stage and the reason. For reason `battery` in stage `row`, add `resume_wp` as `k`.

### Boot

The store already ended any mission stored as running, with the reason `reboot` and its stored `steps_done`, when it was attached. Built. Intake then sends a hello message (map hash, state, `soc`), then an end report for each remote mission whose `result_sent` is false. The robot does not resume after a restart. A status query for mission 0 gets a hello reply. The hello state is `bay` or `charger` when the robot is idle at its own bay pose or on the charger, `busy` while on a mission or returning, and `other` if idle anywhere else.

## Limits

None of these is enforced yet, because there is no intake.

- Messages are at most 158 bytes. `rows` holds integers and ascending ranges only, at most 200 rows per mission (tune after testing). The first row may carry `@k`.
- One mission at a time, as the domain service enforces.
- A mission carries one action and its default params. Per-row actions are not supported.

## Test plan

The tests are in `src/devkit_ui/test`: `test_mission_store`, `test_mission_sqlite_store`, `test_mission_domain_service`, `test_mission_application_service`, `test_mission_boundaries`, `test_mission_view_model`, `test_farm_map_import` and `test_plan_import`.

Written and passing (200 tests in these eight files on 11 Nov 2026 with pytest at d7835e1, not run under colcon; `test_topo_dock_builder`, `test_farm_map_to_topo_map` and `test_topology_edit_service` did not import in the check environment, so they were not run):

- Store: `add_remote` and its refusals, `find_by_external_id`, ids not reused after a delete, no delete of a running mission, `attach` ending missions left running (local and remote), schema migration of an existing database, remote missions excluded from `today_queue` even after a failed run, concurrent reads and writes, and result storage.
- Run path: `run_saved` marks the mission running, stores the result and re-arms the schedule, makes a remote mission inactive, and keeps the previous result when the start is refused. `cancel` against `estop` is reported correctly, as is a failing result callback and an executor exception.
- Import: `parse_farm_map` and its hash check, including `hash8`.

Not written yet:

- Unit: message parser and bad input, check ordering (including the new `dock` and `battery` checks, and a `charge` accepted below `start_soc`), a known mission id with the same and with different rows or bay, whole-row counting across fragments.
- Store: `add_remote` accepting `charge` and `park` with no rows and no map hash, and still refusing the same for `field`.
- Import: a farm map with a split row gives distinct row ids and keeps field, row and fragment in node metadata. This needs the import path to be built first.
- Dock roles: a change to the robot-local dock roles or own bay number leaves `hash8` alone, and an edit that drops a dock node turns `hash8` into `-`.
- Reports: an unsent end report is re-sent at the next status query and at boot, and a repeated report does no harm.
- Battery return: stops at the return level, reports `battery` with stage `row` and `resume_wp` (or `transit` between rows), and returns to the bay by the in route. A mission with `@k` starts that row at that waypoint, and a bad `@k` is rejected with `row`.
- Simulation: an in-process fake carrier (like `LoopBus`) in place of the station for intake, then the wifi adapter with an HTTP test client, then the Gazebo multi-row mission, including a failed transit, a cancel and a `charge` then `park` cycle with a fake charger.
- On the real session before relying on it: wifi drop-outs during a mission and during the end report, the endpoint under the web server's auth, and docking with the real charger.
- No claim is made about behaviour on a wifi link, a charger or a radio until it has run on one.



## What exists (checked at 1885cbb)

- MissionStore keeps missions in SQLite (`/workspace/maps/missions.db`) and has no other backend. A mission is a list of entry node names, one action, `last_run_at` and `last_run_success`. It also has the remote-mission columns and the run result (`state`, `steps_done`, `stage`, `reason`, `started_at`, `ended_at`, `result_sent`). A database from before these columns gains them when opened, with `source` local and `state` idle. The UI can save, activate, delete and run saved missions. It shows the last-run tick or cross. The run result is stored but the UI does not show it.
- `MissionStore.add()` takes the local fields only. `MissionStore.add_remote(external_id, rows, action, map_hash, groups)` stores a station mission idle and active. It returns None and sets status for an empty id or map hash, rows and groups of different lengths, a validation error, or an `external_id` already stored. **This refuses a `charge` or `park` mission, which has no rows, no groups and no map hash. It needs relaxing for those kinds (see section 2).** `find_by_external_id` looks a mission up by the station's id. Nothing outside the tests calls either. `MissionStore.record_result()` writes the run result.
- Mission ids are allocated by SQLite (`AUTOINCREMENT`, and `add` takes the id from the insert), so a deleted mission's id is not reused. `MissionStore.delete()` refuses a mission that is running, because its run still has to write a result.
- `MissionStore.attach()` ends every mission stored as running, with the reason `reboot`, because a restart does not resume a run. `steps_done` and `stage` keep their last values. A remote mission also becomes inactive and its end report is marked unsent. `last_run_at` and `last_run_success` are left alone, because the run did not finish. Only one process may attach to a database: a second one would end a mission that is really running.
- A saved local mission runs from its play button through `run_saved`, which records state, steps done, stage and reason and calls `record_run()`. Remote missions get no play button and are labelled "station-controlled". `today_queue()` skips remote missions and still has no caller, so nothing schedules a mission. The main Run Mission button runs the browser session's queue through `start()` with no result callbacks and records nothing. That is deliberate: a queue run is not a saved mission, and a record per run would fill the store.
- The store holds entry node names and `start()` wants integer row ids. `resolve_steps` accepts integer ids, entry node names, bare id strings and the saved `ROW_<id>_IN` fallback. It rejects an unknown name and two entry nodes for one row. An entry node named like a fallback wins over it, so `save_queue` stores the integer row id instead of `ROW_<id>_IN` when a node of that name exists.
- `MissionApplicationService.start(queue, on_status, on_nav_update, on_result, on_step_done)` reports every failure through `on_status(ERROR, ...)` and returns False. It returns True once the worker has started.
- `MissionDomainService` runs one mission on a thread. Per row: go to the entry, enable the tool, go to the exit, disable the tool. It checks cancel and the soft e-stop before each leg and stops at the first failure. It reports text progress through `on_status`, the count of finished steps through `on_step_done` and one `MissionResult` through `on_result`. It has no battery check and no dock or park routine.
- `navigate_and_wait_result` returns `arrived` or a reason. `MissionApplicationService._navigate` gives each goal a fixed 300 s (`NAV_TIMEOUT_SEC`). `navigate_and_wait` is a bool wrapper used by the Run tab. `NavUpdate` carries a status, a `navigating` flag and an outcome, with no measure of progress.
- Row nodes carry `row_id` and `row_role` in their metadata. Row ids are per swath (`row_id_start + i`, with `row_id_start` chosen at save time). `parse_plan` returns `Plan.lf_rows`, each swath's LiteFarm `(field, row, frag)` as the file gives it, before `_merge_fields` offsets the row numbers. It passes through `PlannedRows`, `stage_f2c_save` and `RowBuildSettings` to `build_row_nodes`, which writes `lf_field`, `lf_row` and `lf_frag` to the metadata of every node of the swath, and refuses a count that differs from the swaths. Only multi-field files carry a field number. For any other file `lf_rows` is empty and the nodes have no `lf_*` keys. A missing `frag` is 0. The metadata survives a YAML write and read (tested).
- `farm_map_import` (PR #213) parses and hash-checks the published farm map. `PlanApplicationService.import_plan` calls it for any file that names a `farm_map_id`, so a retired, edited or unhashed map is refused. It then reads the same text with `parse_plan` and refuses the import if the two readers disagree on the rows. The farm map may also carry `docks` and `routes` (see "Dock and bay locations"). `PlannedRows.map_hash8` carries `FarmMap.hash8` to the save. A farm map import builds rows with waypoints if any field is a contour field. `robot_row_id`, `ROW_ID_STRIDE` and `FarmMap.row_ids` are removed.
- `TopoDoc` has `map_hash8` (the document meta key `hash8`, or `-`), `stamp_map_hash8` and `mark_map_edited`. `persist_and_reload(modify, map_hash8=None)` stamps the hash when given one and otherwise marks the saved map edited. `drop_node` and `patch_node_role` mark it edited. `stage_drop` and `stage_repair` also mark the live map at once, because they change it before the write. A map with no `hash8` key (not built from a farm map) stays without one and reads as `-`.
- `stage_f2c_save(map_hash8=...)` stamps only when the save leaves the map's row nodes exactly the farm map's rows and its dock and route nodes exactly the farm map's docks and routes: every swath built, none skipped, no route left out, no built name already taken by a node that stays, no other row, dock or route nodes in the map. Otherwise the map is marked edited and the status says remote missions are refused. A second import needs `overwrite` to keep the stamp.
- `TopoDoc` has a document-level `meta` dict. The loader (`parse._topo_from_dict`) reads it and `to_dict` writes it. `clone_empty` and `renamed` copy it. Node metadata is read and written the same way.
- sowbot_avoid (`src/devkit-meshcore-avoid/pkg/sowbot_avoid`) is a separate package with its own process. Its `MeshRadio` opens the serial port and requires the configured channel (it raises if the radio has none). It subscribes to channel data, starts auto message fetching and sends zero-hop by default (`--flood` allows repeaters). `LoopBus` and `LoopRadio` are an in-process fake radio. `devkit_ui` does not import it. Simulation only. With rev 5 it is the only user of MeshCore on the robot.

## Changes from rev 5 (this revision)

- **Docks and routes are in the farm map and the hash.** The import builds them into the topo map. The robot-local "dock layer" shrinks to dock roles and the robot's own bay number. Roles, route direction and dock lookup are still open (see "Dock and bay locations").
- **`charge` is no longer subject to the battery start check.** It applied to `charge` in rev 5, which rejected exactly the robots the station queues first (`critical`). Only `field` is checked. `park` was already exempt.
- **Rows, not steps, on the wire.** Intake converts `steps_done` to whole LiteFarm rows with `groups` when it builds `A` and `E`. The station never sees steps. Not built.
- **Start level named `start_soc`.** A robot parameter above `return_soc`. The station's `min_mission` is a hint only and must not be lower.
- Test count corrected. Rev 5 gave 217 in one place and 179 in another.

## Changes from rev 4 (rev 5)

- **Intake is carrier-independent.** It takes verified lines and sends lines, and the carrier is an adapter. Wifi is the first adapter, in `devkit_ui`. A MeshCore adapter can be added later without touching intake. The 158-byte line and the no-timers rule from the MeshCore design are kept.
- **Radio ownership is deferred, not removed.** It matters only when a MeshCore adapter is built (section 6).
- **No station contact in the field.** The robot is autonomous between dock visits. Mid-mission queries and cancels reach it only at the dock. The stored end report is collected when it is back in range.
- **Autonomous return.** At the end of every mission and at the return level, the robot drives to its own bay. It counts as moving for avoid.
- **New mission actions** `charge` and `park`, with no rows, no groups and no map hash check. `soc` added to the accepted and progress reply and to hello, hello states `bay`, `charger`, `busy`, `other`. New reject reason `dock`, and `battery` as a reject and end reason.
- **Battery return resumes from the last waypoint.** The robot stops at the return level, records the last waypoint reached in the interrupted row (`resume_wp`), and the next mission starts that row there (`@k`).
- **Dock and bay locations and the in and out routes** were to live in a robot-local layer outside the imported topo map. Superseded in rev 6: docks and routes come in the farm map and the import builds them into the topo map.
- **No dock zone, no `z`.** Separation near the dock is avoid's job.

## Changes from rev 2

- No separate row table and no separate `remote_missions` table. Field and row numbers go into the map, and remote missions go into the existing store.
- One robot imports the farm map each season. Its topo map is then copied to the other robots, so row identity has to travel inside that map.
- The station chooses a random id for each mission, about 10 hex characters, and the robot never interprets it. There is no epoch, no counter, no `intake_epoch` table and no pruning. Replay is answered by the stored record.
- The YAML backend for missions is removed. The default path changed from `missions.yaml` to `missions.db` on 17 Jul 2026 (commit 030f51d). A `missions.yaml` written before that is not imported; copy its missions by hand if one exists.
- `task_id` is not stored on the robot. The station maps its mission id to the LiteFarm task.

## Design

### 1. The map holds the row identity

Built and tested against the repo's farm map fixture and a real LiteFarm export. Not built: copying the map to other robots, and anything that calls the lookup.

- The farm map is built once per season in LiteFarm and exported to one robot, which builds the topo map. On import, `parse_plan` keeps each swath's field, row and fragment numbers, and they are written to the row node metadata as `lf_field`, `lf_row` and `lf_frag`. The document meta gets `hash8`, the first 8 characters of the farm map's content hash, checked once at import with `parse_farm_map`. The finished topo map is then shared to every other robot, which never sees the farm map.
- Row ids stay per swath. `robot_row_id` (field × 10000 + row) is gone. Nothing derives an id from field and row. The map is searched instead. Ids are assigned once, at import, and copied with the map, so they match on every robot.
- To resolve a (field, row), scan the node metadata and sort its fragments by row id.
- A robot's map counts as edited once a node is dropped, moved or removed. The robot then rejects remote field missions with the reason `map` and reports `-` as its map hash in its hello message. This keeps "identical on every robot" true. The hook is in `TopologyApplicationService`, where every edit is written (`persist_and_reload`, `drop_node`, `patch_node_role`), so a new edit kind is marked without being listed. Changing the row action also counts as an edit.
- A new season means a new farm map, a new `hash8` and a new topo map on every robot. Until a robot has it, it rejects remote field missions with the reason `map`. Charge and park missions do not check the map, so a robot with a stale or edited map can still be charged.
- Tested: document meta and node metadata survive save, load, `renamed` and `clone_empty`, and node-drop, f2c-save, repair and delete keep the document meta (they only change `hash8`). Not checked: the external map manager's copy of the map (the live `/topological_map_2` republish goes through the same loader), and how the map reaches another robot, since no copy mechanism exists.
- A single-field plan file has no field number and no farm map, so it gets no `lf_*` and no `hash8`. A robot holding only such a map reports `-` and rejects every remote field mission with `map`.

#### Dock and bay locations

Partly built. The farm map carries docks and routes and the import builds them into the topo map. No mission code reads them, and nothing marks which dock is the charger or a bay.

Built (read at d7835e1, tests not re-run here):

- The farm map's `docks` (Point, `name`, `heading_deg` or null) and `routes` (LineString, `name`, `from_node` a dock, `to_node` a row end `F<field>R<row>N<n>`) are checked for form and hashed after the rows. The same rows with docks hash differently from the rows alone.
- The import saves each dock as a node `<prefix>_D_<n>` and each route point as `<prefix>_D_<ROUTE>_<k>`, joined to the named row end by navigation edges in both directions. A route whose row end was not built is skipped with a warning, and the map is then marked edited (`hash8` is `-`).
- Docks and routes are therefore part of the map every robot copies, and the hash covers them. Editing or dropping one is an edit like any other.

Not built, not decided:

- **Roles.** A dock has only a name and a heading. The robot has to know which dock is the charger and which is its own bay. Bays differ per robot but the topo map is copied unchanged. Proposed, not decided: dock names `charger` and `bay1` to `bay4` in the farm map, and the robot's own bay number as a robot-local setting (`dock roles` in the diagram).
- **Routes.** A route is a line from a dock to a row end. It has no direction and no keep-left flag. Whether the one-way in and out routes are two routes, or one route used both ways, is not decided.
- **Lookup.** Nothing resolves a dock node by name or follows a route from the charger to a bay.
- Navigation to the charger, the bay and along the routes uses the same Nav2 goals as rows.
- A robot whose map has no charger dock, no route from it to a row end, or no own bay number rejects `charge` and `park` with the reason `dock`, and cannot return home by itself. An edited map still accepts `charge` and `park` (they do not check `hash8`), but a dropped dock node makes them fail with `dock`.

### Robot comms

Not built. No code reads or sends any of these messages. The first carrier is wifi HTTP. The messages are the MeshCore-sized ones, so a MeshCore carrier can replace it later.

```
+==================== FARMHOUSE SBC =====================================+
|                                                                        |
|  +---------------------+   HTTP    +--------------------------------+  |
|  | docker: LiteFarm    |<--------->| docker: Station service        |  |
|  |                     |           |                                |  |
|  |  API                |  POST     |  poll loop (tasks)             |  |
|  |  DB                 |  /login   |  probe + dispatch              |  |
|  |                     |           |  result mapping -> PATCH       |  |
|  |  tasks              |  GET      |  charge queue                 |  |
|  |  row plans + rows   |  /task/:farm_id                            |  |
|  |  published farm map |  /row_plan/task/:id/rows                   |  |
|  |  (content_hash)     |  /row_plan/:id                             |  |
|  |                     |  /farm_map/farm/:id/published/geojson      |  |
|  |                     |<----------+ PATCH /row_plan/task/:id/rows  |  |
|  |                     |           |   done | aborted               |  |
|  +---------------------+           |                                |  |
|                                    |  SQLite (volume)               |  |
|                                    |   missions, held tasks,        |  |
|                                    |   unsent PATCHes, queue,       |  |
|                                    |   per-robot hash8 + soc +      |  |
|                                    |   last contact                 |  |
|                                    +--------+-----------+-----------+  |
+=============================================|===========|=============+
                                              |           |
                                     wifi, 10 m only      |
                                              |           | HTTP
                         one-line text        |           v
                         messages, <=158 B    |     +-----------+
                         mid: random, about   |     |  CHARGER  |
                         10 hex characters    |     | occupied, |
                                              |     | fault,    |
                                              |     | charging  |
                                              |     +-----------+
        M|mid|hash8|action|field|rows     --> |
        Q|mid   (hello if mid=0)           --> |
        C|mid                              --> |
        <-- A|mid|n|soc    R|mid|why   E|mid|...   H|hash8|state|soc
                                              |
              +--------------------+----------+----------+
              v                    v                     v
        +-----------+        +-----------+         +-----------+
        | ROBOT 1   |        | ROBOT 2   |   ...   | ROBOT 4   |
        | devkit_ui |        | devkit_ui |         | devkit_ui |
        |  intake + |        |  intake + |         |  intake + |
        |  missions |        |  missions |         |  missions |
        | radio:    |        | radio:    |         | radio:    |
        |  avoid    |        |  avoid    |         |  avoid    |
        |  only     |        |  only     |         |  only     |
        +-----+-----+        +-----+-----+         +-----+-----+
              ^                    ^                     ^
              +--------------------+---------------------+
                 avoid frames: MeshCore channel, zero-hop, no acks
                 (robot to robot, only while on a mission)
```

Link rules: the protocol is carrier-independent (station spec, Link Layer). Contact exists only within about 10 m of the station. On wifi, station to robot is a `POST` of one line, answered `200` on receipt, and the robot's reply line may come in the body or as a separate `POST`. Robot to station is a `POST` of one line, up to three attempts, and `2xx` is delivered. Auth is a per-robot token checked by the wifi adapter, not by intake. It is not replay protection.

### 2. One mission store

Built in `MissionSqliteStore`, except the changes marked new. `_ensure_schema` adds each column of `_ADDED_COLUMNS` that the `missions` table lacks, so an older database keeps its rows. `external_id` has a unique index that ignores nulls. New columns:

| Column | Meaning |
| --- | --- |
| `source` | `local` or `remote` |
| `external_id` | the station's mission id, unique, remote only |
| `map_hash` | hash8 the mission was made against (null for `charge` and `park`) |
| `groups` | LiteFarm row number for each entry in `rows`, null for local, `charge` and `park`, so whole rows can be counted |
| `state` | `idle`, `running` or `ended` |
| `steps_done`, `stage`, `reason` | result of the last run |
| `started_at`, `ended_at` | UTC |
| `result_sent` | remote only, whether the station has acknowledged the end report |
| `resume_wp` | **new.** Remote only. Index of the last waypoint reached in the interrupted row, for a `battery` stop inside a row. Null otherwise |

Rules:

- Remote missions store `rows` as entry node names with every fragment expanded, so the record matches what ran even if the map changes later. `add_remote` stores the rows it is given, so intake does the expansion. Nothing calls it yet.
- **New:** a `charge` mission stores empty `rows`, null `groups` and null `map_hash`. A `park` mission does the same and stores its bay number in `action_params`. `add_remote` must accept these when the action is `charge` or `park`, and keep its refusals for `field` actions. The `action` column holds `charge` or `park`.
- `today_queue()` skips remote missions. Without this, a failed remote mission would count as due for retry, which the current rule does for local ones. Built and tested.
- A remote mission is inactive once it ends, whatever the result. Built in `run_saved`.
- Remote missions are never pruned. A record is a few KB at most, and keeping them all is what makes a replayed message harmless: a known `external_id` returns the stored state and never runs again. With charging, the count grows faster, since each charge cycle adds two missions per robot. Add a cap only if the database grows a problem in practice.
- The SQLite store holds one reentrant lock around every method, reads included, because its connection is shared by several threads. `update()` rejects an unknown `state`, as `add()` does. Built and tested.
- A mission left running by a restart is ended with the reason `reboot` when the store is attached (see "What exists"). Built and tested. Intake reports the remote ones.

### 3. One start path

Built, except the call to intake and the new kinds.

- `MissionApplicationService.run_saved(mission_id, on_status, on_nav_update=None)` loads the mission, builds the queue from its `rows`, action and params, and calls `start()`. The Run button for saved missions uses it, through `MissionViewModel.run_saved`. A future scheduler and intake are meant to use it too. An inactive mission still runs, because `active` only controls scheduling.
- `resolve_steps` accepts entry node names and finds the exit through the entry's `row_id`. The integer path stays for the browser queue.
- When the executor finishes it calls `on_result`. The application service writes the result to the store, re-arms the schedule through `record_run` and, for a remote mission, makes it inactive. Telling intake is not built.
- **New:** `charge` and `park` do not go through `resolve_steps`. `run_saved` calls a separate routine for each (see section 7). Both still use the same lock, result record and `on_result`.
- **New:** the first row of a remote mission may carry a start waypoint `k`. `resolve_steps` and the executor take an optional start offset for the first step: skip the entry navigation, go to waypoint `k` of that row, enable the tool, and continue to the exit. Needs the row's ordered node list from the map. Not built.

### 4. Structured result

Built. The mission service ends every run with a `MissionResult(ok, steps_done, stage, reason)` and calls `on_result` once, then clears its running flag. `on_step_done(n)` runs as each step finishes.

- `NavigationApplicationService.navigate_and_wait_result` returns `arrived` or a reason. Failed, rejected, unavailable and busy map to `nav`. A spent budget maps to `timeout`. An abort or a cancelled goal maps to `cancel`. `navigate_and_wait` stays as a bool wrapper for the Run tab.
- `MissionApplicationService` turns `cancel` into `estop` when the soft estop, not a mission cancel, caused the stop. The mission service does the same check before each leg.
- stage is `transit` (the entry was not reached, or the mission stopped between steps) or `row` (the tool was enabled).
- `steps_done` counts steps, and a step is one entry-to-exit traverse. It is written as each step completes, so a reboot reports a true count. A row counts as done only when its last fragment finishes. Intake turns `steps_done` into whole LiteFarm rows through `groups` when it builds `A` and `E`, so the station only ever gets rows. That conversion is not built.
- An exception in the executor is reported as a failure with the reason `nav` in the stage it was in. The spec has no separate reason for it.
- The reasons the code produces are `nav`, `timeout`, `cancel` and `estop`. `reboot` is set at boot by intake, which is not built. **New:** `battery` is set by the battery stop (section 8).
- For `charge` and `park`, success is `E|mid|ok` with no steps. A failure uses stage `transit` with `steps_done` 0, and the reason from the same list.
- **New:** a `battery` stop inside a row stores `resume_wp`, the index of the last waypoint reached in that row (0 is the entry). `steps_done` counts whole rows done. Tracking the last waypoint reached needs progress from the navigation layer, which nothing provides yet (see section 5).
- A failing `on_step_done` is logged and swallowed. `on_result` retries its store writes up to three times on a SQLite `OperationalError` (for example a locked database), then reports the failure through `on_status` and the store status and raises, which the executor logs and swallows. If it still fails, the mission stays stored as running until the next restart ends it with the reason `reboot`.
- `run_saved` writes `state=running` with `started_at` once the executor has admitted the mission and before its worker thread starts, and puts the old result back if the start fails after that. A store write that fails at that point raises out of `run_saved`; intake treats that as a failed start.

### 5. Goal budget

Not built. Each goal still has a fixed 300 s budget (`NAV_TIMEOUT_SEC`, passed by `MissionApplicationService._navigate`). `navigate_and_wait_result` counts it down in 0.25 s steps after the goal is sent, excluding the wait of up to 10 s for the action server. When it runs out it cancels the goal and returns `timeout`, so an overrun is now reported as `timeout` and not `nav`.

Replace the fixed budget with a stall watchdog: fail a goal only when there has been no progress toward it for a set time. The stall time is a parameter. `NavUpdate` has no progress data, so the signal has to come from odometry or Nav2 feedback (distance remaining). Neither is wired into the mission path. At slow speeds a long row exceeds 300 s routinely, so this comes before any remote mission is useful. The same applies to a long drive to the charger from a far field.

### 6. Carrier adapters

Not built. Intake is carrier-neutral: `handle_line(sender, line)` takes a line the adapter has verified, and `send_line(line)` returns delivered or failed. Delivered is the carrier's own confirmation (HTTP `2xx`, or the MeshCore ack). Adapters do authentication and framing. Intake never sees a token or a pubkey.

**Wifi adapter (first).**
- Runs in `devkit_ui` as one HTTP endpoint taking one line. It answers `200` on receipt and calls `handle_line` on the web thread, which returns once the start succeeds or fails. The mission itself runs on the domain service's thread. The reply line may go in the response body or be sent as a separate `POST` to the station. Intake does not care which.
- Outgoing lines are `POST`ed to the station endpoint, up to three attempts, from a place that does not block the executor thread.
- The token is checked in the adapter. Whether the endpoint sits behind `web_auth` or listens on its own port is not decided.

**MeshCore adapter (later).** The question from earlier revisions applies unchanged:
- Only one process can open the serial port, and `devkit_ui` and sowbot_avoid are separate processes. Two options: avoid owns the radio and runs the adapter, calling `devkit_ui` through a small local interface (recommended, since it already owns the port and has been run in simulation); or a third small process owns it. Not decided. Check what interface `devkit_ui` can offer (ROS through `RosGateway`, or HTTP behind `web_auth`).
- meshcore_py is asyncio and the executor is threaded. Cross with `run_coroutine_threadsafe` and `call_soon_threadsafe`.
- `MeshRadio` keeps its channel subscription. The adapter subscribes to `CONTACT_MSG_RECV`.
- Provision the station as a contact on the robot's radio, and the robot on the station's. Check that avoid's `get_channel` requirement and the adapter both still work when the station contact is added or removed.
- Send with `send_msg_with_retry` (3 attempts).

### 7. Charge, park and return

Not built. Depends on hardware facts not yet known.

- **Return (autonomous):** at the end of every field mission, and after a battery stop, the robot drives to its own bay: to the in route, along it, then to the bay dock node. The station does not command it and cannot see or cancel it until the robot is in range. It counts as moving for avoid (section 9). It has no mission record. Its end report is the one the mission already stored.
- **`charge`:** from the bay, navigate to the charger dock node (by the route from the bay), dock, and confirm charging has started. Success is `E|mid|ok`. The robot stops there and does nothing more. Charging itself is not a mission.
- **`park`:** undock, then navigate to the robot's own bay pose. The `field` slot of the message carries the bay number. The robot rejects with `dock` if it is not its own bay number. Success is `E|mid|ok`.
- **Unknown, needed before `charge` or the return can be built:** how a robot docks and undocks (a Nav2 goal to a pose, a dedicated docking routine, or a driver), and how it knows charging has started (charger signal, current sense, or a battery-state topic). Without a confirmation signal `charge` can only report arrival, and the station would report a charge that never started.
- Both missions start from the bay or the charger. A field mission starts from a bay.
- Both go through the same checks as any mission for busy, estop and nav.
- A `charge` or `park` mission that fails leaves the robot where it stopped. The station holds the charger and queue and flags the operator. The robot does not retry.
- A robot that cannot reach its bay (stuck, or no dock nodes in its map) is `other` when next in range. Nothing reports it before then.

### 8. Battery return and state of charge

Not built. The robot's battery source is not identified.

- `soc` is whole percent, from whatever battery state the robot exposes. Not read: whether it is a ROS topic, a driver value or nothing yet. If it is not available, the battery return and the `soc` fields cannot be built.
- **`return_soc`** is a robot parameter, 20% at first. It is the reserve needed to reach the charger. At or below it, the robot cancels its goal at once, notes the last waypoint reached, ends the mission (stage `row`, reason `battery`, `resume_wp` set; or stage `transit` if between rows), and returns to its bay. It does not finish the row: the reserve is for getting home.
- A fixed level is wrong for a far row. The reserve should be the energy to reach the charger from the robot's position, plus a margin. Not designed. Until then `return_soc` must be set for the farthest row. The station cannot correct it from outside the field.
- **Start check:** the robot rejects a `field` mission with `battery` when `soc` is below `start_soc` (above `return_soc`). `charge` and `park` skip it. This reads the starting charge only, not the energy the mission needs. Energy per row is unmeasured (open item 4).
- **Resume:** a mission whose first row carries `@k` starts that row at waypoint `k`. The stretch between waypoint `k` and the stop point is worked twice (for sowing, sown twice). The extent is set by waypoint spacing. A straight row with no waypoints between entry and exit has only `k` = 0, so the whole row is redone. Long rows may need waypoints at regular spacing in the map import (setting not built).
- The robot reports `soc` in the accepted and progress reply and in the hello message. The station reads it only at the dock.

### 9. Dock area and routes

Not built. There is no zone and no station lock.

- The in and out routes are route nodes in the topo map (see "Dock and bay locations"). Returns, `charge` and `park` use them.
- Avoid separates robots near the dock. Every move broadcasts and listens, including the autonomous return. Today avoid runs only on a mission, so the return needs to be treated as one.
- Idle robots (in bays, on the charger) do not broadcast. Avoid cannot see them. The layout must keep the routes clear of the bays and the charger.
- Not decided: the layout. `sowbot_avoid` is simulation only.




## Open items

1. Replay protection. The mission id makes a repeat harmless while the record exists. A robot whose database was wiped would accept an old message as new, and the shared token does not stop a replay on the LAN. Not solved.
2. Parked robots are invisible to avoid, since idle robots do not broadcast. This includes robots in bays and on the charger. The autonomous return must broadcast and listen. The layout must keep the routes clear.
3. Who owns the robot's path when avoid side-steps onto another row's waypoint (mission runner or avoid driver).
4. Energy per row is unmeasured, so mission size against battery is a guess. The return reserve should come from distance to the charger, not a fixed 20%.
5. The avoid route (node ids) must be derived from the nodes along each row (entry, waypoints, exit). Not designed.
6. Station and robot clocks are not trusted. The station records its own receive time as `completed_at`, which is when the robot returned to range.
7. The battery source is not identified. Without a `soc` reading there is no battery return and no `soc` field.
8. Docking and undocking, and the signal that charging has started, are not defined (section 7).
9. Dock roles and routes. Where they live is settled by the code: the LiteFarm farm map, in the hash. Still open: how a dock is marked as the charger or a bay (a naming convention is proposed), where a robot's own bay number is set, whether a route carries a direction, and the lookup.
10. Whether the station endpoint sits behind `web_auth` or needs its own listener.
11. The 10 m wifi reach must cover the charger, the bays and the point where a returning robot first reappears. Not measured. The end report is collected only there.
12. Resume needs the last waypoint reached, which the mission runner does not track. A straight row has no waypoints between entry and exit, so a resume redoes the whole row.
13. A robot dead or stuck in the field is found only by an operator or an overdue alarm. The alarm needs an expected mission duration, which nothing provides.
14. The autonomous return has no mission record. If it fails, nothing reports it until the robot is in range, and its hello then says `other`.
15. A later MeshCore adapter: radio ownership (section 6), an airtime budget for the dock and charge polling, and the carried-over MeshCore facts are untested.
16. 
