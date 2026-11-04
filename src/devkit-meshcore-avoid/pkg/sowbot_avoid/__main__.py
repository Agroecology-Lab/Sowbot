"""python -m sowbot_avoid --serial PORT --id N --map map.json --route 4,5,6 --driver pkg.mod:Class"""
import argparse
import asyncio
import importlib
import math
import re
import sys
import time

from .agent import Agent
from .graph import Graph
from .radio import MeshRadio


class MissionError(ValueError):
    """A bad option or map, reported before the radio is opened or the robot moves."""


def read_secret(path):
    """Return the 32 hex character channel key from a file, or from standard input for "-".

    MissionError if the file cannot be read or does not hold exactly 16 bytes of hex.
    """
    try:
        if path == "-":
            text = sys.stdin.read()
        else:
            with open(path) as fh:
                text = fh.read()
    except OSError as e:
        raise MissionError(f"cannot read channel secret: {e}") from e
    text = text.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", text):
        raise MissionError("channel secret must be 32 hex characters (16 bytes)")
    return text


def check_mission(a, graph, route):
    """Raise MissionError unless the id, clearance, channel and route can be flown safely.

    A route must follow map edges (the Agent checks that), have coordinates for every
    node when the map has any. When the map lists park nodes (docks), the route must end
    at one: other robots ignore a robot that has finished, so it must not stay on a
    traversable node. A map with no park nodes puts no condition on the last node.
    """
    if not 1 <= a.id <= 255:
        raise MissionError(f"--id must be 1-255, got {a.id}")
    if not (math.isfinite(a.clearance) and a.clearance > 0):
        raise MissionError(f"--clearance must be a finite number above 0, got {a.clearance}")
    if not 0 <= a.channel_idx <= 255:
        raise MissionError(f"--channel-idx must be 0-255, got {a.channel_idx}")
    if graph.xy:
        missing = sorted({n for n in route if n not in graph.xy})
        if missing:
            raise MissionError(f"route nodes without coordinates in the map: {missing}")
    if graph.park and route[-1] not in graph.park:
        raise MissionError(f"route must end at a park node of the map {sorted(graph.park)}, not {route[-1]}")


async def run(a):
    """Run the configured route with a dynamically loaded driver and MeshCore radio.

    Route, mission and key validation happens before the radio is opened and
    raises MissionError for invalid values. Map file, JSON and driver loading
    errors propagate, as do radio and driver errors. The radio is stopped after
    the agent exits, including on agent failure, if it started successfully.
    """
    try:
        route = [int(x) for x in a.route.split(",")]
    except ValueError as e:
        raise MissionError(f"--route must be comma-separated integers: {e}") from e
    graph = Graph.load(a.map, a.clearance)
    check_mission(a, graph, route)
    secret = read_secret(a.channel_secret_file) if a.channel_secret_file else None
    mod, cls = a.driver.split(":")
    driver = getattr(importlib.import_module(mod), cls)()
    radio = MeshRadio(a.serial, a.channel_idx, a.channel_name, secret, not a.flood)
    t0 = time.monotonic()
    log = lambda *x: print(f"{time.monotonic() - t0:7.1f}s robot {a.id}:", *x, flush=True)
    try:
        agent = Agent(a.id, route, graph, radio, driver, log=log)
    except ValueError as e:
        raise MissionError(str(e)) from e
    await radio.start()
    try:
        await agent.run()
    finally:
        await radio.stop()


def main():
    """Parse command-line options and run the configured mission."""
    ap = argparse.ArgumentParser(prog="sowbot_avoid", description=__doc__, allow_abbrev=False)   # --channel-secret must not match the -file option
    ap.add_argument("--serial", required=True)
    ap.add_argument("--id", type=int, required=True, help="robot id, 1-255")
    ap.add_argument("--map", required=True)
    ap.add_argument("--route", required=True, help="comma-separated node ids, ending at a park node if the map has any")
    ap.add_argument("--driver", required=True, help="module:Class implementing Driver")
    ap.add_argument("--clearance", type=float, default=1.0)
    ap.add_argument("--channel-idx", type=int, default=1)
    ap.add_argument("--channel-name", default="sowbot")
    ap.add_argument("--channel-secret-file", metavar="FILE",
                    help="file holding the 16 byte channel key as 32 hex chars, or - for stdin; "
                         "omit if the channel is already set up (kept off the command line so it "
                         "stays out of the process list and shell history)")
    ap.add_argument("--flood", action="store_true", help="flood through repeaters instead of zero-hop")
    a = ap.parse_args()
    try:
        asyncio.run(run(a))
    except MissionError as e:
        ap.error(str(e))


if __name__ == "__main__":
    main()
