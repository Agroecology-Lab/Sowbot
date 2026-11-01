"""python -m sowbot_avoid --serial PORT --id N --map map.json --route 4,5,6 --driver pkg.mod:Class"""
import argparse
import asyncio
import importlib
import time

from .agent import Agent
from .graph import Graph
from .radio import MeshRadio


async def run(a):
    graph = Graph.load(a.map, a.clearance)
    mod, cls = a.driver.split(":")
    driver = getattr(importlib.import_module(mod), cls)()
    radio = MeshRadio(a.serial, a.channel_idx, a.channel_name, a.channel_secret, not a.flood)
    await radio.start()
    t0 = time.monotonic()
    log = lambda *x: print(f"{time.monotonic() - t0:7.1f}s robot {a.id}:", *x, flush=True)
    agent = Agent(a.id, [int(x) for x in a.route.split(",")], graph, radio, driver, log=log,
                  supersede=not a.keep_resends)
    try:
        await agent.run()
    finally:
        await radio.stop()


def main():
    ap = argparse.ArgumentParser(prog="sowbot_avoid", description=__doc__)
    ap.add_argument("--serial", required=True)
    ap.add_argument("--id", type=int, required=True)
    ap.add_argument("--map", required=True)
    ap.add_argument("--route", required=True, help="comma-separated node ids")
    ap.add_argument("--driver", required=True, help="module:Class implementing Driver")
    ap.add_argument("--clearance", type=float, default=1.0)
    ap.add_argument("--channel-idx", type=int, default=1)
    ap.add_argument("--channel-name", default="sowbot")
    ap.add_argument("--channel-secret", help="16 bytes as 32 hex chars; omit if the channel is already set up")
    ap.add_argument("--flood", action="store_true", help="flood through repeaters instead of zero-hop")
    ap.add_argument("--keep-resends", action="store_true",
                    help="keep older resends queued when a newer frame is sent (as in the issue text)")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
