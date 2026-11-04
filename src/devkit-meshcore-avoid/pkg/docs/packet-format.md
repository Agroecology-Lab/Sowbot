# Packet format (MeshCore GRP_DATA)

Not run on hardware. Source for the MeshCore limits: meshcore-dev/meshcore @ 81dbae5.
The codec is `sowbot_avoid/frame.py`; what the agent sends is `sowbot_avoid/agent.py`.

## MeshCore constraints

| Item | Value |
|---|---|
| Packet type | `PAYLOAD_TYPE_GRP_DATA` (0x06) |
| Inner header | `data_type` u16 LE, `data_len` u8 |
| `data_type` | 0xFFFF (`DATA_TYPE_DEV`) |
| Max app payload | 165 B (`MeshCore.h`), 167 B (`companion_protocol.md`) |
| Cipher | AES-128 ECB, zero padded to 16 B blocks |
| Dedupe | ring of 160 hashes over type + encrypted payload; seen packets not delivered |
| Rule | two frames with the same plaintext are dropped as duplicates, so every frame must differ |

On-air size, zero-hop: 1 header + 1 path len + 1 channel hash + 2 MAC + padded blocks.

| App payload | Blocks | On air |
|---|---|---|
| 0-13 B | 1 | 21 B |
| 14-29 B | 2 | 37 B |

## Frame

There is one kind of frame. It says where the sender stands and where it will drive next.

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | robot id, 1-255 (0 is rejected) |
| 1 | 1 | seq. +1 on every frame, wraps at 256 |
| 2 | 2 each, 1-3 | node path, u16 LE |

- Node 0 of the path is the node the sender stands on: its hold.
- Nodes 1 and 2, if present, are its next two route nodes: its claims, the edges P0→P1 and P1→P2.
- Node ids are 0-65535. Every robot must use the same node id table (`avoid_id` in the map, or ids assigned by sorted name; see `topo.py`).
- The decoder rejects robot id 0 and any payload that is not 1 to 3 whole nodes.

| Frame | Bytes | On air |
|---|---|---|
| 2 claims, 3 nodes | 2 + 6 = 8 | 21 B |
| 1 claim, 2 nodes | 2 + 4 = 6 | 21 B |
| no claims, 1 node | 2 + 2 = 4 | 21 B |

A new seq on every frame makes each plaintext differ, so MeshCore's duplicate filter passes it.
There are no resends, and no hello, enter, beacon or finish frames.

## When the agent sends

One frame on arriving at each node, with the node and the next two route nodes (fewer near the end of the route: none on the last node). One frame at start-up.

At a side-step stop the robot waits, so it sends its node alone. It sends claims again, with a fresh frame, once the route onward is clear. Claims from a waiting robot would block the peers it is waiting for.

| Step | Seconds |
|---|---|
| Boot jitter before the first frame | 0 to 1 |
| After announcing, wait for rival claims (`ARBITRATE`) | 1.5 |
| Hold the old node while the tail clears (`TAIL`) | 2.0 after arriving |
| Listen before talk | wait while a frame was heard in the last 0.1, back off 0.12 to 0.48, at most 2 |
| Radio send failed | retry after 0.1 |

Each node costs 3.5 s plus the drive time, so the frame rate is about 0.15 per second at 3 m spacing and 0.24 per second at 0.5 m, whatever the speed.

## What the agent does with received frames

- Keeps the latest frame per robot id. A frame with the same seq as the stored one is ignored. Frames from itself are ignored.
- Entries never expire. A robot that stopped keeps its last hold and claims in every peer's book, including a robot that finished its route.

### Blocking

An edge is blocked by robot R when:

- R's hold conflicts with the edge. Always.
- Or a claim of R conflicts with the edge, and R has a lower id, or R's frame is older than 1.5 s (R has committed). Claims that include the node we stand on are ignored.

Conflict: with coordinates, the two segments are closer than the clearance (strict). Without coordinates, they share a node. A node missing a coordinate counts as a conflict.

### Side-step

A robot blocked for 3 s only by higher ids, and not yet side-stepped 10 times, side-steps. Higher ids never do.

- Stop: the nearest row waypoint (by path length over edges no hold blocks) that is clear of every hold and claim, and whose row no other robot has announced. Any announced node of a row takes the whole row.
- Never an `_IN`, `_OUT`, headland or dock node. The robot drives through those.
- Route becomes: the way to the stop, then the way on to the node it was heading for.
- At the stop: announce the node alone. Wait until the stretch back to that node, plus the next two edges, has no lower-id robot holding or claiming it. Then announce with claims and resume.

## Differences from sim (`claimsim2.py`)

| Item | Sim | This format |
|---|---|---|
| Payload size | 20 B on air | 21 B on air (one block) |
| `epoch`, `heard`, `whois`, `ackreq` | present | omitted |
| Map version | none | none |
| Duplicate drop | not modelled | new seq on every frame |
| Frames sent | ENTER, ARRIVED, FINISHED, resends | one frame per node, no resends |
