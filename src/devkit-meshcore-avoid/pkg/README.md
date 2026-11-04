# sowbot-avoid

Robot-to-robot collision avoidance over [MeshCore](https://github.com/meshcore-dev/meshcore_py), implementing [Sowbot #202](https://github.com/Agroecology-Lab/Sowbot/issues/202).
One agent per robot, each with its own companion radio. No central controller.

**Status: simulated only, never run on hardware.** Not a safety function: onboard obstacle detection stays in charge.

## Layout

| Path | Contents |
|---|---|
| `sowbot_avoid/frame.py` | Frame codec (`docs/packet-format.md`) |
| `sowbot_avoid/graph.py` | Map, conflict test, path search |
| `sowbot_avoid/agent.py` | The protocol: notebook, claims, side-steps |
| `sowbot_avoid/radio.py` | `MeshRadio` (real) and `LoopBus` (simulated channel) |
| `sowbot_avoid/driver.py` | `Driver` interface for your motion stack |
| `sowbot_avoid/sim.py` | `simulate()`, `SimDriver`, a 3x3 test grid |
| `sowbot_avoid/topo.py` | Loads the F2C topological map (YAML) or a JSON fixture. Nodes named `dock`, `dock1`, ... become docks |
| `sweeps/maize_sweep.py` | Batch simulation over the maize map |
| `tests/` | pytest suite, plus a 9-node map fixture |

## Install and test

Python 3.10 or later. The core package has no dependencies.
Debian and Ubuntu block system-wide `pip install` (PEP 668), so use a virtual environment. If `venv` is missing, run `sudo apt install python3-venv`.

From `pkg/`:

    python3 -m venv .venv
    source .venv/bin/activate     # run this in every new shell
    pip install -e ".[test]"      # pytest and pyyaml: tests, sim, sweep
    pip install -e ".[radio]"     # only for a real radio: adds meshcore>=2.3.15
    pytest                        # about 30 s: the simulated runs use real timing

`.venv/` is gitignored. Every command below assumes the environment is active. Without activating it, call `.venv/bin/python` and `.venv/bin/pytest` instead.

## Simulate

### Maize map, 2 to 6 robots

The maize map is farmer-generated and changes, so always pass it with `--map`. Without it the script searches upwards for `maps/maize_map` and silently falls back to a 9-node test fixture if it finds none.

From `pkg/`:

    MAP=../../../maps/maize_map
    N=3
    python sweeps/maize_sweep.py --map $MAP --scenario random --robots $N --loss 0 --seeds 5

Change `--robots` to `2 3 4 5 6` for the whole range, add `--loss 0.1` for lost messages, `--jsonl out.jsonl` for per-run data.
A robot count the map cannot hold at the clearance is skipped, with a message on stderr.
A deadlocked run stops at the 600 s (unscaled) timeout, about 60 s of real time.
Run it on an idle machine, one job at a time. The default grid is 720 runs, so narrow it.

| Option | Values |
|---|---|
| `--scenario` | `follow` (same direction), `opposed` (alternating), `random`, `local` (within `--local-radius` of an anchor) |
| `--scale`, `--timeout`, `--clearance`, `--stagger` | Timer scale, unscaled timeout, clearance, random late starts |

**Docks.** Only nodes the farmer has renamed `dock`, `dock1`, `dock2`, ... (any case) count. If the map has some, each route is extended to the nearest dock no other route has taken, and spare routes end on map nodes. If it has none, no dock is created and routes end on map nodes. The map line reports the dock count.

#### Reading the output

The sweep ends with a plain summary above the detailed table. Example: 3 robots on a 53-node map, 1 seed.

    map ../../../maps/maize_map
      53 nodes, 53 with coordinates, 13 rows, 14.1 x 9.9 m, ...

    Summary
      PASS  random, 3 robots, loss 0.00, 1/1 runs finished, closest moving pair 1.20 m
      Overall: 1 PASS, 0 WARN, 0 FAIL of 1 cases.
      PASS: all finished and kept clear. WARN: also passed a finished robot too close. FAIL: a run did not finish, or moving robots came too close.
      Fewer than 5 seeds in some cases: one pass proves little. Use --seeds 5 or more.

| Verdict | Meaning |
|---|---|
| PASS | Every robot finished, and no two moving robots came closer than the clearance (1 m, less a 1 micrometre floating-point tolerance) |
| WARN | As PASS, but a robot passed a finished robot closer than the clearance. A finished robot still holds its last node, so this means it was not blocked there |
| FAIL | A run did not finish, or two moving robots came closer than the clearance |

Each line is one case: scenario, robot count and loss, across all its seeds. A case fails if any one seed fails.

- **Map line.** Check the node count and size match your farm. If not, you are on the wrong map.

The `Details` table underneath has the numbers behind each verdict:

| Column | Meaning |
|---|---|
| `done%` | Share of runs where every robot reached its end |
| `live<clr`, `min live` | Runs where two moving robots came closer than the clearance, and the closest they got, in metres |
| `parked<clr`, `min park` | The same, against a robot that had already finished |
| `secs` | Median mission time at real speed (the sim runs ten times faster) |
| `frames` | Median radio messages sent in total |
| `side` | Mean number of times a robot side-stepped to break a standoff |

Tight maps fail. On a 9-node test map, 4 or more robots deadlocked.
Runs are not deterministic: the agents use wall-clock time, so the same seed can give different results.

### What the simulator models

One collision domain where every robot hears every other. Overlapping frames are lost, plus random loss and random delivery delay.
`SimDriver` drives straight between node coordinates at 0.5 m/s, with no dynamics, tracking error or obstacles.

## Run on a robot

    python -m sowbot_avoid --serial /dev/ttyUSB0 --id 2 --map map.json --route 4,5,6,11 \
        --channel-secret-file /etc/sowbot/channel.key --driver mypkg.mymod:MyDriver

`map.json`:

    {"nodes": {"4": [0, 0], "5": [2.5, 0], "6": [5, 0], "11": [7.5, 0]},
     "edges": [[4, 5], [5, 6], [6, 11]], "park": [11]}

Edges work both ways. Every route node needs coordinates and every hop must be an edge.
`park` lists docks and is optional. When it is present a route must end at one, and side-steps never use them. A finished robot keeps its last hold in every peer's book, so ending on a route node blocks it for good; a dock stays out of the way. Without it there are no docks and a route may end on any node.

| Option | Meaning |
|---|---|
| `--id` | Robot id, 1 to 255. Node ids are 0 to 65535 |
| `--clearance` | Conflict distance in map units. Default 1.0 |
| `--channel-idx`, `--channel-name` | Radio channel slot (default 1) and name (default `sowbot`) |
| `--channel-secret-file` | File with 32 hex characters, or `-` for stdin. Omit if the radio already has the channel |
| `--flood` | Flood through repeaters. Default is zero-hop |

Options, map and route are checked before the radio opens. Needs companion firmware 1.15.0 or later.

Implement `Driver` for your motion stack:

    from sowbot_avoid import Driver

    class MyDriver(Driver):
        async def drive(self, a, b):
            ...  # command edge a -> b, return when the robot is at b

If `drive` raises, the agent stops. It does not stop the robot itself.

## How it works

Every robot has the same map and a fixed route of nodes. Each one broadcasts what it holds and what it will take next, keeps the latest frame from every peer, and drives an edge only when no one else holds it or has priority over it. Robots do not negotiate.

**Conflict.** Two node pairs conflict when the distance between their segments is below `clearance`. A node is a zero-length segment. A node without coordinates always conflicts. Without any coordinates, pairs conflict only if they share a node.

**Frames.** One kind of frame: 2 bytes (robot id, sequence) plus 2 per node, at most 3 nodes, so 8 bytes and one AES block on air. The first node is the one the robot stands on (its hold). The next two are its claims: the edges it will drive next. A robot announces on arriving at each node, and once at start-up. It sends nothing else: no boot, resend, beacon or finish messages. `docs/packet-format.md` has the layout and timing.

**Priority.** Before driving edge `e`, a robot is blocked by a peer if the peer's hold conflicts with `e`, or if one of its claims conflicts with `e` and the peer has a lower id or its frame is older than 1.5 s (it has committed). Claims that include the node we stand on are ignored. Holds always win. Otherwise the lower id wins.

**Driving an edge.** Check for blockers and wait while there are any. Drive. Keep the old node held for 2 s after arriving, then announce the new node. At start-up: random 0 to 1 s delay, announce, then wait 1.5 s for rival claims. The same 1.5 s wait follows every announcement.

**Loss.** No acknowledgements and no resends. A new sequence number on every frame makes each one differ, so it passes MeshCore's duplicate filter. A peer that missed a frame learns the robot's position from the next one, one node later. Before sending, a robot waits while it heard a frame in the last 0.1 s, backing off 0.12 to 0.48 s, for at most 2 s. A failed send is retried every 0.1 s.

**Side-step.** Only the lower id gives way: a robot blocked only by higher ids for 3 s side-steps.
It goes to the nearest row waypoint (`R<n>_W<k>`, by path length) that is clear of every hold and claim and whose row no other robot has announced. Any announced node of a row takes the whole row, so if row 1 is in use it moves up to row 2, and so on.
It never stops on an `_IN` or `_OUT` node, a headland node or a dock. It drives through them on the way.
The route becomes: the way to the stop, then the way on to the node it was heading for. At the stop it waits until that stretch, plus the next two edges, is announced clear, then resumes.
At most 10 side-steps. With no free waypoint the robot keeps waiting.

## Limits

- Not run on hardware. MeshCore timing, range and loss are assumed.
- Notebook entries never expire. A peer that dies holding an edge blocks it indefinitely.
- A finished robot never clears its last hold, so it blocks that node and everything within the clearance of it for the rest of the run. End routes on a dock, off the rows.
- Head-on meetings on tight maps can exhaust the side-step limit.
- Robots must share the same map and node id table. Unless every node has `avoid_id` set, ids come from sorted node names, so renaming or adding a node changes them. `topo.py` computes a digest of the table, but the agent does not exchange it.
- A failed radio send stops the next edge but not the current one. Lost reception is not detected.
- Robot size, speed and stopping distance are folded into one `clearance`.
