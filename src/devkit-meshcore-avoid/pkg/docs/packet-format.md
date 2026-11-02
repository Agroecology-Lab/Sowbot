# Legacy packet format (MeshCore GRP_DATA)

Proposal.
Not run on hardware or in the sim.
Source: meshcore-dev/meshcore @ 81dbae5.



cd ~/feldfreund_devkit_ros/src/devkit-meshcore-avoid/pkg
pipx install pytest pyyaml
python3 -m venv .venv
.venv/bin/pip install -e ".[test]"
.venv/bin/python -m pytest -q tests/test_topo.py tests/test_sweep_setup.py
.venv/bin/python sweeps/maize_sweep.py --seeds 5

## MeshCore constraints

| Item | Value |
|---|---|
| Packet type | `PAYLOAD_TYPE_GRP_DATA` (0x06) |
| Inner header | `data_type` u16 LE, `data_len` u8 |
| `data_type` | 0xFFFF (`DATA_TYPE_DEV`) |
| Max app payload | 165 B (`MeshCore.h`), 167 B (`companion_protocol.md`) |
| Cipher | AES-128 ECB, zero padded to 16 B blocks |
| Dedupe | ring of 160 hashes over type + encrypted payload; seen packets not delivered |
| Rule | every resend must differ in plaintext |

On-air size, zero-hop: 1 header + 1 path len + 1 channel hash + 2 MAC + padded blocks.

| App payload | Blocks | On air |
|---|---|---|
| 0-13 B | 1 | 21 B |
| 14-29 B | 2 | 37 B |

## Frame (3 B header + 2 B per node, 11 B max)

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | robot id |
| 1 | 1 | seq. +1 on every new frame (beacons included); resends keep it; compared mod 256 |
| 2 | 1 | flags |
| 3 | 2 each, 1-4 | node path, u16 LE |

## Flags

| Bits | Field | Values |
|---|---|---|
| 0-2 | kind | 0 HELLO, 1 ENTER, 2 BEACON, 3 ARRIVED, 4 FINISHED |
| 3-4 | resend index | 0 original; ENTER: 1 (~0.7 s), 2 (~2 s); ARRIVED: 1 (+1 s), 2 (+3 s), 3 (+7 s) |
| 5-6 | claim count | 0-2 |
| 7 | retreating | gives way to all, no claims |

## Node path by kind

| Kind | Hold | Claims | Nodes |
|---|---|---|---|
| ENTER, BEACON | edge P0→P1 | P1→P2, P2→P3 | 2 + claim count |
| ARRIVED | node P0 | P0→P1, P1→P2 | 1 + claim count |
| HELLO, FINISHED | none | none | 0 |

## Examples

| Frame | Bytes |
|---|---|
| ENTER, 2 claims, 4 nodes | 3 + 8 = 11 |
| ARRIVED, 2 claims, 3 nodes | 3 + 6 = 9 |
| FINISHED | 3 |

## Differences from sim (`claimsim2.py`)

| Item | Sim | This format |
|---|---|---|
| Payload size | 20 B on air | 21 B on air (one block) |
| `epoch`, `heard`, `whois`, `ackreq` | present | omitted (new protocol only) |
| Map version | none | none; 2 B spare in one block |
| Duplicate drop on resend | not modelled | handled by resend index and seq |
