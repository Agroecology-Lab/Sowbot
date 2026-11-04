"""Simulated robots and a small test map. No hardware needed."""
import asyncio
import math
import random
import time

from .agent import Agent
from .driver import Driver
from .graph import Graph
from .radio import LoopBus


class World:
    def __init__(self):
        self.pos, self.min_sep, self.pair = {}, float("inf"), None
        # A finished robot keeps its last hold, so its separation is tracked apart:
        # min_live is between two robots still running, min_parked involves a finished one.
        self.finished, self.min_live, self.min_parked = set(), float("inf"), float("inf")

    def set(self, rid, p):
        """Update a robot's position and the smallest separation observed so far."""
        self.pos[rid] = p
        for o, q in self.pos.items():
            if o != rid:
                d = math.hypot(p[0] - q[0], p[1] - q[1])
                if d < self.min_sep:
                    self.min_sep, self.pair = d, (rid, o)
                if rid in self.finished or o in self.finished:
                    self.min_parked = min(self.min_parked, d)
                else:
                    self.min_live = min(self.min_live, d)


class SimDriver(Driver):
    def __init__(self, world, rid, graph, speed=0.5, scale=1.0):
        self.w, self.rid, self.g, self.speed, self.k = world, rid, graph, speed, scale

    def place(self, node):
        """Place the robot at a graph node; a missing coordinate raises KeyError."""
        self.w.set(self.rid, self.g.xy[node])

    async def drive(self, a, b):
        """Simulate travel between node coordinates, updating the world position.

        Missing coordinates raise KeyError; zero speed raises ZeroDivisionError.
        """
        p, q = self.g.xy[a], self.g.xy[b]
        dur = max(math.hypot(q[0] - p[0], q[1] - p[1]) / self.speed * self.k, 1e-3)
        t0 = time.monotonic()
        while True:
            f = min(1.0, (time.monotonic() - t0) / dur)
            self.w.set(self.rid, (p[0] + f * (q[0] - p[0]), p[1] + f * (q[1] - p[1])))
            if f >= 1.0:
                return
            await asyncio.sleep(0.01)



def grid(n=3, step=2.5):
    """Build an n by n grid with spacing step and three dock nodes numbered above n * n.

    Docks hang off the first node of row 0 (west), the last node of row 0 (east)
    and the first node of the last row (west). For n=3 they are 10, 11 and 12.
    """
    xy = {r * n + c + 1: (c * step, r * step) for r in range(n) for c in range(n)}
    edges = [(i, i + 1) for i in xy if i % n] + [(i, i + n) for i in xy if i + n in xy]
    top_left, top_right, bottom_left = 1, n, (n - 1) * n + 1
    d1, d2, d3 = n * n + 1, n * n + 2, n * n + 3
    xy.update({d1: (-step, 0.0), d2: ((n - 1) * step + step, 0.0), d3: (-step, (n - 1) * step)})
    edges += [(top_left, d1), (top_right, d2), (bottom_left, d3)]
    return Graph(edges, xy, park=(d1, d2, d3))


async def simulate(routes, loss, seed, scale=0.1, timeout=300, graph=None, delays=None):
    """Run routes keyed by robot ID on the simulated grid, or on graph when given.

    Every robot stands at its first node from the start. delays maps a robot ID to
    seconds (unscaled) before its agent starts; until then peers cannot see it.

    Loss is the random frame loss probability; scale multiplies simulated timing
    and timeout is in unscaled seconds. A TimeoutError, including one raised by
    an agent, yields partial results; other errors propagate.

    The result includes done (whether all agents finished), sep (minimum observed
    separation), sep_live (between running robots), sep_parked (involving a
    finished robot), and pair (IDs at the minimum sep, or None). Separations use
    graph coordinate units and are infinite if no pair was observed. It also
    includes frames, lost, sidesteps, and secs (elapsed unscaled seconds).
    """
    rng, g, world = random.Random(seed), graph or grid(), World()
    bus, agents, delays = LoopBus(rng, loss, scale), [], delays or {}
    for rid, route in routes.items():
        d = SimDriver(world, rid, g, scale=scale)
        d.place(route[0])
        agents.append(Agent(rid, route, g, bus.radio(), d, scale, random.Random(seed * 100 + rid)))
        # the agent logs "finished" once its route is done; from then on it counts as parked
        agents[-1].log = (lambda r: lambda *a: world.finished.add(r) if a and a[0] == "finished" else None)(rid)

    async def launch(a):
        await asyncio.sleep(delays.get(a.rid, 0.0) * scale)
        await a.run()

    t0 = time.monotonic()
    try:
        await asyncio.wait_for(asyncio.gather(*(launch(a) for a in agents)), timeout * scale)
    except asyncio.TimeoutError:
        pass
    return dict(done=all(a.done for a in agents), sep=world.min_sep, sep_live=world.min_live,
                sep_parked=world.min_parked, pair=world.pair, frames=bus.frames, lost=bus.lost,
                sidesteps=sum(a.sidesteps for a in agents), secs=(time.monotonic() - t0) / scale)

