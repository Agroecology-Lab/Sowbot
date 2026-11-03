# Station service spec

Status: draft.
Sources read: Sowbot `caatinga-dev` 1a09baa (2026-11-03), samuk/LiteFarm 78bb847 (2026-11-02), MeshCore 81dbae5 (2026-10-15). `meshcore_py` not read.

## Scope

Python service on a farm SBC. Polls LiteFarm for robot tasks, sends each to one robot over a MeshCore direct message, relays per-row results back to LiteFarm.

Out of scope: robot-to-robot avoidance (`sowbot_avoid`), task creation, row plan creation (robot exports GeoJSON, a user imports it), safety functions. E-stop stays on the robot.

## Verified interfaces

### LiteFarm API

Auth: `POST /login` returns `id_token`, `user_id`. Token expires. Headers on later calls: `Authorization: Bearer`, `user_id`, `farm_id`. Reuse `devkit_ui/litefarm_client.py` (stdlib only; its docstring names "the robot station").

| Call | Use |
|---|---|
| `GET /task/:farm_id` | All farm tasks. Fields used: `task_id`, `assignee_user_id`, `due_date`, `complete_date`, `abandon_date`, `locations` |
| `GET /row_plan/task/:task_id/rows` | `[{row_plan_id, map_id, row_id, row_number, frag, status, completed_at, note}]` in mission order. `status`: `pending`, `done`, `aborted` |
| `PATCH /row_plan/task/:task_id/rows` | Body `{rows:[{row_number, frag?, status: done\|aborted, completed_at?, note?}]}`. 204 on success. Same result repeated: accepted. Different result: 409. All rows applied or none. Allowed on a complete task |
| `GET /row_plan/:row_plan_id/geojson` | Plan export |

- PATCH caller must be role 1, 2 or 5, or the task assignee. Abandoned task: 409 (`getWritableTask`).
- `row_number` is the id missions use (migration comment). Without `frag`, a result applies to every fragment of the row.
- Row selection grammar in LiteFarm: `"12-18,21"` (`parseRowSpec`).
- Plan `map_id` default: `<map name>-<first 12 hex of map_hash>` (`docs/row-plan-geojson.md`).

### Robot (devkit_ui)

- Mission = rows, `action` (`drive` or `weed`), `action_params`, `repeat_every_hours` (`missions/store.py`).
- Rows resolve to entry/exit nodes by `row_id` in the loaded map. A row without entry or exit raises `MissionError`.
- One mission at a time (`mission already running`).
- No remote mission intake found. `ui_node.py` has one page route (`/`). Optional shared-password Basic auth on port 80 (`web_auth.py`).

### MeshCore

- Channel data (`CMD_SEND_CHANNEL_DATA` 0x3E, `GRP_DATA`): no ack. Used by `sowbot_avoid`: channel idx 1 `sowbot`, `data_type` 0xFFFF, zero-hop. Max 165 B.
- Direct text (`CMD_SEND_TXT_MSG` = 2):
  - Recipient must be a contact (6-byte pubkey prefix). Unknown: `ERR_CODE_NOT_FOUND`.
  - Reply `RESP_CODE_SENT` (6): flood flag, `expected_ack` u32, `est_timeout` u32.
  - Delivery ack arrives as `PUSH_CODE_SEND_CONFIRMED` (0x82): ack u32, trip time u32.
  - `MAX_TEXT_LEN` = 160 B (`BaseChatMesh.h`). `attempt` byte in the command; `attempt > 3` limits text to 158 B.
- The ack is sent by firmware in `BaseChatMesh::onPeerDataRecv`, on receipt. It confirms radio delivery only, not that the robot accepted the mission.

### sowbot_avoid

- Simulated only, never run on hardware (README). Not a safety function.
- `MeshRadio` opens the companion radio serial port and subscribes to `CHANNEL_DATA_RECV` only.
- Robot id 1 to 255. All robots must share one map and node id table. The table digest is computed but not exchanged.

## Station design (proposal)

### Config

- LiteFarm URL, farm id, station user (password from env).
- Poll interval.
- Station radio serial port.
- Robots: `robot_id` (same as avoid id), pubkey prefix (6 B hex), `map_id`, optional `assignee_user_id`.

### Loop

1. Poll `GET /task/:farm_id`. Candidates: not deleted, no `complete_date`, no `abandon_date`, not already dispatched.
2. `GET .../rows`. Keep `pending` rows. Skip the task if none.
3. Compare the rows' `map_id` with the target robot's `map_id`. Mismatch: log, hold the task, do not send.
4. Choose a robot (see open items).
5. Send `M`. Wait for the radio ack, then for `A` or `R` from the robot.
6. On each `P`, `PATCH` the row result. On `F`, mark the task finished in station state.

The station does not set `complete_date` on the task.

### Messages

Direct text, ASCII, `|` separated, 160 B max.

| Dir | Message | Meaning |
|---|---|---|
| S→R | `M\|mid\|task_id\|hash8\|action\|rows` | Mission. `hash8` = first 8 hex of `map_hash`. `rows` uses the `12-18,21` grammar |
| S→R | `C\|mid` | Cancel |
| S→R | `Q\|mid` | Status query |
| R→S | `A\|mid` | Accepted |
| R→S | `R\|mid\|reason` | Rejected: `busy`, `estop`, `map`, `row` |
| R→S | `P\|mid\|row\|d` or `a` | Row done or aborted |
| R→S | `F\|mid` | Mission finished |
| R→S | `H\|hash8\|busy` | Hello on boot |

`mid` is a station-assigned counter. The robot accepts `M` and `C` only from the configured station pubkey.

### State

SQLite on the SBC: `mid`, `task_id`, `robot_id`, state, last acked message, unsent row results.

- Restart: reload state, send `Q` for open missions, resume.
- LiteFarm unreachable: queue row results, `PATCH` on reconnect. Safe to repeat (same result accepted).
- No radio ack after 3 attempts: robot unreachable, task stays undispatched, retry next cycle.
- `PATCH` returns 409 (abandoned task or conflicting result): send `C` for an abandoned task, log a conflict, do not retry.

## Gaps to close

1. Robot has no remote mission intake. Needs a receiver that maps `M` to `MissionApplicationService.start` with `(row, action, params)`.
2. One serial port per process. The receiver must share the radio session with the avoid agent, or avoid must also subscribe to contact messages.
3. Mission traffic shares the airtime that avoid assumes (it backs off when it hears a frame in the last 0.1 s).
4. LiteFarm has no robot entity. Robot choice rule undecided: assignee-to-robot map, or any idle robot on the field.
5. No LiteFarm task type for robot work found in the source read. Station selection by assignee only.
6. Avoid route (node ids) and mission rows (row ids) are separate. The receiver must derive the avoid route from the rows' entry/exit nodes.
7. `meshcore_py` API for direct send, `expected_ack` and contact management not read.
