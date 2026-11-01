"""The rules: notebook, hold/claim checks, repeats, beacons, deadlock retreats (Sowbot issue #202)."""
import asyncio
import random
import time
from dataclasses import replace

from .frame import ARRIVED, BEACON, ENTER, FINISHED, HELLO, Frame


# Timers from issue #202, in seconds. Everything is multiplied by --timescale (tests only).
LISTEN = 1.5
ENTER_REPEATS = (0.7, 2.0)
ARRIVED_REPEATS = (1.0, 3.0, 7.0)
BEACON_EVERY = 5.0
TAIL = 2.0
BACKOFF = (0.12, 0.48)
STUCK_RETREAT = 3.0
FORCED_RETREAT = (10.0, 20.0)
RETREAT_MAX = 10


class Agent:
    def __init__(self, rid, route, graph, radio, driver, scale=1.0, rng=None, log=None, supersede=True):
        """Configure an agent with a nonempty route of node IDs.

        Scale multiplies protocol delays; supersede drops pending non-FINISHED
        transmissions when a new frame is queued. An empty route raises IndexError.
        """
        self.rid, self.route, self.g, self.radio, self.driver = rid, list(route), graph, radio, driver
        self.k, self.rng, self.supersede = scale, rng or random.Random(), supersede
        self.log = log or (lambda *a: None)
        self.book = {}                     # robot id -> latest Frame (the "notebook")
        self.seq, self.idx, self.cur = 0, 0, route[0]
        self.q, self.last_tx, self.sent = [], 0.0, 0
        self.wake, self.tx_wake = asyncio.Event(), asyncio.Event()
        self.retreating, self.retreats, self.resume = False, 0, None
        self.blocked_since = None
        self.forced_after = self.rng.uniform(*FORCED_RETREAT) * self.k
        self.done = False

    # ---- receive -------------------------------------------------------------------------------
    def on_rx(self, payload):
        """Record a valid, newer frame from another robot and wake the mission.

        Invalid, own, duplicate, and older frames are ignored; sequence numbers
        are compared modulo 256 using a forward window of 1 through 127.
        """
        f = Frame.decode(payload)
        if f is None or f.rid == self.rid:
            return
        old = self.book.get(f.rid)
        if old is not None and not 1 <= (f.seq - old.seq) & 255 <= 127:
            return                          # repeat of a frame we have, or an older one
        self.book[f.rid] = f
        self.wake.set()

    # ---- rules ---------------------------------------------------------------------------------
    def blocked_by(self, edge):
        """Return IDs blocking an edge by a hold or a qualifying claim.

        Claims from lower IDs block, or claims from any ID while retreating;
        claims containing the current node and FINISHED frames are ignored.
        Conflicts are determined by the graph.
        """
        out = set()
        for rid, f in self.book.items():
            if f.kind == FINISHED:
                continue
            h = f.hold()
            if h and self.g.conflict(edge, h):
                out.add(rid)
                continue
            if rid < self.rid or self.retreating:           # a retreating robot gives way to everyone
                for c in f.claims():
                    if self.cur in c:                       # claim on the spot we stand on: ignore
                        continue
                    if self.g.conflict(edge, c):
                        out.add(rid)
                        break
        return out

    def mutual(self, j):
        """Return whether robot j's first claim conflicts with our current node."""
        f = self.book.get(j)
        cl = f.claims() if f else ()
        return bool(cl) and self.g.conflict(cl[0], (self.cur, self.cur))

    # ---- send ----------------------------------------------------------------------------------
    def announce(self, kind, nodes, nclaims=0, repeats=()):
        """Queue a new frame and optional retransmissions.

        Repeats are delays in seconds before scaling and jitter. Pending
        non-FINISHED frames are discarded first when supersede is enabled.
        """
        self.seq = (self.seq + 1) & 255
        f = Frame(self.rid, self.seq, kind, 0, nclaims, self.retreating, tuple(nodes))
        now = time.monotonic()
        if self.supersede:      # a newer full-state frame makes older pending resends pointless
            self.q = [x for x in self.q if x[1].kind == FINISHED]
        self.q.append((now, f))
        for i, d in enumerate(repeats, 1):
            self.q.append((now + d * self.k * self.rng.uniform(0.7, 1.3), replace(f, resend=i)))
        self.q.sort(key=lambda x: x[0])
        self.last_tx = now
        self.tx_wake.set()

    async def _sender(self):
        while True:
            if not self.q:
                self.tx_wake.clear()
                await self.tx_wake.wait()
                continue
            wait = self.q[0][0] - time.monotonic()
            if wait > 0:
                self.tx_wake.clear()
                try:
                    await asyncio.wait_for(self.tx_wake.wait(), wait)
                except asyncio.TimeoutError:
                    pass
                continue
            _, f = self.q.pop(0)
            while time.monotonic() - self.radio.last_rx < 0.1 * self.k:     # crude listen-before-talk
                await asyncio.sleep(self.rng.uniform(*BACKOFF) * self.k)
            try:
                await self.radio.send(f.encode())
            except Exception as e:                                          # radio errors must not kill us
                self.log("send failed", e)
            self.last_tx = time.monotonic()
            self.sent += 1

    # ---- route helpers -------------------------------------------------------------------------
    def _claims_from(self, i):
        """Return up to two destination node IDs after index i, or none while retreating."""
        if self.retreating:
            return []
        return [self.route[j + 1] for j in range(i, min(i + 2, len(self.route) - 1))]

    async def _wait(self, t):
        self.wake.clear()
        try:
            await asyncio.wait_for(self.wake.wait(), t * self.k)
        except asyncio.TimeoutError:
            pass

    # ---- mission -------------------------------------------------------------------------------
    async def run(self):
        """Follow the route and drain queued announcements, then mark the agent done.

        Driver errors propagate; the sender task is cancelled on exit. Radio
        send errors are caught by the sender and passed to the log callback.
        """
        self.radio.set_handler(self.on_rx)
        sender = asyncio.create_task(self._sender())
        try:
            await self._mission()
            while self.q:
                await asyncio.sleep(0.05 * self.k)
            self.done = True
        finally:
            sender.cancel()

    async def _mission(self):
        await asyncio.sleep(self.rng.uniform(0, 1.0) * self.k)   # start-up jitter: robots rarely boot in the same instant
        self.announce(HELLO, ())
        await asyncio.sleep(LISTEN * self.k)
        while self.blocked_by((self.cur, self.cur)):
            await self._wait(0.1)
        nx = self._claims_from(0)
        self.announce(ARRIVED, [self.cur] + nx, len(nx), ARRIVED_REPEATS)
        while True:
            if self.retreating and self.idx >= len(self.route) - 1:
                self.route, self.idx, self.retreating, self.resume = self.resume, 0, False, None
                self.log("retreat done, resuming")
                nx = self._claims_from(0)
                self.announce(ARRIVED, [self.cur] + nx, len(nx), ARRIVED_REPEATS)
            if self.idx >= len(self.route) - 1:
                break
            edge = (self.cur, self.route[self.idx + 1])
            blockers = self.blocked_by(edge)
            if blockers:
                await self._blocked(blockers)
                continue
            self.blocked_since = None
            await self._drive(edge)
        self.announce(FINISHED, (), 0, ARRIVED_REPEATS)
        self.log("finished")

    async def _drive(self, edge):
        a, b = edge
        nx = self._claims_from(self.idx + 1)
        nodes = [a, b] + nx
        self.announce(ENTER, nodes, len(nx), ENTER_REPEATS)
        task = asyncio.create_task(self.driver.drive(a, b))
        while not task.done():
            await asyncio.wait({task}, timeout=0.05 * self.k)
            if not task.done() and time.monotonic() - self.last_tx >= BEACON_EVERY * self.k:
                self.announce(BEACON, nodes, len(nx))
        await task
        self.cur, self.idx = b, self.idx + 1
        await asyncio.sleep(TAIL * self.k)                  # keep the edge held while the tail clears
        nx = self._claims_from(self.idx)
        self.announce(ARRIVED, [self.cur] + nx, len(nx), ARRIVED_REPEATS)

    # ---- deadlocks -----------------------------------------------------------------------------
    async def _blocked(self, blockers):
        now = time.monotonic()
        if self.blocked_since is None:
            self.blocked_since = now
        stuck = now - self.blocked_since
        if not self.retreating and self.retreats < RETREAT_MAX:
            pair = any(j > self.rid and self.mutual(j) for j in blockers) and stuck >= STUCK_RETREAT * self.k
            if (pair or stuck >= self.forced_after) and self._start_retreat():
                return
        await self._wait(0.1)

    def _start_retreat(self):
        holds = [f.hold() for f in self.book.values() if f.kind != FINISHED and f.hold()]
        claims = [c for f in self.book.values() if f.kind != FINISHED for c in f.claims()]
        free_edge = lambda u, v: not any(self.g.conflict((u, v), h) for h in holds)
        free_node = lambda n: not any(self.g.conflict((n, n), h) for h in holds + claims)
        nxt = self.route[self.idx + 1]
        reach, queue = {self.cur}, [self.cur]
        for u in queue:                                     # nearest free side-step point
            for v in sorted(self.g.adj.get(u, ())):
                if v not in reach and v not in self.g.park and free_edge(u, v):
                    reach.add(v)
                    queue.append(v)
                    if v != nxt and free_node(v):
                        wp = self.g.path(self.cur, v, free_edge)
                        back = self.g.path(v, nxt)
                        if wp and back:
                            self.resume = back + self.route[self.idx + 2:]
                            self.route, self.idx, self.retreating = wp, 0, True
                            self.retreats += 1
                            self.blocked_since = None
                            self.forced_after = self.rng.uniform(*FORCED_RETREAT) * self.k
                            self.log(f"retreat {self.retreats}: via {wp}")
                            return True
        return False

