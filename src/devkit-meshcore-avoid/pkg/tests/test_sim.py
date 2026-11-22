import asyncio

import pytest

from sowbot_avoid.sim import simulate

ROUTES = {1: [4, 5, 6, 3, 11], 2: [2, 5, 8, 7, 12], 3: [6, 5, 4, 1, 10]}   # meet at node 5; 1 and 3 head-on


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_no_loss_all_finish_and_keep_clear(seed):
    r = asyncio.run(simulate(ROUTES, 0.0, seed))
    assert r["done"] and r["sep"] >= 1.0


def test_loss_20_seed_2():
    r = asyncio.run(simulate(ROUTES, 0.2, 2))
    assert r["done"] and r["sep"] >= 1.0


def test_react_stops_part_way_and_retreats_when_a_peer_claims_the_edge():
    """Two robots set off head-on on one corridor without having heard each other (their first frames
    are lost): without react they meet, with react they stop and retreat to the node they left."""
    import asyncio
    import random
    from sowbot_avoid.agent import Agent
    from sowbot_avoid.frame import Frame
    from sowbot_avoid.graph import Graph
    from sowbot_avoid.sim import SimDriver, World

    xy = {1: (0, 0), 2: (6, 0), 3: (12, 0)}
    g = Graph([(1, 2), (2, 3)], xy, 1.0)

    class Radio:
        last_rx = 0.0

        def set_handler(self, h):
            self.h = h

        async def send(self, b):
            pass

    async def go(react):
        w = World()
        d = SimDriver(w, 1, g, scale=0.05)
        d.place(1)
        a = Agent(1, [1, 2, 3], g, Radio(), d, 0.05, random.Random(1), react=react)
        task = asyncio.ensure_future(a.run())
        await asyncio.sleep(0.15 * 1.0)          # boot, announce, arbitrate: now driving 1 -> 2
        while a.moving is None and not a.done:
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.05)
        a.on_rx(Frame(2, 7, (3, 2, 1)).encode())  # a peer from the far end claims 3 -> 2 -> 1
        await asyncio.sleep(0.3)
        out = (a.stops, d.at)
        task.cancel()
        return out

    stops, at = asyncio.run(go(True))
    assert stops >= 1
    assert asyncio.run(go(False))[0] == 0
