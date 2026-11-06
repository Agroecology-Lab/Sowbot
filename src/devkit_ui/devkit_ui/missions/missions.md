# Robot mission intake spec 

Status: draft. Nothing here is built. Read from Sowbot caatinga-dev at cdbb668 (PR #213 merge, 6 Nov 2026). Neither the tests nor any hardware were run. Companion: Station service spec (rev 3), which defines the messages.

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

## What exists (checked at cdbb668)

- MissionStore keeps missions in SQLite (`/workspace/maps/missions.db`), with a YAML backend for older files. A mission is a list of entry node names, one action, and `last_run_at` and `last_run_success`. The UI can save, activate and delete missions.
- Nothing runs a saved mission. `today_queue()` and `record_run()` have no callers, and the Run button runs the browser session's queue, not a saved mission.
- The store and the runner do not fit together. The store holds entry node names. `start()` wants integer row ids.
- `MissionApplicationService.start(queue, on_status, on_nav_update)` reports every failure through `on_status(ERROR, ...)` and returns normally.
- `MissionDomainService` runs one mission on a thread. Per row: go to the entry, enable the tool, go to the exit, disable the tool. It stops at the first failure. Progress is text only.
- `navigate_and_wait` gives each goal a fixed 300 s (`NAV_TIMEOUT_SEC`) and returns only true or false. `NavUpdate` carries a status and an outcome, with no measure of progress.
- Row nodes carry `row_id` and `row_role` in their metadata and nothing else. Row ids are per swath (`row_id_start + i`). The LiteFarm field and row number are lost on import, because `parse_plan` discards them.
- `farm_map_import` (PR #213) parses and hash-checks the published farm map, but nothing calls it. `robot_row_id` has no fragment term, while `FarmMap.row_ids` lists one id per fragment, so a split row gives duplicate ids. Read from source, not run.
- `TopoDoc` has a document-level `meta` dict.
- sowbot_avoid is a separate package with its own process. Its `MeshRadio` opens the serial port, requires a channel, subscribes to channel data, starts auto message fetching and sends zero-hop. `devkit_ui` does not import it. Simulation only.

## Changes from rev 2

- No separate row table and no separate `remote_missions` table. Field and row numbers go into the map, and remote missions go into the existing store.
- One robot imports the farm map each season. Its topo map is then copied to the other robots, so row identity has to travel inside that map.
- The radio owner is an open decision. Rev 2 assumed one process owned both the radio and the mission code. In the repo they are two processes.

## Design

### 1. The map holds the row identity

- The farm map is built once per season in LiteFarm and exported to one robot, which builds the topo map. On import, `parse_plan` keeps each swath's field, row and fragment numbers, and they are written to the row node metadata as `lf_field`, `lf_row` and `lf_frag`. The document meta gets `hash8`, the first 8 characters of the farm map's content hash, checked once at import with `parse_farm_map`. The finished topo map is then shared to every other robot, which never sees the farm map.
- Row ids stay per swath. Drop `robot_row_id` (field × 10000 + row). Nothing derives an id from field and row. The map is searched instead. Ids are assigned once, at import, and copied with the map, so they match on every robot.
- To resolve a (field, row), scan the node metadata and sort its fragments by row id.
- A robot's map counts as edited once a node is dropped, moved or removed. The robot then rejects remote missions with the reason `map` and reports `-` as its map hash in its hello message. This keeps "identical on every robot" true. Hook point is `topology_edit_service`, not yet read in detail.
- A new season means a new farm map, a new `hash8` and a new topo map on every robot. Until a robot has it, it rejects remote missions with the reason `map`.
- Check before relying on it: document meta is written by `TopoDoc.to_dict` and kept by its clone method, but the file loader and the way the map is copied between robots were not read. Meta and node metadata must survive save, load, copy to another robot and node-drop edits.

### 2. One mission store

Extend the `missions` table. `_ensure_schema` only does `CREATE TABLE IF NOT EXISTS`, so adding columns needs a migration step. New columns:

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

- Remote missions store `rows` as entry node names with every fragment expanded, so the record matches what ran even if the map changes later.
- `today_queue()` skips remote missions. Without this, a failed remote mission would count as due for retry, which the current rule does for local ones.
- A remote mission is inactive once it ends, whatever the result.
- A small table `intake_epoch(epoch, max_counter)` holds the highest counter per epoch, so pruning old records cannot reopen replay. Keep the last 20 remote missions.
- The YAML backend either gets the same fields or is retired for missions. Decide before migrating.
- The SQLite `missions` property queries on every read, on one connection shared by several threads. The store lock covers writes only. A radio thread adds a writer, so reads should take the lock too.

### 3. One start path

- Add `MissionApplicationService.run_saved(mid, on_status)`. It loads the mission, resolves its entry names to steps and calls `start()`. The Run button for saved missions, a future scheduler and intake all use it.
- `resolve_steps` also accepts entry node names, and finds the exit through the entry's `row_id`. The integer path stays for the browser queue.
- When the executor finishes it calls `on_result`. The application service writes the result to the store, then tells intake.

### 4. Structured result

- `navigate` returns a reason instead of a bool. From `NavUpdate.outcome`: failed, rejected and unavailable map to `nav`. Cancelled maps to `cancel` or `estop`, depending on which flag was set. A spent budget maps to `timeout`.
- stage is `transit` (the entry was not reached) or `row` (the tool was enabled).
- A row counts as done only when its last fragment finishes. `groups` makes that countable.
- `steps_done` is written as each whole row completes, not only at the end, so a reboot reports a true count.

### 5. Goal budget

Replace the fixed 300 s with a stall watchdog: fail a goal only when there has been no progress toward it for a set time. The stall time is a parameter. `NavUpdate` has no progress data, so the signal has to come from odometry or Nav2 feedback (distance remaining). Neither is wired into the mission path yet. An overrun is reported as `timeout`, not `nav`. At slow speeds a long row exceeds 300 s routinely, so this comes before any remote mission is useful.

### 6. Radio ownership

Only one process can open the serial port. `devkit_ui` and sowbot_avoid are separate processes. Two options:

- The avoid process owns the radio and runs intake. It calls `devkit_ui` through a small local interface (start, cancel, status). Recommended, because it is the process that already owns the port and has been run in simulation.
- A third small process owns the radio, and both use it.

Not decided. Check what interface `devkit_ui` can offer (ROS through `RosGateway`, or HTTP behind `web_auth`).

Whichever is chosen:

- meshcore_py is asyncio and the executor is threaded. Cross with `run_coroutine_threadsafe` and `call_soon_threadsafe`.
- `MeshRadio` keeps its channel subscription. Intake subscribes to `CONTACT_MSG_RECV`.
- Provision the station as a contact on the robot's radio, and the robot on the station's.
- Check that avoid's `get_channel` requirement and intake both still work when the station contact is added or removed.

## Intake behaviour

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

Then write the mission to the store (source remote, state running) and call `run_saved`. If `start()` delivers an ERROR on the calling thread, delete the record and reject the mission with the reason `nav` (or `busy` for "mission already running"). Otherwise accept it, reporting no rows done. The highest counter moves only for accepted missions.

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

A mission stored as running is marked ended with the reason `reboot` and its stored `steps_done`. Send a hello message (map hash, state idle), then the end report for that mission. The robot does not resume after a restart. A status query for mission 0 gets a hello reply.

## Limits

- Messages are at most 158 bytes. `rows` holds integers and ascending ranges only, at most 200 rows per mission (tune after testing).
- One mission at a time, as the domain service enforces.
- A mission carries one action and its default params. Per-row actions are not supported.

## Test plan

Extend the existing tests in `src/devkit_ui/test` (`test_mission_store`, `test_mission_domain_service`, `test_mission_application_service`, `test_farm_map_import`).

- Unit: message parser and bad input, check ordering, mission id replay and idempotency, result mapping (ok, fail/row, fail/transit, cancel, reboot), whole-row counting across fragments.
- Store: schema migration on an existing database, remote missions excluded from `today_queue`, `run_saved` for a local mission, epoch counter survives pruning.
- Import: a farm map with a split row gives distinct row ids and keeps field, row and fragment in node metadata.
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
