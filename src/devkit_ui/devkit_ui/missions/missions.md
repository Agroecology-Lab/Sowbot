# Robot mission intake spec 

Status: draft. Built so far: the SQLite schema and migration, including the remote-mission columns; `MissionStore.add_remote` and the epoch, lookup and pruning methods on `MissionStore`; ending any mission left running by a restart; `run_saved` with outcome recording; name-based `resolve_steps`; locked store reads; the structured mission result; ids that are never reused; `today_queue()` skipping remote missions; and the Run button and a result line for saved missions. Not built: the stall watchdog, the farm map import into the topo map (field, row and fragment metadata, `hash8`, the edit hook), the intake, the radio link and the station. Nothing calls `add_remote` yet.

Originally read from Sowbot caatinga-dev at cdbb668 (PR #213 merge, 6 Nov 2026). Updated on 6 Nov 2026 against mission-refactor at 980b2fb by reading the code, then with the store changes made after that commit. The mission, store, farm map import and plan import tests pass (211 tests) with `std_msgs` stubbed, because ROS is not installed in that environment. Nothing ran under ROS or on hardware. Companion: Station service spec (rev 3), which defines the messages.

Messages are named in plain words here: mission, cancel, status query, accepted, rejected, end report and hello. The [station spec gives the exact formats.](https://github.com/Agroecology-Lab/Sowbot_Station/blob/main/README.md)

## Purpose

Let one station send a mission to a robot over MeshCore and get the outcome back. Missions made on the robot and missions sent from the station share one store, one start path and one result record.

The topological map stays the source of truth. Every robot holds the same map, and nothing about rows is kept outside it.

This is not a safety function. E-stop and the independent safety subsystem are unchanged. Remote cancel is best effort.

## Assumptions

- One MeshCore radio per robot, shared by avoid (channel, no acks) and mission messages (direct, with ack).
- A robot is stopped when it receives a mission. It moves and sends avoid frames only while on a mission.
- Up to 10 robots over about 1 mile square. Slow robots, long rows, missions lasting hours.
- The robot sends nothing unprompted except a reply to a mission message (accepted or rejected), an end report when a mission ends, and a hello message on boot.

## Map generation

In the diagram, `[new]` marks what is not built. `built:` marks what exists on mission-refactor.

```

LiteFarm: published farm map (GeoJSON, content_hash)
   |
   | export file, once per season
   v
+==================== ROBOT 1 (imports map) ===========================+
|                                                                      |
|  parse_farm_map()            exists, nothing calls it                |
|    checks: published, F<f>R<r> names, content_hash recomputed        |
|    -> FarmMap {fields, rows+frag, content_hash}                      |
|                                                                      |
|  import path                                                         |
|    parse_plan -> topo_row_builder                                    |
|    [new] keep field/row/frag per swath (today: dropped)             |
|    [new] drop robot_row_id (field*10000+row)                         |
|                                                                      |
|  WRITES: topo map (YAML)                                             |
|  +----------------------------------------------------------------+  |
|  | doc.meta                                                       |  |
|  |   hash8: c50f60d8        [new] first 8 of content_hash         |  |
|  | nodes (entry / exit / waypoints per swath)                     |  |
|  |   meta.row_id    = row_id_start + i   (per swath, set once)    |  |
|  |   meta.row_role  = entry | exit                                |  |
|  |   meta.lf_field  [new]                                         |  |
|  |   meta.lf_row    [new]                                         |  |
|  |   meta.lf_frag   [new]                                         |  |
|  |   edges: in-row + headland (no headland across field/frag)    |  |
|  +----------------------------------------------------------------+  |
|  edit service: add/delete node/row, f2c save, repair                 |
|     -> [new] any edit sets hash8 to "-"  (robot then rejects "map") |
|                                                                      |
|  WRITES: missions.db (SQLite, /workspace/maps/missions.db)           |
|  +----------------------------------------------------------------+  |
|  | original: id name rows(entry node names) action action_params  |  |
|  |           repeat_every_hours active created_at                 |  |
|  |           last_run_at last_run_success                         |  |
|  | built:    source(local|remote) external_id task_id map_hash    |  |
|  |           groups(lf row per entry) state(idle|running|ended)   |  |
|  |           steps_done stage reason started_at ended_at          |  |
|  |           result_sent                                          |  |
|  | built: table intake_epoch(epoch, max_counter)                  |  |
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
   |  never sees farm   |     |  never sees farm   |
   |  map               |     |  map               |
   +--------------------+     +--------------------+
   ```

Each robot resolves (field,row) by scanning node meta (lf_field, lf_row,
lf_frag sorted by row_id), so the station never needs robot row ids.

This resolution is not built. Today `resolve_steps` resolves integer row ids and entry node names only. The copy to the other robots is not built either. The sim page has "Save copy as" and archive for the local map, and the system page exports a LiteFarm row plan. No robot-to-robot copy exists in the UI.


## What exists (checked at 980b2fb)

- MissionStore keeps missions in SQLite (`/workspace/maps/missions.db`). A `.yaml` path selects the YAML backend, which has none of the new fields. A mission is a list of entry node names, one action, `last_run_at` and `last_run_success`. On SQLite it also has the remote-mission columns and the run result (`state`, `steps_done`, `stage`, `reason`, `started_at`, `ended_at`, `result_sent`). A database from before these columns gains them when opened, with `source` local and `state` idle. The UI can save, activate, delete and run saved missions. It shows the last-run tick or cross and a result line (`result_text`): running with steps so far, done, or why it stopped, the stage and the steps finished.
- `MissionStore.add()` takes the local fields only. `MissionStore.add_remote()` stores a station mission idle and active, and refuses a YAML store, an empty id or map hash, rows and groups of different lengths, a validation error and a repeated `external_id`. `find_by_external_id`, `max_counter`, `record_counter` and `prune_remote` are on `MissionStore` too, and raise `RuntimeError` for YAML, which cannot keep replay protection. Nothing outside the tests calls any of them. `MissionStore.record_result()` writes the run result and does nothing for YAML.
- Mission ids are allocated by SQLite (the column is `AUTOINCREMENT`), so a deleted mission's id is not reused. `MissionStore.delete()` refuses a mission that is running, because its run still has to write a result.
- `MissionStore.attach()` ends every mission stored as running, with the reason `reboot`, because a restart does not resume a run. `steps_done` and `stage` keep their last values. A remote mission also becomes inactive and its end report is marked unsent. `last_run_at` and `last_run_success` are left alone, because the run did not finish.
- A saved local mission runs from its play button through `run_saved`, which records state, steps done, stage and reason and calls `record_run()`. Remote missions get no play button and are labelled "station-controlled". `today_queue()` skips remote missions and still has no caller, so nothing schedules a mission. The main Run Mission button runs the browser session's queue through `start()` with no result callbacks and records nothing. That is deliberate: a queue run is not a saved mission, and a record per run would fill the store.
- The store holds entry node names and `start()` wants integer row ids. `resolve_steps` accepts integer ids, entry node names, bare id strings and the saved `ROW_<id>_IN` fallback. It rejects an unknown name and two entry nodes for one row.
- `MissionApplicationService.start(queue, on_status, on_nav_update, on_result, on_step_done)` reports every failure through `on_status(ERROR, ...)` and returns False. It returns True once the worker has started.
- `MissionDomainService` runs one mission on a thread. Per row: go to the entry, enable the tool, go to the exit, disable the tool. It checks cancel and the soft e-stop before each leg and stops at the first failure. It reports text progress through `on_status`, the count of finished steps through `on_step_done` and one `MissionResult` through `on_result`.
- `navigate_and_wait_result` returns `arrived` or a reason. `MissionApplicationService._navigate` gives each goal a fixed 300 s (`NAV_TIMEOUT_SEC`). `navigate_and_wait` is a bool wrapper used by the Run tab. `NavUpdate` carries a status, a `navigating` flag and an outcome, with no measure of progress.
- Row nodes carry `row_id` and `row_role` in their metadata and nothing else. Row ids are per swath (`row_id_start + i`). The LiteFarm field and row number are lost on import, because `parse_plan` returns only the swaths, the origin, `break_after` and the plan parameters. For a multi-field plan `_merge_fields` offsets the row numbers so that each field continues after the largest number so far.
- `farm_map_import` (PR #213) parses and hash-checks the published farm map, but nothing outside its tests calls `parse_farm_map`. `FarmMap.hash8` exists, and nothing writes it to the map. `robot_row_id` has no fragment term, while `FarmMap.row_ids` lists one id per fragment, so a split row gives duplicate ids. Read from source, not run.
- `TopoDoc` has a document-level `meta` dict. The loader (`parse._topo_from_dict`) reads it and `to_dict` writes it. `clone_empty` and `renamed` copy it. Node metadata is read and written the same way.
- sowbot_avoid (`src/devkit-meshcore-avoid/pkg/sowbot_avoid`) is a separate package with its own process. Its `MeshRadio` opens the serial port and requires the configured channel (it raises if the radio has none). It subscribes to channel data, starts auto message fetching and sends zero-hop by default (`--flood` allows repeaters). `LoopBus` and `LoopRadio` are an in-process fake radio. `devkit_ui` does not import it, and no code subscribes to contact messages. Simulation only.

## Changes from rev 2

- No separate row table and no separate `remote_missions` table. Field and row numbers go into the map, and remote missions go into the existing store.
- One robot imports the farm map each season. Its topo map is then copied to the other robots, so row identity has to travel inside that map.
- The radio owner is an open decision. Rev 2 assumed one process owned both the radio and the mission code. In the repo they are two processes.

## Design

### 1. The map holds the row identity

Not built. Nothing in this section is in the code yet.

- The farm map is built once per season in LiteFarm and exported to one robot, which builds the topo map. On import, `parse_plan` keeps each swath's field, row and fragment numbers, and they are written to the row node metadata as `lf_field`, `lf_row` and `lf_frag`. The document meta gets `hash8`, the first 8 characters of the farm map's content hash, checked once at import with `parse_farm_map`. The finished topo map is then shared to every other robot, which never sees the farm map.
- Row ids stay per swath. Drop `robot_row_id` (field × 10000 + row). Nothing derives an id from field and row. The map is searched instead. Ids are assigned once, at import, and copied with the map, so they match on every robot.
- To resolve a (field, row), scan the node metadata and sort its fragments by row id.
- A robot's map counts as edited once a node is dropped, moved or removed. The robot then rejects remote missions with the reason `map` and reports `-` as its map hash in its hello message. This keeps "identical on every robot" true. The edits are `stage_drop`, `stage_f2c_save`, `stage_repair`, `stage_delete_node` and `stage_delete_row` in `TopologyEditService`, and none sets `hash8`. They are written by `PlanApplicationService._start_write` and `MapAuthoringViewModel._persist_deletion`. The hook belongs where a staged edit is written. That path was not read in detail.
- A new season means a new farm map, a new `hash8` and a new topo map on every robot. Until a robot has it, it rejects remote missions with the reason `map`.
- Checked in source: document meta and node metadata survive load, `to_dict`, `clone_empty` and `renamed`. Not checked: the external map manager's copy of the map (the live `/topological_map_2` republish goes through the same loader), whether node-drop and f2c-save edits keep document meta, and how the map reaches another robot, since no copy mechanism exists. Meta and node metadata must survive save, load, copy to another robot and node edits.

### Robot comms

Not built. No code reads or sends any of these messages.

```
+==================== FARMHOUSE SBC =====================================+
|                                                                        |
|  +---------------------+   HTTP    +--------------------------------+  |
|  | docker: LiteFarm    |<--------->| docker: Station service        |  |
|  |                     |           |                                |  |
|  |  API                |  POST     |  poll loop (tasks)             |  |
|  |  DB                 |  /login   |  dispatch + liveness (20 min)  |  |
|  |                     |           |  result mapping -> PATCH       |  |
|  |  tasks              |  GET      |                                |  |
|  |  row plans + rows   |  /task/:farm_id                            |  |
|  |  published farm map |  /row_plan/task/:id/rows                   |  |
|  |  (content_hash)     |  /row_plan/:id                             |  |
|  |                     |  /farm_map/farm/:id/published/geojson      |  |
|  |                     |<----------+ PATCH /row_plan/task/:id/rows  |  |
|  |                     |           |   done | aborted               |  |
|  +---------------------+           |                                |  |
|                                    |  SQLite (volume)               |  |
|                                    |   missions, held tasks,        |  |
|                                    |   unsent PATCHes, per-robot    |  |
|                                    |   hash8 + last contact         |  |
|                                    |  config: robot_id, pubkey      |  |
|                                    |   prefix, assignee_user_id     |  |
|                                    +---------------+----------------+  |
|                                                    | serial            |
|                                                    | /dev/tty* passed  |
|                                                    | into container    |
|                                    +---------------v----------------+  |
|                                    | MeshCore companion radio       |  |
|                                    | (1 radio, 1 port)              |  |
|                                    +---------------+----------------+  |
+====================================================|===================+
                                                     |
                      MeshCore direct text, <=158 B, acked
                      (companions do not repeat; unreachable is normal)
                                                     |
        M|mid|task|hash8|action|field|rows   ------> |
        Q|mid   (hello if mid=0)             ------> |
        C|mid                                ------> |
        <------ A|mid|n    R|mid|why    E|mid|...    H|hash8|state
                                                     |
              +--------------------+-----------------+----+
              v                    v                      v
        +-----------+        +-----------+          +-----------+
        | ROBOT 1   |        | ROBOT 2   |   ...    | ROBOT 10  |
        | radio     |        | radio     |          | radio     |
        | intake +  |        | intake +  |          | intake +  |
        | missions  |        | missions  |          | missions  |
        +-----+-----+        +-----+-----+          +-----+-----+
              ^                    ^                      ^
              +--------------------+----------------------+
                 avoid frames: channel, zero-hop, no acks
                 (robot to robot, same radio, only while on a mission)
```





### 2. One mission store

Built in `MissionSqliteStore`. `_ensure_schema` adds each column of `_ADDED_COLUMNS` that the `missions` table lacks, so an older database keeps its rows. `external_id` has a unique index that ignores nulls. New columns:

| Column | Meaning |
| --- | --- |
| `source` | `local` or `remote` |
| `external_id` | the mission id, unique, remote only |
| `task_id` | the LiteFarm task, remote only |
| `map_hash` | hash8 the mission was made against |
| `groups` | LiteFarm row number for each entry in `rows`, null for local, so whole rows can be counted |
| `state` | `idle`, `running` or `ended` |
| `steps_done`, `stage`, `reason` | result of the last run |
| `started_at`, `ended_at` | UTC |
| `result_sent` | remote only, whether the station has acknowledged the end report |

Rules:

- Remote missions store `rows` as entry node names with every fragment expanded, so the record matches what ran even if the map changes later. `add_remote` stores the rows it is given, so intake does the expansion. Nothing calls it yet.
- `today_queue()` skips remote missions. Without this, a failed remote mission would count as due for retry, which the current rule does for local ones. Built and tested.
- A remote mission is inactive once it ends, whatever the result. Built in `run_saved`.
- `intake_epoch(epoch, max_counter)` holds the highest counter per epoch, so pruning old records cannot reopen replay. `record_counter` raises it only when the new counter is higher, and returns whether it did. The check and the write are one statement, so two callers cannot both succeed with the same counter. `prune_remote(keep=20)` deletes all but the newest 20 remote missions, except a running one, and leaves local missions and `intake_epoch` alone. Built and tested.
- The YAML backend gets none of the fields, and `record_result` does nothing for it. Remote missions cannot work on YAML. Still to decide: retire YAML for missions, or give it the fields. The default path is the `.db` file.
- The SQLite store holds one reentrant lock around every method, reads included, because its connection is shared by several threads. `update()` rejects an unknown `state`, as `add()` does. Built and tested.
- A mission left running by a restart is ended with the reason `reboot` when the store is attached (see "What exists"). Intake reports the remote ones.

### 3. One start path

Built, except the call to intake.

- `MissionApplicationService.run_saved(mission_id, on_status, on_nav_update=None)` loads the mission, builds the queue from its `rows`, action and params, and calls `start()`. The Run button for saved missions uses it, through `MissionViewModel.run_saved`. A future scheduler and intake are meant to use it too. An inactive mission still runs, because `active` only controls scheduling.
- `resolve_steps` accepts entry node names and finds the exit through the entry's `row_id`. The integer path stays for the browser queue.
- When the executor finishes it calls `on_result`. The application service writes the result to the store, re-arms the schedule through `record_run` and, for a remote mission, makes it inactive. Telling intake is not built.

### 4. Structured result

Built. The mission service ends every run with a `MissionResult(ok, steps_done, stage, reason)` and calls `on_result` once, then clears its running flag. `on_step_done(n)` runs as each step finishes.

- `NavigationApplicationService.navigate_and_wait_result` returns `arrived` or a reason. Failed, rejected, unavailable and busy map to `nav`. A spent budget maps to `timeout`. An abort or a cancelled goal maps to `cancel`. `navigate_and_wait` stays as a bool wrapper for the Run tab.
- `MissionApplicationService` turns `cancel` into `estop` when the soft estop, not a mission cancel, caused the stop. The mission service does the same check before each leg.
- stage is `transit` (the entry was not reached, or the mission stopped between steps) or `row` (the tool was enabled).
- `steps_done` counts steps, and a step is one entry-to-exit traverse. It is written as each step completes, so a reboot reports a true count. A row counts as done only when its last fragment finishes, so the station turns steps into LiteFarm rows through `groups`. That conversion is not built.
- An exception in the executor is reported as a failure with the reason `nav` in the stage it was in. The spec has no separate reason for it.
- The reasons the code produces are `nav`, `timeout`, `cancel` and `estop`. `reboot` is set at boot by intake, which is not built.
- A failing `on_result` or `on_step_done` is logged and swallowed, so a store error cannot leave a mission marked running.
- `run_saved` writes `state=running` with `started_at` before starting, and puts the old result back if the start is refused. The YAML backend records no result fields.

### 5. Goal budget

Not built. Each goal still has a fixed 300 s budget (`NAV_TIMEOUT_SEC`, passed by `MissionApplicationService._navigate`). `navigate_and_wait_result` counts it down in 0.25 s steps after the goal is sent, excluding the wait of up to 10 s for the action server. When it runs out it cancels the goal and returns `timeout`, so an overrun is now reported as `timeout` and not `nav`.

Replace the fixed budget with a stall watchdog: fail a goal only when there has been no progress toward it for a set time. The stall time is a parameter. `NavUpdate` has no progress data, so the signal has to come from odometry or Nav2 feedback (distance remaining). Neither is wired into the mission path. At slow speeds a long row exceeds 300 s routinely, so this comes before any remote mission is useful.

### 6. Radio ownership

Not decided and not built. Only one process can open the serial port. `devkit_ui` and sowbot_avoid are separate processes. Two options:

- The avoid process owns the radio and runs intake. It calls `devkit_ui` through a small local interface (start, cancel, status). Recommended, because it is the process that already owns the port and has been run in simulation.
- A third small process owns the radio, and both use it.

Not decided. Check what interface `devkit_ui` can offer (ROS through `RosGateway`, or HTTP behind `web_auth`).

Whichever is chosen:

- meshcore_py is asyncio and the executor is threaded. Cross with `run_coroutine_threadsafe` and `call_soon_threadsafe`.
- `MeshRadio` keeps its channel subscription. Intake subscribes to `CONTACT_MSG_RECV`.
- Provision the station as a contact on the robot's radio, and the robot on the station's.
- Check that avoid's `get_channel` requirement and intake both still work when the station contact is added or removed.

## Intake behaviour

Not built. This section is the design.

### Receiving

1. Accept only messages from the configured station prefix. Drop anything else with no reply.
2. Parse strictly. If the mission id can be read but another field is bad, reject the message with the reason `fmt`. If nothing can be read, drop it.
3. A mission id has the form `<epoch>.<counter>`. Reject a counter at or below the highest accepted for that epoch. A repeated mission message with a known id returns the stored state (accepted, or ended), never a second run.

### Accepting a mission

Checks run in this order, each with its own rejection reason:

1. busy: a mission is running or starting.
2. estop: the confirmed soft e-stop is active.
3. map: the map is edited, or hash8 differs from the map's hash8.
4. action: not in `ACTIONS`.
5. row: a (field, row) is missing from the map, is duplicated, or lacks an entry or exit node. Run the same resolution as `resolve_steps` here.
6. nav: navigation is unavailable.

Then write the mission with `add_remote` (stored idle) and call `run_saved`, which marks it running. If `start()` delivers an ERROR on the calling thread, delete the record and reject the mission with the reason `nav` (or `busy` for "mission already running"). The record is idle again by then, so `delete` allows it. Otherwise accept it, reporting no rows done, and call `record_counter`. The highest counter moves only for accepted missions. A repeated mission id is found with `find_by_external_id` before any of this.

The domain service takes its own lock when starting, so a simultaneous start from the web UI loses cleanly and shows up as that ERROR.

### While running

- A status query for the mission returns a progress reply: the mission is accepted and running, with the number of whole rows done. This is the only progress the robot reports, and only when asked.
- A cancel message for the running mission calls `cancel()`. The outcome comes back in the end report with the reason `cancel`. Cancelling an ended mission re-sends its end report. Cancelling an unknown mission is rejected with the reason `unknown`.
- Nothing else is sent. No timers, no progress broadcasts.

### Ending

The result is already in the store when the executor finishes. Send the end report with `send_msg_with_retry` (3 attempts) on the radio loop, without blocking the executor thread. If no ack arrives, `result_sent` stays false and the end report is re-sent at the station's next status query or hello exchange. There is no resend timer. The station's regular status query recovers a lost end report.

- If all rows are done, send a successful end report.
- Otherwise send a failed end report giving the number of whole rows done, the stage and the reason.

### Boot

The store already ended any mission stored as running, with the reason `reboot` and its stored `steps_done`, when it was attached. Built. Intake then sends a hello message (map hash, state idle), then an end report for each remote mission whose `result_sent` is false. The robot does not resume after a restart. A status query for mission 0 gets a hello reply.

## Limits

None of these is enforced yet, because there is no intake.

- Messages are at most 158 bytes. `rows` holds integers and ascending ranges only, at most 200 rows per mission (tune after testing).
- One mission at a time, as the domain service enforces.
- A mission carries one action and its default params. Per-row actions are not supported.

## Test plan

The tests are in `src/devkit_ui/test`: `test_mission_store`, `test_mission_sqlite_store`, `test_mission_yaml_store`, `test_mission_domain_service`, `test_mission_application_service`, `test_mission_boundaries`, `test_mission_view_model`, `test_farm_map_import` and `test_plan_import`.

Written and passing (211 tests on 6 Nov 2026, with `std_msgs` stubbed, not run under colcon):

- Store: `add_remote` and its refusals, YAML refusing remote missions, ids not reused after a delete, no delete of a running mission, `attach` ending a mission left running, pruning sparing a running mission, `record_counter` reporting whether it raised the counter, `result_text`, schema migration of an existing database, remote missions excluded from `today_queue` even after a failed run, the epoch counter only rising, pruning keeping the newest remote missions, all local missions and the counter, concurrent reads and writes, and result storage (nothing written for YAML).
- Run path: `run_saved` marks the mission running, stores the result and re-arms the schedule, makes a remote mission inactive, and keeps the previous result when the start is refused. `cancel` against `estop` is reported correctly, as is a failing result callback and an executor exception.
- Import: `parse_farm_map` and its hash check, including `hash8`.

Not written yet:

- Unit: message parser and bad input, check ordering, mission id replay and idempotency, whole-row counting across fragments.
- Import: a farm map with a split row gives distinct row ids and keeps field, row and fragment in node metadata. This needs the import path to be built first.
- Simulation: sowbot_avoid's `LoopBus` and `LoopRadio` as a fake radio for intake, then the Gazebo multi-row mission, including a failed transit and a cancel.
- On the real session before relying on it: intake and avoid on one meshcore_py connection, contact provisioning, `CONTACT_MSG_RECV` delivery with auto fetching on.
- No claim is made about behaviour on a radio until it has run on one.

## Open items

1. Replay protection beyond the mission id is not checked in MeshCore firmware.
2. Parked robots may be invisible to avoid, since idle robots do not broadcast. Depends on whether idle robots listen, and on zero-hop range.
3. Who owns the robot's path when avoid side-steps onto another row's waypoint (mission runner or avoid driver).
4. The avoid route (node ids) must be derived from the nodes along each row (entry, waypoints, exit). Not designed.
5. Station and robot clocks are not trusted. The station records its own receive time as `completed_at`.
6. Radio ownership (section 6).
7. YAML backend for missions: retire it or give it the fields (section 2). Remote missions cannot use it.
