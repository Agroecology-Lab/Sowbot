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

    def set(self, rid, p):
        """Update a robot's position and the smallest separation observed so far."""
        self.pos[rid] = p
        for o, q in self.pos.items():
            if o != rid:
                d = math.hypot(p[0] - q[0], p[1] - q[1])
                if d < self.min_sep:
                    self.min_sep, self.pair = d, (rid, o)


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
    """Build an n by n grid with spacing step and three fixed dock nodes."""
    xy = {r * n + c + 1: (c * step, r * step) for r in range(n) for c in range(n)}
    edges = [(i, i + 1) for i in xy if i % n] + [(i, i + n) for i in xy if i + n in xy]
    xy.update({10: (-2.5, 0.0), 11: (7.5, 0.0), 12: (-2.5, 5.0)})     # docks
    edges += [(1, 10), (3, 11), (7, 12)]
    return Graph(edges, xy, park=(10, 11, 12))


async def simulate(routes, loss, seed, scale=0.1, timeout=300, supersede=True):
    """Run routes keyed by robot ID on the simulated grid.

    Loss is the random frame loss probability; scale multiplies simulated timing
    and timeout is in unscaled seconds. On timeout, return partial results;
    done indicates whether all agents finished. Other errors propagate.

    Return completion, minimum observed separation, frame and loss counts,
    retreat count, elapsed unscaled seconds, and send counts by robot ID.
    """
    rng, g, world = random.Random(seed), grid(), World()
    bus, agents = LoopBus(rng, loss, scale), []
    for rid, route in routes.items():
        d = SimDriver(world, rid, g, scale=scale)
        d.place(route[0])
        agents.append(Agent(rid, route, g, bus.radio(), d, scale, random.Random(seed * 100 + rid), supersede=supersede))
    t0 = time.monotonic()
    try:
        await asyncio.wait_for(asyncio.gather(*(a.run() for a in agents)), timeout * scale)
    except asyncio.TimeoutError:
        pass
    return dict(done=all(a.done for a in agents), sep=world.min_sep, frames=bus.frames, lost=bus.lost,
                retreats=sum(a.retreats for a in agents), secs=(time.monotonic() - t0) / scale,
                sent={a.rid: a.sent for a in agents})

