# sowbot-avoid

Robot-to-robot collision avoidance over [MeshCore](https://github.com/meshcore-dev/meshcore_py), implementing [Sowbot #202](https://github.com/Agroecology-Lab/Sowbot/issues/202).
One agent per robot, each with its own companion radio. No central controller.

**Status: simulated only, never run on hardware.** Not a safety function: onboard obstacle detection stays in charge.

# How the robots avoid each other

Every robot carries the same map and a fixed route made of waypoints. Each robot radios out where it is and where it is going next. It remembers the latest message from every other robot. It drives a stretch of path only if no other robot is on it or has the right to it. Robots never negotiate.

## Conflicts

- Two stretches of path conflict if they come closer than a set safe distance.
- A single waypoint counts as a stretch of zero length.

## Messages

- Each message is 8 bytes: robot ID, message number, and up to 3 waypoints.
- The first waypoint is where the robot is standing.
- The other two are the stretches it plans to drive next.
- A robot sends a message each time it reaches a waypoint, and once at start-up. It sends nothing else.

## Who goes first

Before driving a stretch, a robot checks every other robot. It must wait if either of these is true:

- The other robot is standing somewhere that conflicts with the stretch.
- The other robot plans to use a conflicting stretch, and either it has a lower ID or its last message is more than 1.5 s old (it has committed).

A robot that is standing in the way always wins. Otherwise the lower ID wins.

## Driving

1. Wait until nothing blocks the stretch.
2. Drive it.
3. After arriving, keep claiming the old waypoint for 2 s, then announce the new one.
4. After every announcement, wait 1.5 s for rival claims.

At start-up: wait a random 0 to 1 s, announce, then wait 1.5 s.

## Lost messages

- No confirmations, no resends.
- A robot that misses a message catches up from the next one, which arrives one waypoint later.
- Before sending, a robot waits if it heard anything in the last 0.1 s. It backs off 0.12 to 0.48 s, for at most 2 s.
- If a send fails, it retries every 0.1 s.

## Stepping aside

- Only the lower-ID robot gives way. If it has been blocked only by higher-ID robots for 3 s, it steps aside.
- It picks the nearest waiting spot in a crop row (by path length) that is clear of every robot and every plan, and whose row no other robot has announced.
- If any waypoint of a row has been announced, the whole row counts as taken. If row 1 is busy, it tries row 2, and so on.
- It never stops on row entry or exit points, headland points or docks. It can drive through them.
- It drives to the spot, waits until the stretch ahead (plus the next two) is announced clear, then carries on to its original waypoint.
- At most 10 side-steps. If no spot is free, it keeps waiting.

## Limitations

- Not tested on real robots. Radio timing, range and message loss are assumptions.
- Stored positions never expire. If a robot dies while claiming a stretch, that stretch stays blocked.
- A finished robot never releases its last position. It blocks that waypoint and everything within the safe distance for the rest of the run. End routes on a dock, away from the rows.
- Head-on meetings on tight maps can use up all 10 side-steps.
- All robots must use the same map and the same waypoint ID table. Unless every waypoint has a fixed ID set, IDs come from sorted waypoint names, so renaming or adding a waypoint changes them. A script can compute a checksum of the table, but the robots do not share or compare it.
- A failed radio send stops the next stretch but not the current one. Missed incoming messages are not detected.
- Robot size, speed and stopping distance are combined into the single safe distance.






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

No more than 1 robot per 100m2 is a sensible ballpark.

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
learance`.
