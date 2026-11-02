# sowbot-avoid

Robot-to-robot collision avoidance over MeshCore, legacy scheme from [Sowbot #202](https://github.com/Agroecology-Lab/Sowbot/issues/202).
Runs on the robot's SBC next to [meshcore_py](https://github.com/meshcore-dev/meshcore_py), talking to one companion radio.

Status: untested on hardware. Not a safety function; onboard obstacle detection stays in charge.

## Layout

| Module | Contents |
|---|---|
| `frame.py` | Frame codec (`docs/packet-format.md`). No dependencies |
| `graph.py` | Map, conflict test, path search |
| `agent.py` | The rules: notebook, checks, repeats, beacons, retreats |
| `radio.py` | `MeshRadio` (real) and `LoopBus` (simulated channel) |
| `driver.py` | `Driver` interface: implement `drive(a, b)` for the robot's motion stack |
| `sim.py` | Simulated robots and a test map |

## Install and run

    pip install -e .[radio]
    python -m sowbot_avoid --serial /dev/ttyUSB0 --id 2 --map map.json --route 4,5,6,11 \
        --channel-secret-file /etc/sowbot/channel.key --driver mypkg.mymod:MyDriver

`map.json`:

    {"nodes": {"4": [0, 0], "5": [2.5, 0], "6": [5, 0], "11": [7.5, 0]},
     "edges": [[4, 5], [5, 6], [6, 11]], "park": [11]}

Edges work both ways.
Every node on a route needs coordinates, and every hop must be an edge of the map.
`park` lists docks: a route must end at one, and retreats never use them,
because a robot that has sent FINISHED is ignored by the others.
`--id` is 1 to 255 and node IDs are 0 to 65535.
`--channel-secret-file` reads 32 hex chars from a file (`-` reads standard input), so the key stays out of the process list and shell history.
Omit it if the channel is already set up on the radio.

## Tests

    pip install -e .[test]
    pytest

Needs companion firmware 1.15.0 or later. Zero-hop by default; `--flood` goes through repeaters.
