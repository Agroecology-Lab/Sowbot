"""Node claim: on entering a node, announce it and the next two intended nodes (Sowbot issue #202)."""
import asyncio
import contextlib
import heapq
import math
import random
import time

from .frame import Frame

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
        self.hold_idx = self.hold_end = None    # route index of a side-step stop where we wait for the route to clear
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
            if self.g.conflict(edge, f.hold()):
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
        """Send current node + next LOOKAHEAD nodes. Retries until the radio takes it.

        At a side-step stop only the node is sent, no claims: the robot waits there, and claims on
        the way back would block the peers that it is waiting for.
        """
        nxt = [] if self.hold_idx == self.idx else self.route[self.idx + 1:self.idx + 1 + LOOKAHEAD]
        while True:
            self.seq = (self.seq + 1) & 255
            frame = Frame(self.rid, self.seq, tuple([self.cur] + nxt))
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
        """Follow the route, then mark the agent done. Driver errors propagate.

        If blocked long enough, the agent may add a side-step to its route,
        announce the revised claims and wait for arbitration before retrying.
        At the side-step stop it waits until the route onward is announced clear.
        """
        self.radio.set_handler(self.on_rx)
        await asyncio.sleep(self.rng.uniform(0, 1.0) * self.k)      # boot jitter
        while True:
            await self._announce()                                  # the whole protocol: one frame per node
            if self.idx >= len(self.route) - 1:
                break
            await asyncio.sleep(ARBITRATE * self.k)                 # let rival claims arrive
            if self.hold_idx == self.idx:                           # at a side-step stop
                while self._route_blocked():
                    await self._wait(0.1)
                self.hold_idx = None
                self.log('route clear, resuming')
                await self._announce()                              # now with claims again
                await asyncio.sleep(ARBITRATE * self.k)
            edge = (self.cur, self.route[self.idx + 1])
            while blockers := self.blocked_by(edge):
                if self._stuck(blockers) and self._sidestep():
                    await self._announce()                          # peers still hold our old claims
                    await asyncio.sleep(ARBITRATE * self.k)
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
        """Detour to the nearest free row waypoint, then rejoin the route.

        Stops are row waypoints only (Graph.rows): never an _IN or _OUT node, which the robot
        passes through. A waypoint is skipped when any other robot has announced a node of its
        row, so with row 1 in use the nearest waypoint of another row is taken. The stop must
        also be clear of every hold and claim. "Nearest" is by path length over edges that no
        hold blocks. On success the route becomes the way out, then the way on to the node we
        were heading for, and run() waits at the stop until that stretch is announced clear.
        """
        holds = [f.hold() for f in self.book.values()]
        claims = [c for f in self.book.values() for c in f.claims()]
        taken = {self.g.row_key(n) for f in self.book.values() for n in f.nodes}
        free_edge = lambda u, v: not any(self.g.conflict((u, v), h) for h in holds)
        free_node = lambda n: not any(self.g.conflict((n, n), x) for x in holds + claims)
        nxt = self.route[self.idx + 1]
        dist, prev, heap = {self.cur: 0.0}, {}, [(0.0, self.cur)]
        while heap:                                         # Dijkstra: nearest first
            d, u = heapq.heappop(heap)
            if d > dist[u]:
                continue
            for v in sorted(self.g.adj.get(u, ())):
                if v in self.g.park or not free_edge(u, v):
                    continue
                w = math.dist(self.g.xy[u], self.g.xy[v]) if u in self.g.xy and v in self.g.xy else 1.0
                if d + w < dist.get(v, math.inf):
                    dist[v], prev[v] = d + w, u
                    heapq.heappush(heap, (d + w, v))
        for d, v in sorted((d, v) for v, d in dist.items()):
            if v == nxt or v == self.cur or v not in self.g.rows or self.g.row_key(v) in taken or not free_node(v):
                continue
            back = self.g.path(v, nxt)
            if not back:
                continue
            out = [v]
            while out[-1] != self.cur:
                out.append(prev[out[-1]])
            out.reverse()
            self.route[self.idx:self.idx + 2] = out + back[1:]
            self.hold_idx = self.idx + len(out) - 1
            self.hold_end = self.hold_idx + len(back) - 1       # index of the node we were heading for
            self.sidesteps += 1
            self.blocked_since = None
            self.log(f'side-step {self.sidesteps}: via {out}')
            return True
        return False

    def _route_blocked(self):
        """IDs of lower-id robots blocking an edge of the stretch from the side-step stop to the
        node we were heading for, plus the LOOKAHEAD edges after it.

        Higher ids are ignored here, so waiting robots can never wait on each other in a cycle:
        a higher id yields to us. The per-edge check in run() still stops us from driving into
        any hold or committed claim.
        """
        stretch = self.route[self.hold_idx:self.hold_end + 1 + LOOKAHEAD]
        out = set()
        for e in zip(stretch, stretch[1:]):
            out |= self.blocked_by(e)
        return {r for r in out if r < self.rid}
