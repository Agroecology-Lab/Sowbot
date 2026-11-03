"""Node claim: on entering a node, announce it and the next two intended nodes (Sowbot issue #202)."""
import asyncio
import contextlib
import random
import time

from .frame import ARRIVED, Frame

# Seconds, all multiplied by the timescale (tests only).
ARBITRATE = 1.5     # after announcing, wait for rival claims; also the age at which a claim counts as committed
TAIL = 2.0          # keep the old node held while the robot's tail clears
BACKOFF = (0.12, 0.48)
LBT_MAX = 2.0       # longest listen-before-talk wait
LOOKAHEAD = 2       # intended nodes announced beyond the current one
STUCK = 3.0         # blocked this long by higher ids only: side-step
SIDESTEP_MAX = 10   # stop side-stepping after this many


class Agent:
    def __init__(self, rid, route, graph, radio, driver, scale=1.0, rng=None, log=None):
        """Configure an agent with a nonempty route of node IDs.

        ValueError for a robot ID outside 1-255, an empty route, a node outside
        0-65535, or a hop that is not a map edge.
        """
        route = list(route)
        if not 1 <= rid <= 255:
            raise ValueError(f'robot id {rid} outside 1-255')
        if not route:
            raise ValueError('route is empty')
        bad = [n for n in route if not 0 <= n <= 65535]
        if bad:
            raise ValueError(f'route nodes outside 0-65535: {bad}')
        hops = [(a, b) for a, b in zip(route, route[1:]) if b not in graph.adj.get(a, ())]
        if hops:
            raise ValueError(f'route hops that are not map edges: {hops}')
        self.rid, self.route, self.g, self.radio, self.driver = rid, route, graph, radio, driver
        self.k, self.rng = scale, rng or random.Random()
        self.log = log or (lambda *a: None)
        self.book = {}      # robot id -> latest Frame
        self.seen = {}      # robot id -> arrival time of that frame
        self.seq = int(self.rng.uniform(0, 256)) & 255
        self.idx, self.cur = 0, route[0]
        self.wake = asyncio.Event()
        self.blocked_since, self.sidesteps = None, 0
        self.done = False

    # ---- receive -------------------------------------------------------------------------------
    def on_rx(self, payload):
        """Keep the latest valid frame per other robot; a repeated seq is ignored."""
        f = Frame.decode(payload)
        if f is None or f.rid == self.rid:
            return
        old = self.book.get(f.rid)
        if old is not None and f.seq == old.seq:
            return
        self.book[f.rid] = f
        self.seen[f.rid] = time.monotonic()
        self.wake.set()

    # ---- rules ---------------------------------------------------------------------------------
    def blocked_by(self, edge):
        """Return IDs whose held node or claim conflicts with edge.

        A hold always blocks. A claim blocks if its sender has a lower ID (it wins ties),
        or if it is older than ARBITRATE (the sender has committed and is moving).
        Claims that include the node we stand on are ignored.
        """
        now, out = time.monotonic(), set()
        for rid, f in self.book.items():
            h = f.hold()
            if h and self.g.conflict(edge, h):
                out.add(rid)
                continue
            if rid < self.rid or now - self.seen[rid] >= ARBITRATE * self.k:
                for c in f.claims():
                    if self.cur not in c and self.g.conflict(edge, c):
                        out.add(rid)
                        break
        return out

    # ---- send ----------------------------------------------------------------------------------
    async def _announce(self):
        """Send current node + next LOOKAHEAD nodes. Retries until the radio takes it."""
        nxt = self.route[self.idx + 1:self.idx + 1 + LOOKAHEAD]
        while True:
            self.seq = (self.seq + 1) & 255
            frame = Frame(self.rid, self.seq, ARRIVED, 0, len(nxt), False, tuple([self.cur] + nxt))
            deadline = time.monotonic() + LBT_MAX * self.k
            while time.monotonic() - self.radio.last_rx < 0.1 * self.k and time.monotonic() < deadline:
                await asyncio.sleep(self.rng.uniform(*BACKOFF) * self.k)
            try:
                await self.radio.send(frame.encode())
                return
            except Exception as e:
                self.log('send failed', e)
                await asyncio.sleep(0.1 * self.k)

    async def _wait(self, t):
        self.wake.clear()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.wake.wait(), t * self.k)

    # ---- mission -------------------------------------------------------------------------------
    async def run(self):
        """Follow the route, then mark the agent done. Driver errors propagate."""
        self.radio.set_handler(self.on_rx)
        await asyncio.sleep(self.rng.uniform(0, 1.0) * self.k)      # boot jitter
        while True:
            await self._announce()                                  # the whole protocol: one frame per node
            if self.idx >= len(self.route) - 1:
                break
            await asyncio.sleep(ARBITRATE * self.k)                 # let rival claims arrive
            edge = (self.cur, self.route[self.idx + 1])
            while blockers := self.blocked_by(edge):
                if self._stuck(blockers) and self._sidestep():
                    edge = (self.cur, self.route[self.idx + 1])
                    continue
                await self._wait(0.1)
            self.blocked_since = None
            await self.driver.drive(*edge)
            self.cur, self.idx = edge[1], self.idx + 1
            await asyncio.sleep(TAIL * self.k)
        self.log('finished')
        self.done = True

    # ---- deadlock ------------------------------------------------------------------------------
    def _stuck(self, blockers):
        """True once blocked for STUCK seconds by higher ids only: the lower id gives way."""
        now = time.monotonic()
        if self.blocked_since is None:
            self.blocked_since = now
        return (self.sidesteps < SIDESTEP_MAX and all(j > self.rid for j in blockers)
                and now - self.blocked_since >= STUCK * self.k)

    def _sidestep(self):
        """Detour via the nearest row node that no other robot holds or claims, then rejoin the route.

        Headland nodes are driven through but never stopped on.
        """
        holds = [h for f in self.book.values() if (h := f.hold())]
        claims = [c for f in self.book.values() for c in f.claims()]
        free_edge = lambda u, v: not any(self.g.conflict((u, v), h) for h in holds)
        free_node = lambda n: not any(self.g.conflict((n, n), x) for x in holds + claims)
        nxt = self.route[self.idx + 1]
        reach, queue = {self.cur}, [self.cur]
        for u in queue:                                     # breadth first: nearest first
            for v in sorted(self.g.adj.get(u, ())):
                if v in reach or v in self.g.park or not free_edge(u, v):
                    continue
                reach.add(v)
                queue.append(v)
                if v != nxt and v in self.g.rows and free_node(v):
                    out, back = self.g.path(self.cur, v, free_edge), self.g.path(v, nxt)
                    if out and back:
                        self.route[self.idx:self.idx + 2] = out + back[1:]
                        self.sidesteps += 1
                        self.blocked_since = None
                        self.log(f'side-step {self.sidesteps}: via {out}')
                        return True
        return False
