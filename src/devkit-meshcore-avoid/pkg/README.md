# sowbot-avoid

Robot-to-robot collision avoidance over [MeshCore](https://github.com/meshcore-dev/meshcore_py), implementing the legacy scheme from [Sowbot #202](https://github.com/Agroecology-Lab/Sowbot/issues/202).
Each robot runs one agent on its SBC, talking to one MeshCore companion radio.
There is no central controller.

**Status: simulated only. Never run on hardware.**
Not a safety function. Onboard obstacle detection stays in charge.

## Contents

| Path | Contents |
|---|---|
| `sowbot_avoid/frame.py` | Frame codec. No dependencies. Format in `docs/packet-format.md` |
| `sowbot_avoid/graph.py` | Map, conflict test, shortest-hop path search |
| `sowbot_avoid/agent.py` | The protocol: notebook, hold/claim checks, resends, beacons, deadlock retreats |
| `sowbot_avoid/radio.py` | `MeshRadio` (real, via `meshcore_py`) and `LoopBus` (simulated shared channel) |
| `sowbot_avoid/driver.py` | `Driver` interface. Implement `drive(a, b)` for your motion stack |
| `sowbot_avoid/sim.py` | `simulate()`, `SimDriver`, `World`, and a 3x3 test grid |
| `sowbot_avoid/topo.py` | Loads the F2C topological map (YAML) or a JSON fixture into a `Graph` |
| `sowbot_avoid/__main__.py` | CLI for a real robot |
| `sweeps/maize_sweep.py` | Batch sim over the maize map: scenarios, loss rates, robot counts |
| `tests/` | pytest suite. `tests/data/maize_map_fixture.json` is a 9-node map |

## Install

Python 3.10 or later.

    pip install -e ".[test]"       # sim, sweep and tests (pytest, pyyaml)
    pip install -e ".[radio]"      # adds meshcore>=2.3.15 for a real radio
    pip install -e ".[topo]"       # pyyaml only, to read YAML maps

The core package has no dependencies.

## Run the tests

    pytest

The suite includes simulated runs with real timing, so it takes about 30 s.

## Run the simulation

There is no simulation CLI. Call `simulate()` directly:

    import asyncio
    from sowbot_avoid.sim import simulate

    routes = {1: [4, 5, 6, 3, 11], 2: [2, 5, 8, 7, 12], 3: [6, 5, 4, 1, 10]}
    print(asyncio.run(simulate(routes, loss=0.2, seed=2)))

This uses the built-in 3x3 grid (2.5 m spacing, nodes 1 to 9, docks 10 to 12). Robots 1 and 3 meet head-on and all three cross at node 5.

Arguments: `loss` is the random frame loss probability. `scale` (default 0.1) multiplies every timer, so 0.1 runs ten times faster than real time. `timeout` is in unscaled seconds. `graph` replaces the grid. `delays` maps robot id to a late start in seconds. `supersede=False` keeps old resends queued.

Returns a dict:

| Key | Meaning |
|---|---|
| `done` | Every agent finished its route |
| `sep` | Smallest separation seen between any two robots, in map units |
| `sep_live` | Same, between two robots both still running |
| `sep_parked` | Same, where one robot had finished (its peers ignore it, see below) |
| `frames`, `lost` | Frames sent on the channel, and frames lost |
| `retreats` | Deadlock retreats started, all robots |
| `secs` | Elapsed time, unscaled |
| `sent` | Frames sent per robot |

### What the simulator models

- One collision domain. Every robot hears every other robot, so there is no range limit and no hidden node.
- Frames that overlap in time are both lost. On top of that, each frame is lost with probability `loss`. Delivery has a random delay.
- `SimDriver` moves a robot in a straight line between node coordinates at 0.5 m/s. No dynamics, no tracking error, no obstacles.
- The agents run on wall-clock time, scaled. Results vary with machine load.

### Sweep over the maize map

    python sweeps/maize_sweep.py --map tests/data/maize_map_fixture.json --seeds 3
    python sweeps/maize_sweep.py --scenario opposed --robots 2 4 --loss 0 0.2 --park none --seeds 20
    python sweeps/maize_sweep.py --seeds 10 --jsonl out.jsonl

Without `--map` it looks upwards for `maps/maize_map` and falls back to the fixture.
Run it on an idle machine, one job at a time. It is not part of pytest.
The default grid is 720 runs. Narrow it with the options below.

| Option | Values |
|---|---|
| `--scenario` | `follow` (same direction along the rows), `opposed` (alternating directions), `random` (random start and goal), `local` (start and goal within `--local-radius` of an anchor) |
| `--robots` | Robot counts. Default 2 4 6. A count the map cannot hold at the clearance is skipped |
| `--loss` | Frame loss rates. Default 0 0.1 0.2 |
| `--park` | `docks` (each route ends on its own dock, 1.5 m off the goal), `none` (routes end on map nodes), `both` |
| `--resends` | `default` (drop older resends), `keep`, `both` |
| `--seeds`, `--seed0` | Seeds per cell, and the first seed |
| `--scale`, `--timeout`, `--clearance`, `--stagger`, `--jsonl` | Timer scale, unscaled timeout (default 600 s), clearance, random late starts, per-run JSON output |

The report columns are: `done%` (all robots finished), `live<clr` (runs where two running robots came closer than the clearance), `parked<clr` (runs where a robot passed a finished one that close), median `secs` and `frames`, and mean `retr`.

### Observed on the 9-node fixture (loss 0, docks, default resends, 2 to 3 seeds)

| Scenario | Robots | Finished | Min live separation | Runs passing a parked robot closer than 1 m |
|---|---|---|---|---|
| follow | 2 | 100% | 1.18 m | 0 of 3 |
| follow | 3 | 100% | 1.18 m | 2 of 3 (0.87 m) |
| random | 2 | 100% | 1.20 m | 0 of 3 |
| random | 3 | 100% | 1.20 m | 2 of 3 (0.21 m) |
| opposed | 2 | **0%** | 1.20 m | 0 of 2 |

The opposed runs hit the 10-retreat limit and timed out at 600 s. The fixture is tiny, so there are few side-step nodes, but it shows head-on meetings can livelock. The real `maps/maize_map` has not been swept.

## Run on a robot

    python -m sowbot_avoid --serial /dev/ttyUSB0 --id 2 --map map.json --route 4,5,6,11 \
        --channel-secret-file /etc/sowbot/channel.key --driver mypkg.mymod:MyDriver

`map.json`:

    {"nodes": {"4": [0, 0], "5": [2.5, 0], "6": [5, 0], "11": [7.5, 0]},
     "edges": [[4, 5], [5, 6], [6, 11]], "park": [11]}

Edges work both ways. Every route node needs coordinates and every hop must be an edge.
`park` lists docks. A route must end at one, and retreats never use them, because a robot that has sent FINISHED is ignored by the others.
Without coordinates, two edges conflict only if they share a node.

| Option | Meaning |
|---|---|
| `--serial` | Serial port of the companion radio |
| `--id` | Robot id, 1 to 255. Node ids are 0 to 65535 |
| `--map`, `--route` | Map JSON and comma-separated node ids |
| `--driver` | `module:Class` implementing `Driver` |
| `--clearance` | Conflict distance in map units. Default 1.0 |
| `--channel-idx`, `--channel-name` | Radio channel slot (default 1) and name (default `sowbot`) |
| `--channel-secret-file` | File with 32 hex characters, or `-` for stdin. Omit if the channel is already set on the radio |
| `--flood` | Flood through repeaters. Default is zero-hop |
| `--keep-resends` | Keep old resends queued when a newer frame is sent, as in the issue text |

Options, map and route are checked before the radio opens.
Needs companion firmware 1.15.0 or later.

### Driver

    from sowbot_avoid import Driver

    class MyDriver(Driver):
        async def drive(self, a, b):
            ...  # send the goal for edge a -> b, return when the robot is at b

If `drive` raises, the agent stops and the robot's position is left unchanged. The agent does not stop the robot itself.

## Algorithm

Every robot knows the same map and flies a fixed route of nodes. Robots do not negotiate. Each one broadcasts what it holds and what it intends to take, keeps a notebook of the latest frame from every peer, and only drives an edge that nobody else holds or has priority over.

### Conflicts

Two node pairs conflict when the minimum distance between the two segments is below `clearance` (strictly less). A node is a zero-length segment. A node missing coordinates counts as a conflict.

### Frames

| Kind | Sent | Hold | Claims |
|---|---|---|---|
| HELLO | At boot | none | none |
| ENTER | Before driving an edge | The edge | Next 2 edges |
| BEACON | Every 5 s while driving, if nothing else was sent | The edge | Next 2 edges |
| ARRIVED | On reaching a node | The node | Next 2 edges |
| FINISHED | Route done | none | none |

A frame is 3 bytes plus 2 per node, at most 11 bytes, so it fits one AES block (21 bytes on air, zero-hop). Each new frame increments an 8-bit sequence number. A receiver accepts a frame only if its sequence is 1 to 127 ahead of the stored one, modulo 256. A HELLO with a different sequence resets the entry, because the sender rebooted.

### Priority

Before driving edge `e`, a robot is blocked by a peer if:

1. the peer's hold conflicts with `e`, always; or
2. the peer has a lower id and one of its claims conflicts with `e`, unless that claim contains the node we are standing on.

A retreating robot (below) gives way to all claims, whatever the id. FINISHED peers are ignored.
The lower id therefore wins a contested edge. Holds always win over claims.

### Driving an edge

1. Queue ENTER and wait until the radio has sent it. If it was dropped or failed, do not drive.
2. Check again for blockers. If one appeared, send ARRIVED to withdraw the hold and wait.
3. Drive, sending a BEACON if nothing has been sent for 5 s.
4. On arrival, keep the edge held for 2 s (the tail), then send ARRIVED.

At start-up a robot waits a random 0 to 1 s, sends HELLO, listens for 1.5 s, waits until its own node is not held, sends ARRIVED, then waits 1.5 s for rival first claims before moving.

### Loss handling

There are no acknowledgements. Instead:

- ENTER is resent after about 0.7 s and 2 s. ARRIVED and FINISHED after about 1, 3 and 7 s. Each delay is jittered by 0.7 to 1.3.
- A resend keeps its sequence number and carries a resend index, so every copy differs in plaintext and gets past MeshCore's duplicate filter.
- A newer frame drops older queued resends, except FINISHED (`--keep-resends` disables this).
- Listen before talk: transmission waits while a frame was heard in the last 0.1 s, with random back-off of 0.12 to 0.48 s, for at most 2 s.

### Deadlock retreat

Two robots can claim each other's position and both wait forever. Only the lower id may break this, since a robot blocked by a lower id just waits for it. A robot blocked only by higher ids retreats when:

- a blocker's first claim conflicts with the node we stand on (a mutual block) and we have been blocked for 3 s; or
- we have been blocked for a random 10 to 20 s.

The retreat does a breadth-first search from the current node, over edges that conflict with no peer's hold and never through a park node. It picks the nearest node that is not the next route node and conflicts with no hold or claim. The route becomes the path to that node, then the path back to the blocked node, then the rest of the original route.
While retreating a robot sets the retreat flag and sends no claims.
Each retreat hop waits 1.5 s. If a conflicting hold is still there, the robot yields the hop. If the rival is a higher-id robot that announced its own retreat within the window, the rival loses the tie.
At most 10 retreats per mission. If no side-step node exists, the robot keeps waiting.

## Limits

- Not run on hardware. MeshCore timing, range and loss are assumed, not measured.
- Everything depends on timers and a shared map. Robots with different maps or node id tables will not see each other correctly. `topo.py` exposes a digest of the id table for comparison, but the agent does not exchange it.
- A robot that has sent FINISHED is invisible to its peers. The CLI therefore insists on a park node, but nothing stops a robot passing close to a docked one. The sweep shows this.
- Head-on meetings on single-lane maps can exhaust the retreat limit (see the sweep table).
- Notebook entries never expire. A peer that dies or goes out of range while holding an edge blocks that edge indefinitely.
- A failed radio send stops the robot starting its next edge, but it finishes the edge it is on. Lost reception is not detected.
- Only the node graph is considered. Robot size, speed and stopping distance are folded into one `clearance`.
